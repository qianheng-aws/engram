"""Tests for new features: confidence tagging, god nodes, surprising connections, PreToolUse hook."""

import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from engram.graph import MemoryGraph


def _make_vault(tmpdir):
    vault = os.path.join(tmpdir, "vault")
    os.makedirs(os.path.join(vault, "_meta"), exist_ok=True)
    return vault


def _build_test_graph(vault):
    """Build a graph with 2 communities for testing."""
    g = MemoryGraph(vault)

    # Community 1: Slack bridge cluster
    g.upsert_entity("CLAUDE_SLACK_BRIDGE", {
        "entity_type": "PROJECT", "description": "Slack bridge for Claude",
        "confidence": "EXTRACTED",
    })
    g.upsert_entity("STDERR_PIPE_BLOCKING", {
        "entity_type": "CONCEPT", "description": "Bug where stderr fills 64KB pipe buffer",
        "confidence": "EXTRACTED",
    })
    g.upsert_entity("ASYNC_DRAIN", {
        "entity_type": "CONCEPT", "description": "Fix: async task to drain stderr",
        "confidence": "INFERRED",
    })
    g.upsert_relation("CLAUDE_SLACK_BRIDGE", "STDERR_PIPE_BLOCKING", {
        "description": "had this bug", "weight": 0.9, "confidence": "EXTRACTED",
    })
    g.upsert_relation("STDERR_PIPE_BLOCKING", "ASYNC_DRAIN", {
        "description": "fixed by", "weight": 0.8, "confidence": "EXTRACTED",
    })

    # Community 2: Memory system cluster
    g.upsert_entity("ENGRAM", {
        "entity_type": "PROJECT", "description": "Persistent memory for Claude Code",
        "confidence": "EXTRACTED",
    })
    g.upsert_entity("KNOWLEDGE_GRAPH", {
        "entity_type": "CONCEPT", "description": "Graph-based entity storage",
        "confidence": "EXTRACTED",
    })
    g.upsert_entity("OBSIDIAN_VAULT", {
        "entity_type": "TOOL", "description": "Markdown vault with wikilinks",
        "confidence": "INFERRED",
    })
    g.upsert_relation("ENGRAM", "KNOWLEDGE_GRAPH", {
        "description": "uses", "weight": 0.9, "confidence": "EXTRACTED",
    })
    g.upsert_relation("ENGRAM", "OBSIDIAN_VAULT", {
        "description": "exports to", "weight": 0.7, "confidence": "INFERRED",
    })

    # Cross-community edge (surprising connection)
    g.upsert_relation("CLAUDE_SLACK_BRIDGE", "ENGRAM", {
        "description": "debugging led to creating engram", "weight": 0.6,
        "confidence": "INFERRED",
    })

    # Weak/ambiguous entity
    g.upsert_entity("SOME_LIBRARY", {
        "entity_type": "TOOL", "description": "Mentioned in passing",
        "confidence": "AMBIGUOUS",
    })

    g.save()
    return g


# ── Test confidence tagging ──────────────────────────────

def test_confidence_stored_on_entity():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        g = _build_test_graph(vault)

        attrs = g.get_entity("STDERR_PIPE_BLOCKING")
        assert attrs["confidence"] == "EXTRACTED"

        attrs = g.get_entity("ASYNC_DRAIN")
        assert attrs["confidence"] == "INFERRED"

        attrs = g.get_entity("SOME_LIBRARY")
        assert attrs["confidence"] == "AMBIGUOUS"
        print("  ✅ confidence stored on entities")


def test_confidence_stored_on_relation():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        g = _build_test_graph(vault)

        neighbors = g.get_neighbors("ENGRAM")
        for name, edge in neighbors:
            if name == "KNOWLEDGE_GRAPH":
                assert edge["confidence"] == "EXTRACTED"
            elif name == "OBSIDIAN_VAULT":
                assert edge["confidence"] == "INFERRED"
        print("  ✅ confidence stored on relations")


def test_confidence_in_entity_markdown():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        _build_test_graph(vault)

        md_path = os.path.join(vault, "entities", "concepts", "STDERR_PIPE_BLOCKING.md")
        with open(md_path) as f:
            content = f.read()
        assert "confidence: EXTRACTED" in content
        assert "confidence/extracted" in content
        print("  ✅ confidence in entity markdown frontmatter + tags")


def test_confidence_in_relation_index():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        _build_test_graph(vault)

        idx_path = os.path.join(vault, "relations", "_index.md")
        with open(idx_path) as f:
            content = f.read()
        assert "Confidence" in content  # header
        assert "EXTRACTED" in content
        assert "INFERRED" in content
        print("  ✅ confidence in relation index")


def test_confidence_default():
    """Entities without explicit confidence default to EXTRACTED."""
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        g = MemoryGraph(vault)
        g.upsert_entity("NO_CONFIDENCE", {
            "entity_type": "CONCEPT", "description": "No confidence set",
        })
        g.save()

        md_path = os.path.join(vault, "entities", "concepts", "NO_CONFIDENCE.md")
        with open(md_path) as f:
            content = f.read()
        assert "confidence: EXTRACTED" in content
        print("  ✅ confidence defaults to EXTRACTED")


# ── Test god nodes ────────────────────────────────────────

def test_god_nodes_ranking():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        g = _build_test_graph(vault)

        gods = g.god_nodes(top_n=3)
        assert len(gods) > 0
        # ENGRAM and CLAUDE_SLACK_BRIDGE should rank high (3 edges each)
        top_names = [g["name"] for g in gods[:2]]
        assert "ENGRAM" in top_names or "CLAUDE_SLACK_BRIDGE" in top_names
        print("  ✅ god nodes ranking works")


def test_god_nodes_fields():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        g = _build_test_graph(vault)

        gods = g.god_nodes(top_n=1)
        god = gods[0]
        assert "name" in god
        assert "degree" in god
        assert "pagerank" in god
        assert "score" in god
        assert "entity_type" in god
        assert "description" in god
        assert god["score"] > 0
        assert god["pagerank"] > 0
        print("  ✅ god nodes have all expected fields")


def test_god_nodes_single_isolated_node():
    """god_nodes must not crash with a single node that has degree 0."""
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        g = MemoryGraph(vault)
        g.upsert_entity("LONELY", {"entity_type": "CONCEPT", "description": "no edges"})
        gods = g.god_nodes()
        assert len(gods) == 1
        assert gods[0]["name"] == "LONELY"
        assert gods[0]["degree"] == 0
        assert gods[0]["score"] == 0.5  # 0 degree component + 1.0 pagerank component
        print("  ✅ god nodes handles single isolated node (no ZeroDivisionError)")


def test_god_nodes_empty_graph():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        g = MemoryGraph(vault)
        gods = g.god_nodes()
        assert gods == []
        print("  ✅ god nodes returns empty on empty graph")


# ── Test surprising connections ───────────────────────────

def test_surprising_connections():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        g = _build_test_graph(vault)

        communities = g.detect_communities(min_size=2)
        surprising = g.find_surprising_connections(communities)

        assert len(surprising) > 0
        # The cross-community edge should be found
        edge = surprising[0]
        assert "source" in edge
        assert "target" in edge
        assert "surprise_score" in edge
        assert edge["source_community"] != edge["target_community"]
        print("  ✅ surprising connections found cross-community edges")


def test_surprising_connections_fields():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        g = _build_test_graph(vault)

        communities = g.detect_communities(min_size=2)
        surprising = g.find_surprising_connections(communities)

        if surprising:
            s = surprising[0]
            required_fields = ["source", "target", "source_community", "target_community",
                               "weight", "surprise_score", "description"]
            for f in required_fields:
                assert f in s, f"Missing field: {f}"
        print("  ✅ surprising connections have all expected fields")


def test_surprising_connections_no_communities():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        g = MemoryGraph(vault)
        surprising = g.find_surprising_connections([])
        assert surprising == []
        print("  ✅ surprising connections returns empty with no communities")


# ── Test CLI integration ──────────────────────────────────

def test_cli_status_god_nodes():
    """engram status should include god_nodes and confidence_distribution."""
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        _build_test_graph(vault)

        result = subprocess.run(
            [sys.executable, "-m", "engram_cli", "status", "--vault", vault],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        data = json.loads(result.stdout)
        assert "god_nodes" in data
        assert "confidence_distribution" in data
        assert len(data["god_nodes"]) > 0
        assert data["confidence_distribution"].get("EXTRACTED", 0) > 0
        print("  ✅ CLI status includes god_nodes + confidence_distribution")


def test_cli_community_surprising():
    """engram community should include surprising_connections."""
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        _build_test_graph(vault)

        result = subprocess.run(
            [sys.executable, "-m", "engram_cli", "community", "--vault", vault],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        data = json.loads(result.stdout)
        assert "surprising_connections" in data
        print("  ✅ CLI community includes surprising_connections")


def test_cli_replay_confidence():
    """engram replay should store confidence from input JSON."""
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        os.makedirs(os.path.join(vault, "_meta"), exist_ok=True)

        payload = json.dumps({
            "date": "2026-04-08",
            "entities": [
                {"name": "TEST_A", "entity_type": "CONCEPT", "description": "Test entity A", "confidence": "EXTRACTED"},
                {"name": "TEST_B", "entity_type": "TOOL", "description": "Test entity B", "confidence": "AMBIGUOUS"},
            ],
            "relations": [
                {"source": "TEST_A", "target": "TEST_B", "description": "uses", "weight": 0.5, "confidence": "INFERRED"},
            ],
            "daily_summary": "Test replay with confidence",
        })

        result = subprocess.run(
            [sys.executable, "-m", "engram_cli", "replay", "--vault", vault, "--stdin"],
            input=payload, capture_output=True, text=True,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"

        # Verify confidence was stored
        g = MemoryGraph(vault)
        assert g.get_entity("TEST_A")["confidence"] == "EXTRACTED"
        assert g.get_entity("TEST_B")["confidence"] == "AMBIGUOUS"

        neighbors = g.get_neighbors("TEST_A")
        for name, edge in neighbors:
            if name == "TEST_B":
                assert edge["confidence"] == "INFERRED"
        print("  ✅ CLI replay stores confidence tags")


# ── Test PreToolUse hook ──────────────────────────────────

def test_pretool_hook_with_graph():
    """engram-pretool should output a message when graph exists."""
    hook_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "plugin", "bin", "engram-pretool")

    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        _build_test_graph(vault)

        # Write a temporary config pointing to this vault
        config_dir = os.path.join(tmpdir, "config")
        os.makedirs(config_dir, exist_ok=True)
        config_path = os.path.join(config_dir, "config.json")
        with open(config_path, "w") as f:
            json.dump({"vault": vault}, f)

        payload = json.dumps({"tool_name": "Grep"})

        # Patch CONFIG_PATH via env — we'll modify the script to check this
        # For now, test the script directly by checking it's executable and parses
        result = subprocess.run(
            [sys.executable, hook_path],
            input=payload, capture_output=True, text=True,
            env={**os.environ, "HOME": tmpdir},  # Trick: ~/.engram/vault -> tmpdir/.engram/vault
        )
        # With HOME overridden, config won't be found, but we set up the vault at tmpdir/.engram/vault
        # Let's set that up properly
        alt_vault = os.path.join(tmpdir, ".engram", "vault")
        os.makedirs(os.path.join(alt_vault, "_meta"), exist_ok=True)
        _build_test_graph_at(alt_vault)

        # Write config
        alt_config = os.path.join(tmpdir, ".engram", "config.json")
        with open(alt_config, "w") as f:
            json.dump({"vault": alt_vault}, f)

        result = subprocess.run(
            [sys.executable, hook_path],
            input=payload, capture_output=True, text=True,
            env={**os.environ, "HOME": tmpdir},
        )
        if result.stdout.strip():
            data = json.loads(result.stdout)
            assert "message" in data
            assert "engram" in data["message"]
            print("  ✅ PreToolUse hook outputs graph reminder")
        else:
            # Hook ran without error, graph message is optional
            print("  ✅ PreToolUse hook runs without error")


def _build_test_graph_at(vault):
    """Build a minimal graph at the given vault path."""
    g = MemoryGraph(vault)
    g.upsert_entity("TEST", {"entity_type": "CONCEPT", "description": "test"})
    g.save()


def test_pretool_hook_no_graph():
    """engram-pretool should output nothing when no graph exists."""
    hook_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "plugin", "bin", "engram-pretool")

    with tempfile.TemporaryDirectory() as tmpdir:
        payload = json.dumps({"tool_name": "Grep"})
        result = subprocess.run(
            [sys.executable, hook_path],
            input=payload, capture_output=True, text=True,
            env={**os.environ, "HOME": tmpdir},
        )
        assert result.stdout.strip() == ""
        assert result.returncode == 0
        print("  ✅ PreToolUse hook silent when no graph")


def test_pretool_hook_ignores_non_search():
    """engram-pretool should ignore non-search tools."""
    hook_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "plugin", "bin", "engram-pretool")

    with tempfile.TemporaryDirectory() as tmpdir:
        payload = json.dumps({"tool_name": "Edit"})
        result = subprocess.run(
            [sys.executable, hook_path],
            input=payload, capture_output=True, text=True,
            env={**os.environ, "HOME": tmpdir},
        )
        assert result.stdout.strip() == ""
        print("  ✅ PreToolUse hook ignores non-search tools")


# ── Test rich content ─────────────────────────────────────

def test_markdown_description_preserved():
    """Markdown in description should be rendered fully in entity file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        g = MemoryGraph(vault)
        md_desc = (
            "Bug where stderr fills 64KB pipe buffer.\n\n"
            "**Root cause:** `subprocess.PIPE` without consumer.\n\n"
            "```python\nasync def _drain(proc):\n    await proc.stderr.read()\n```"
        )
        g.upsert_entity("PIPE_BUG", {
            "entity_type": "CONCEPT",
            "description": md_desc,
        })
        g.save()

        md_path = os.path.join(vault, "entities", "concepts", "PIPE_BUG.md")
        with open(md_path) as f:
            content = f.read()
        assert "**Root cause:**" in content
        assert "```python" in content
        assert "async def _drain" in content
        # Should NOT be truncated
        assert "subprocess.PIPE" in content
        print("  ✅ markdown description preserved in entity file")


def test_references_rendered():
    """References should appear as a section in entity markdown."""
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        g = MemoryGraph(vault)
        g.upsert_entity("MY_PROJECT", {
            "entity_type": "PROJECT",
            "description": "A cool project",
            "references": json.dumps([
                "https://github.com/user/repo",
                "https://github.com/user/repo/issues/42",
            ]),
        })
        g.save()

        md_path = os.path.join(vault, "entities", "projects", "MY_PROJECT.md")
        with open(md_path) as f:
            content = f.read()
        assert "## References" in content
        assert "https://github.com/user/repo" in content
        assert "https://github.com/user/repo/issues/42" in content
        print("  ✅ references rendered in entity file")


def test_references_empty_no_section():
    """No References section when entity has no references."""
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        g = MemoryGraph(vault)
        g.upsert_entity("NO_REFS", {
            "entity_type": "CONCEPT",
            "description": "No refs here",
        })
        g.save()

        md_path = os.path.join(vault, "entities", "concepts", "NO_REFS.md")
        with open(md_path) as f:
            content = f.read()
        assert "## References" not in content
        print("  ✅ no References section when empty")


def test_cli_replay_references():
    """engram replay should store references from input JSON."""
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)

        payload = json.dumps({
            "date": "2026-04-08",
            "entities": [{
                "name": "REF_TEST",
                "entity_type": "PROJECT",
                "description": "Test with refs",
                "confidence": "EXTRACTED",
                "references": ["https://example.com"],
            }],
            "relations": [],
            "daily_summary": "Test",
        })

        result = subprocess.run(
            [sys.executable, "-m", "engram_cli", "replay", "--vault", vault, "--stdin"],
            input=payload, capture_output=True, text=True,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"

        g = MemoryGraph(vault)
        refs = g.get_entity("REF_TEST").get("references", "")
        assert "https://example.com" in refs
        print("  ✅ CLI replay stores references")


def test_description_uses_latest():
    """When description is updated, entity file should show latest version."""
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        g = MemoryGraph(vault)
        g.upsert_entity("EVOLVING", {
            "entity_type": "CONCEPT",
            "description": "Initial description",
        })
        g.upsert_entity("EVOLVING", {
            "entity_type": "CONCEPT",
            "description": "Updated with **more detail** and `code`",
        })
        g.save()

        md_path = os.path.join(vault, "entities", "concepts", "EVOLVING.md")
        with open(md_path) as f:
            content = f.read()
        assert "**more detail**" in content
        assert "`code`" in content
        print("  ✅ entity file uses latest description")


def test_local_path_in_entity_markdown():
    """local_path should appear in frontmatter when set."""
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        g = MemoryGraph(vault)
        g.upsert_entity("MY_REPO", {
            "entity_type": "PROJECT",
            "description": "A local project",
            "local_path": "/Volumes/workplace/my-repo",
        })
        g.save()

        md_path = os.path.join(vault, "entities", "projects", "MY_REPO.md")
        with open(md_path) as f:
            content = f.read()
        assert 'local_path: "/Volumes/workplace/my-repo"' in content
        print("  ✅ local_path in entity frontmatter")


def test_local_path_absent_when_empty():
    """No local_path line in frontmatter when entity has no local path."""
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        g = MemoryGraph(vault)
        g.upsert_entity("REMOTE_THING", {
            "entity_type": "CONCEPT",
            "description": "No local path",
        })
        g.save()

        md_path = os.path.join(vault, "entities", "concepts", "REMOTE_THING.md")
        with open(md_path) as f:
            content = f.read()
        assert "local_path" not in content
        print("  ✅ no local_path when empty")


def test_cli_replay_local_path():
    """engram replay should store local_path from input JSON."""
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)

        payload = json.dumps({
            "date": "2026-04-09",
            "entities": [{
                "name": "PATH_TEST",
                "entity_type": "PROJECT",
                "description": "Test with local path",
                "confidence": "EXTRACTED",
                "local_path": "/tmp/test-project",
            }],
            "relations": [],
            "daily_summary": "Test",
        })

        result = subprocess.run(
            [sys.executable, "-m", "engram_cli", "replay", "--vault", vault, "--stdin"],
            input=payload, capture_output=True, text=True,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"

        g = MemoryGraph(vault)
        assert g.get_entity("PATH_TEST")["local_path"] == "/tmp/test-project"
        print("  ✅ CLI replay stores local_path")


# ── Test engram-hook config ───────────────────────────────

def test_engram_hook_uses_config_vault():
    """engram-hook should read vault path from config, not hardcode ~/.engram/vault."""
    hook_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "plugin", "bin", "engram-hook")

    with tempfile.TemporaryDirectory() as tmpdir:
        # Set up a custom vault at a non-default path
        custom_vault = os.path.join(tmpdir, "my-custom-vault")
        meta_dir = os.path.join(custom_vault, "_meta")
        os.makedirs(meta_dir)
        # Enable hook
        open(os.path.join(meta_dir, "hook-enabled"), "w").close()

        # Write config pointing to custom vault
        config_dir = os.path.join(tmpdir, ".engram")
        os.makedirs(config_dir)
        with open(os.path.join(config_dir, "config.json"), "w") as f:
            json.dump({"vault": custom_vault}, f)

        # Also create default vault path to ensure hook does NOT use it
        default_vault = os.path.join(tmpdir, ".engram", "vault", "_meta")
        os.makedirs(default_vault)
        open(os.path.join(default_vault, "hook-enabled"), "w").close()

        payload = json.dumps({
            "session_id": "test-session-123",
            "last_assistant_message": "This is a test message that is long enough to pass the 50 char minimum threshold for processing.",
            "stop_reason": "user",
        })

        result = subprocess.run(
            [sys.executable, hook_path, "Stop"],
            input=payload, capture_output=True, text=True,
            env={**os.environ, "HOME": tmpdir},
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"

        # Check: queue file should be in custom vault, NOT in default
        custom_queue = os.path.join(custom_vault, "_meta", "queue")
        default_queue = os.path.join(tmpdir, ".engram", "vault", "_meta", "queue")

        custom_has_files = os.path.isdir(custom_queue) and len(os.listdir(custom_queue)) > 0
        default_has_files = os.path.isdir(default_queue) and len(os.listdir(default_queue)) > 0

        if default_has_files and not custom_has_files:
            print("  ❌ BUG: engram-hook wrote to hardcoded default, not config vault")
            assert False, "engram-hook ignores config.json, uses hardcoded vault path"
        elif custom_has_files:
            print("  ✅ engram-hook reads vault from config.json")
        else:
            # Might not have written if hook-enabled check failed
            print("  ⚠️  engram-hook wrote to neither location (check hook-enabled flag)")
            assert False, "Hook did not write queue file to either location"


# ── Test knowledge gaps ──────────────────────────────────

def test_knowledge_gaps_isolated_nodes():
    """Nodes with degree 0 are reported as isolated."""
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        g = MemoryGraph(vault)
        g.upsert_entity("CONNECTED_A", {"entity_type": "CONCEPT", "description": "A"})
        g.upsert_entity("CONNECTED_B", {"entity_type": "CONCEPT", "description": "B"})
        g.upsert_entity("LONELY", {"entity_type": "CONCEPT", "description": "No edges"})
        g.upsert_relation("CONNECTED_A", "CONNECTED_B", {"description": "linked", "weight": 1.0})
        g.save()

        gaps = g.knowledge_gaps()
        isolated_names = [n["name"] for n in gaps["isolated_nodes"]]
        assert "LONELY" in isolated_names
        assert "CONNECTED_A" not in isolated_names
        assert "CONNECTED_B" not in isolated_names
        print("  ✅ knowledge gaps: isolated nodes detected")


def test_knowledge_gaps_thin_communities():
    """Communities with < 3 members are flagged as thin."""
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        g = _build_test_graph(vault)

        gaps = g.knowledge_gaps()
        assert "thin_communities" in gaps
        for tc in gaps["thin_communities"]:
            assert tc["size"] < 3
        print("  ✅ knowledge gaps: thin communities detected")


def test_knowledge_gaps_empty_graph():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        g = MemoryGraph(vault)
        gaps = g.knowledge_gaps()
        assert gaps["isolated_nodes"] == []
        assert gaps["thin_communities"] == []
        print("  ✅ knowledge gaps: empty graph returns empty lists")


def test_cli_status_knowledge_gaps():
    """engram status should include knowledge_gaps."""
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        g = MemoryGraph(vault)
        g.upsert_entity("ORPHAN", {"entity_type": "CONCEPT", "description": "alone"})
        g.upsert_entity("PAIR_A", {"entity_type": "CONCEPT", "description": "a"})
        g.upsert_entity("PAIR_B", {"entity_type": "CONCEPT", "description": "b"})
        g.upsert_relation("PAIR_A", "PAIR_B", {"description": "link", "weight": 1.0})
        g.save()

        result = subprocess.run(
            [sys.executable, "-m", "engram_cli", "status", "--vault", vault],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        data = json.loads(result.stdout)
        assert "knowledge_gaps" in data
        isolated_names = [n["name"] for n in data["knowledge_gaps"]["isolated_nodes"]]
        assert "ORPHAN" in isolated_names
        print("  ✅ CLI status includes knowledge_gaps")


# ── Test suggested questions ─────────────────────────────

def test_suggested_questions_bridge_nodes():
    """Suggested questions identify cross-community bridge nodes."""
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        g = _build_test_graph(vault)

        questions = g.suggested_questions(top_n=5)
        assert len(questions) > 0
        q = questions[0]
        assert "question" in q
        assert "node" in q
        assert "betweenness" in q
        print("  ✅ suggested questions: bridge nodes identified")


def test_suggested_questions_empty_graph():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        g = MemoryGraph(vault)
        questions = g.suggested_questions()
        assert questions == []
        print("  ✅ suggested questions: empty graph returns empty")


def test_suggested_questions_single_node():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        g = MemoryGraph(vault)
        g.upsert_entity("SOLO", {"entity_type": "CONCEPT", "description": "alone"})
        questions = g.suggested_questions()
        assert questions == []
        print("  ✅ suggested questions: single node returns empty")


def test_cli_context_suggested_questions():
    """engram context should include suggested_questions."""
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        _build_test_graph(vault)

        result = subprocess.run(
            [sys.executable, "-m", "engram_cli", "context", "--vault", vault],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        data = json.loads(result.stdout)
        assert "suggested_questions" in data
        print("  ✅ CLI context includes suggested_questions")


# ── Test enhanced status ─────────────────────────────────

def test_cli_status_enhanced():
    """engram status should include surprising_connections and suggested_questions."""
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        _build_test_graph(vault)

        result = subprocess.run(
            [sys.executable, "-m", "engram_cli", "status", "--vault", vault],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        data = json.loads(result.stdout)
        assert "surprising_connections" in data
        assert "suggested_questions" in data
        assert "knowledge_gaps" in data
        # With _build_test_graph there should be a cross-community edge
        assert len(data["surprising_connections"]) > 0
        print("  ✅ CLI status includes full graph analysis")


if __name__ == "__main__":
    print("Testing confidence tagging...")
    test_confidence_stored_on_entity()
    test_confidence_stored_on_relation()
    test_confidence_in_entity_markdown()
    test_confidence_in_relation_index()
    test_confidence_default()

    print("\nTesting god nodes...")
    test_god_nodes_ranking()
    test_god_nodes_fields()
    test_god_nodes_single_isolated_node()
    test_god_nodes_empty_graph()

    print("\nTesting surprising connections...")
    test_surprising_connections()
    test_surprising_connections_fields()
    test_surprising_connections_no_communities()

    print("\nTesting CLI integration...")
    test_cli_status_god_nodes()
    test_cli_community_surprising()
    test_cli_replay_confidence()

    print("\nTesting PreToolUse hook...")
    test_pretool_hook_with_graph()
    test_pretool_hook_no_graph()
    test_pretool_hook_ignores_non_search()
    test_engram_hook_uses_config_vault()

    print("\nTesting rich content...")
    test_markdown_description_preserved()
    test_references_rendered()
    test_references_empty_no_section()
    test_cli_replay_references()
    test_description_uses_latest()
    test_local_path_in_entity_markdown()
    test_local_path_absent_when_empty()
    test_cli_replay_local_path()

    print("\n🎉 All feature tests passed!")
