"""End-to-end tests for core CLI commands: replay, integrate, prune, community, abstract, save-pattern, query, status."""

import json
import os
import subprocess
import sys
import tempfile

CLI = [sys.executable, "-m", "engram_cli"]


def _run(cmd_args, vault, stdin_data=None):
    """Run engram CLI command and return parsed JSON output."""
    result = subprocess.run(
        CLI + cmd_args + ["--vault", vault],
        input=stdin_data, capture_output=True, text=True,
    )
    assert result.returncode == 0, f"Command {cmd_args} failed:\nstderr: {result.stderr}\nstdout: {result.stdout}"
    return json.loads(result.stdout) if result.stdout.strip() else {}


def _make_vault(tmpdir):
    vault = os.path.join(tmpdir, "vault")
    os.makedirs(os.path.join(vault, "_meta"), exist_ok=True)
    return vault


def _seed_replay(vault):
    """Seed vault with test data via replay."""
    payload = json.dumps({
        "date": "2026-04-08",
        "entities": [
            {"name": "PROJECT_A", "entity_type": "PROJECT", "description": "Main project", "confidence": "EXTRACTED"},
            {"name": "BUG_X", "entity_type": "CONCEPT", "description": "A nasty bug", "confidence": "EXTRACTED"},
            {"name": "TOOL_Y", "entity_type": "TOOL", "description": "Helper tool", "confidence": "INFERRED"},
            {"name": "PROJECT_B", "entity_type": "PROJECT", "description": "Related project", "confidence": "EXTRACTED"},
        ],
        "relations": [
            {"source": "PROJECT_A", "target": "BUG_X", "description": "had this bug", "weight": 0.9, "confidence": "EXTRACTED"},
            {"source": "PROJECT_A", "target": "TOOL_Y", "description": "uses", "weight": 0.6, "confidence": "INFERRED"},
            {"source": "PROJECT_B", "target": "TOOL_Y", "description": "also uses", "weight": 0.5, "confidence": "EXTRACTED"},
        ],
        "daily_summary": "Worked on Project A, found Bug X, used Tool Y.",
    })
    return _run(["replay", "--stdin"], vault, stdin_data=payload)


# ── replay ────────────────────────────────────────────────

def test_replay_basic():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        result = _seed_replay(vault)
        assert result["status"] == "ok"
        assert result["entities_added"] == 4
        assert result["relations_added"] == 3
        assert result["total_nodes"] == 4
        assert result["total_edges"] == 3
        print("  ✅ replay: basic entity/relation creation")


def test_replay_daily_note():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        result = _seed_replay(vault)
        daily_path = result["daily_note"]
        assert os.path.exists(daily_path)
        with open(daily_path) as f:
            content = f.read()
        assert "PROJECT_A" in content
        assert "## Summary" in content
        print("  ✅ replay: daily note created with entities and summary")


def test_replay_dedup():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        _seed_replay(vault)
        result = _seed_replay(vault)  # exact same payload again
        assert result["status"] == "skipped"
        assert "duplicate" in result["reason"]
        print("  ✅ replay: dedup blocks identical replay within window")


def test_replay_queue_cleanup():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        queue_dir = os.path.join(vault, "_meta", "queue")
        os.makedirs(queue_dir)
        for i in range(3):
            with open(os.path.join(queue_dir, f"q-{i}.json"), "w") as f:
                json.dump({"status": "pending", "session_id": f"s-{i}"}, f)

        _seed_replay(vault)
        remaining = [f for f in os.listdir(queue_dir) if f.endswith(".json")]
        assert len(remaining) == 0, f"Expected 0 queue files, got {len(remaining)}"
        print("  ✅ replay: pending queue files deleted after replay")


# ── status ────────────────────────────────────────────────

def test_status_basic():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        _seed_replay(vault)
        result = _run(["status"], vault)
        assert result["nodes"] == 4
        assert result["edges"] == 3
        assert "god_nodes" in result
        assert "confidence_distribution" in result
        assert "type_distribution" in result
        assert result["daily_notes"] == 1
        assert "PROJECT_A" in result["entities"]
        print("  ✅ status: returns correct counts and fields")


def test_status_empty_vault():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        result = _run(["status"], vault)
        assert result["nodes"] == 0
        assert result["edges"] == 0
        assert result["god_nodes"] == []
        print("  ✅ status: handles empty vault")


# ── integrate ─────────────────────────────────────────────

def test_integrate_detect():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        _seed_replay(vault)
        result = _run(["integrate"], vault)
        assert result["status"] == "ok"
        assert "duplicate_candidates" in result
        assert result["total_entities"] == 4
        print("  ✅ integrate: detect mode returns candidates")


def test_integrate_merge():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        # Create two entities to merge
        payload = json.dumps({
            "date": "2026-04-08",
            "entities": [
                {"name": "REACT_APP", "entity_type": "PROJECT", "description": "React frontend"},
                {"name": "REACT_APPLICATION", "entity_type": "PROJECT", "description": "Same React frontend, different name"},
            ],
            "relations": [
                {"source": "REACT_APP", "target": "REACT_APPLICATION", "description": "same thing", "weight": 0.5},
            ],
            "daily_summary": "test",
        })
        _run(["replay", "--stdin"], vault, stdin_data=payload)

        merge_payload = json.dumps({"merges": [{"canonical": "REACT_APP", "aliases": ["REACT_APPLICATION"]}]})
        result = _run(["integrate", "--stdin"], vault, stdin_data=merge_payload)
        assert result["status"] == "ok"
        assert "REACT_APPLICATION → REACT_APP" in result["merged"]

        # Verify merged
        status = _run(["status"], vault)
        assert "REACT_APPLICATION" not in status["entities"]
        assert "REACT_APP" in status["entities"]
        print("  ✅ integrate: merge removes alias, keeps canonical")


# ── prune ─────────────────────────────────────────────────

def test_prune_report():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        _seed_replay(vault)
        result = _run(["prune"], vault)
        assert result["status"] == "ok"
        assert "fading" in result
        assert "archivable" in result
        assert result["total_entities"] == 4
        print("  ✅ prune: report mode returns decay scores")


def test_prune_archive():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        _seed_replay(vault)

        archive_payload = json.dumps({"archive": ["TOOL_Y"]})
        result = _run(["prune", "--stdin"], vault, stdin_data=archive_payload)
        assert result["status"] == "ok"
        assert "TOOL_Y" in result["archived"]

        # Verify archived
        status = _run(["status"], vault)
        assert "TOOL_Y" not in status["entities"]
        assert status["nodes"] == 3

        # Check archive file exists
        archive_path = os.path.join(vault, "_meta", "archive", "TOOL_Y.json")
        assert os.path.exists(archive_path)
        print("  ✅ prune: archive removes entity and saves to archive dir")


# ── community ─────────────────────────────────────────────

def test_community_detect():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        _seed_replay(vault)
        result = _run(["community"], vault)
        assert result["status"] == "ok"
        assert "communities" in result
        assert "surprising_connections" in result
        print("  ✅ community: detect mode returns communities and surprising connections")


def test_community_save():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        _seed_replay(vault)

        save_payload = json.dumps({"communities": [{
            "id": 0,
            "title": "Test Community",
            "summary": "A test community summary.",
            "members": ["PROJECT_A", "BUG_X"],
        }]})
        result = _run(["community", "--stdin"], vault, stdin_data=save_payload)
        assert result["status"] == "ok"
        assert len(result["saved"]) == 1

        comm_path = result["saved"][0]["path"]
        assert os.path.exists(comm_path)
        with open(comm_path) as f:
            content = f.read()
        assert "Test Community" in content
        assert "[[PROJECT_A]]" in content
        print("  ✅ community: save creates markdown with members")


# ── abstract + save-pattern ───────────────────────────────

def test_abstract():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        _seed_replay(vault)
        result = _run(["abstract"], vault)
        assert "daily_notes" in result
        assert "existing_patterns" in result
        assert "2026-04-08" in result["daily_notes"]
        print("  ✅ abstract: returns daily notes and existing patterns")


def test_save_pattern():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        pattern_payload = json.dumps({
            "new_patterns": [{
                "name": "Test Pattern",
                "description": "A recurring behavior.",
                "evidence": ["2026-04-08"],
                "confidence": 0.8,
            }],
            "updated_patterns": [],
        })
        result = _run(["save-pattern", "--stdin"], vault, stdin_data=pattern_payload)
        assert result["status"] == "ok"
        assert "Test Pattern" in result["patterns_saved"]

        pattern_path = os.path.join(vault, "patterns", "test-pattern.md")
        assert os.path.exists(pattern_path)
        with open(pattern_path) as f:
            content = f.read()
        assert "confidence/high" in content
        assert "[[daily/2026-04-08|2026-04-08]]" in content
        print("  ✅ save-pattern: creates pattern file with confidence tag and daily links")


def test_save_pattern_skips_low_confidence():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        pattern_payload = json.dumps({
            "new_patterns": [{
                "name": "Weak Pattern",
                "description": "Not confident enough.",
                "evidence": ["2026-04-08"],
                "confidence": 0.3,
            }],
            "updated_patterns": [],
        })
        result = _run(["save-pattern", "--stdin"], vault, stdin_data=pattern_payload)
        assert result["status"] == "ok"
        assert "Weak Pattern" not in result["patterns_saved"]
        print("  ✅ save-pattern: skips patterns with confidence < 0.5")


# ── query ─────────────────────────────────────────────────

def test_query_keyword_match():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        _seed_replay(vault)
        result = _run(["query", "--question", "bug"], vault)
        assert "BUG_X" in result["matched_entities"]
        assert len(result["context"]) > 0
        print("  ✅ query: keyword match finds entity")


def test_query_description_match():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        _seed_replay(vault)
        result = _run(["query", "--question", "nasty"], vault)
        assert "BUG_X" in result["matched_entities"]
        print("  ✅ query: description match finds entity")


def test_query_no_match():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        _seed_replay(vault)
        result = _run(["query", "--question", "nonexistent thing"], vault)
        assert len(result["matched_entities"]) == 0
        print("  ✅ query: returns empty for no match")


# ── lint ──────────────────────────────────────────────────

def test_lint_clean():
    """Lint on a healthy vault should report 0 issues."""
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        _seed_replay(vault)
        result = _run(["lint"], vault)
        assert result["status"] == "ok"
        assert result["checks"] == 4
        # All entities are connected and have markdown — only check no missing_markdown/orphan_markdown
        assert len(result["details"]["missing_markdown"]) == 0
        assert len(result["details"]["orphan_markdown"]) == 0
        assert len(result["details"]["incomplete_frontmatter"]) == 0
        print("  ✅ lint: clean vault passes")


def test_lint_orphan_node():
    """Lint detects degree-0 nodes not created today."""
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        _seed_replay(vault)
        # Add an isolated entity with old date
        from engram.graph import MemoryGraph
        graph = MemoryGraph(vault)
        graph.upsert_entity("ORPHAN_Z", {
            "entity_type": "CONCEPT",
            "description": "Isolated entity",
            "confidence": "INFERRED",
            "source_id": "session-2026-01-01",
            "last_updated": "2026-01-01",
        })
        graph.save()
        result = _run(["lint"], vault)
        orphans = result["details"]["orphan_nodes"]
        orphan_names = [o["name"] for o in orphans]
        assert "ORPHAN_Z" in orphan_names
        print("  ✅ lint: detects orphan nodes")


def test_lint_orphan_markdown():
    """Lint detects markdown files not backed by graph nodes."""
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        _seed_replay(vault)
        # Create a stray markdown file
        stray_dir = os.path.join(vault, "entities", "concepts")
        os.makedirs(stray_dir, exist_ok=True)
        with open(os.path.join(stray_dir, "GHOST_ENTITY.md"), "w") as f:
            f.write("---\nentity_type: CONCEPT\n---\n# GHOST\n")
        result = _run(["lint"], vault)
        orphan_paths = [o["path"] for o in result["details"]["orphan_markdown"]]
        assert any("GHOST_ENTITY" in p for p in orphan_paths)
        print("  ✅ lint: detects orphan markdown files")


def test_lint_dead_wikilinks():
    """Lint detects wikilinks to non-existent entities."""
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        _seed_replay(vault)
        # Inject a dead wikilink into an entity file
        entity_dir = os.path.join(vault, "entities", "projects")
        for fname in os.listdir(entity_dir):
            if fname.startswith("PROJECT_A"):
                fpath = os.path.join(entity_dir, fname)
                with open(fpath, "a") as f:
                    f.write("\nSee also [[NONEXISTENT_ENTITY]] for details.\n")
                break
        result = _run(["lint"], vault)
        dead = result["details"]["dead_wikilinks"]
        dead_targets = [d["target"] for d in dead]
        assert "NONEXISTENT_ENTITY" in dead_targets
        print("  ✅ lint: detects dead wikilinks")


def test_lint_incomplete_frontmatter():
    """Lint detects entity files with missing required frontmatter fields."""
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        _seed_replay(vault)
        # Overwrite an entity file with incomplete frontmatter
        broken_dir = os.path.join(vault, "entities", "concepts")
        os.makedirs(broken_dir, exist_ok=True)
        with open(os.path.join(broken_dir, "BUG_X.md"), "w") as f:
            f.write("---\nentity_type: CONCEPT\n---\n# BUG_X\nMissing confidence, tags, created.\n")
        result = _run(["lint"], vault)
        incomplete = result["details"]["incomplete_frontmatter"]
        broken_files = [i["file"] for i in incomplete]
        assert any("BUG_X" in f for f in broken_files)
        # Should be missing confidence, tags, created
        for item in incomplete:
            if "BUG_X" in item["file"]:
                assert "confidence" in item["missing"]
                assert "tags" in item["missing"]
                assert "created" in item["missing"]
                break
        print("  ✅ lint: detects incomplete frontmatter")


# ── context ───────────────────────────────────────────────

def test_context_json_output():
    """context without flag prints JSON to stdout."""
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        _seed_replay(vault)
        result = _run(["context"], vault)
        assert "context" in result
        assert "entity_count" in result
        assert "edge_count" in result
        assert result["entity_count"] == 4
        print("  ✅ context: JSON output unchanged")


def test_context_write_cache_seeded():
    """context --write-cache creates cache file with entity names."""
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        _seed_replay(vault)

        # Run with --write-cache
        result = subprocess.run(
            CLI + ["context", "--write-cache", "--vault", vault],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, f"Command failed:\nstderr: {result.stderr}\nstdout: {result.stdout}"
        output = json.loads(result.stdout)

        # Verify confirmation JSON
        assert "written" in output
        assert "bytes" in output
        assert "_meta/context-cache.md" in output["written"]

        # Verify cache file exists and contains expected content
        cache_path = os.path.join(vault, "_meta", "context-cache.md")
        assert os.path.exists(cache_path)

        with open(cache_path) as f:
            content = f.read()

        # Must contain a god-node name from seed data
        assert "PROJECT_A" in content or "BUG_X" in content or "TOOL_Y" in content or "PROJECT_B" in content
        assert "## Key Entities" in content  # Should have the section header
        print("  ✅ context --write-cache: creates cache with entity names")


def test_context_write_cache_empty_vault():
    """context --write-cache on empty vault creates empty/header-only file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)

        # Run with --write-cache on empty vault
        result = subprocess.run(
            CLI + ["context", "--write-cache", "--vault", vault],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, f"Command failed:\nstderr: {result.stderr}\nstdout: {result.stdout}"
        output = json.loads(result.stdout)

        # Verify confirmation JSON
        assert "written" in output
        assert "bytes" in output

        # Verify cache file exists (empty or header-only is fine)
        cache_path = os.path.join(vault, "_meta", "context-cache.md")
        assert os.path.exists(cache_path)
        print("  ✅ context --write-cache: empty vault creates file without crash")


def test_entity_index_seeded():
    """context --write-cache creates entity-index.json with keywords and snippets."""
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        _seed_replay(vault)

        # Run with --write-cache (should create both cache and index)
        result = subprocess.run(
            CLI + ["context", "--write-cache", "--vault", vault],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"

        # Verify entity-index.json exists
        index_path = os.path.join(vault, "_meta", "entity-index.json")
        assert os.path.exists(index_path), "entity-index.json should be created"

        with open(index_path) as f:
            index_data = json.load(f)

        # Check schema
        assert index_data.get("version") == 2
        assert "entities" in index_data
        entities = index_data["entities"]

        # Should have at least one entity from seed data
        assert len(entities) > 0, "Should have entities from seed"

        # Check structure of first entity
        entity_name = next(iter(entities))
        entity = entities[entity_name]
        assert "keywords" in entity
        assert "snippet" in entity
        assert "local_path" in entity
        assert isinstance(entity["keywords"], list)
        assert isinstance(entity["snippet"], str)

        # Keywords should be lowercase, ≥ 3 chars
        for kw in entity["keywords"]:
            assert kw.islower(), f"Keyword {kw} should be lowercase"
            assert len(kw) >= 3, f"Keyword {kw} should be ≥ 3 chars"

        # Snippet should be compact (≤ 300 chars)
        assert len(entity["snippet"]) <= 300, f"Snippet too long: {len(entity['snippet'])} chars"

        # Snippet should start with entity name
        assert entity_name in entity["snippet"], f"Snippet should contain {entity_name}"

        print("  ✅ entity-index.json: created with keywords and snippets")


def test_entity_index_empty_vault():
    """Empty vault creates valid empty entity-index.json."""
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)

        result = subprocess.run(
            CLI + ["context", "--write-cache", "--vault", vault],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"

        index_path = os.path.join(vault, "_meta", "entity-index.json")
        assert os.path.exists(index_path)

        with open(index_path) as f:
            index_data = json.load(f)

        assert index_data == {"version": 2, "entities": {}}
        print("  ✅ entity-index.json: empty vault creates valid empty index")


def test_entity_index_includes_cjk_terms_and_local_path():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        local_path = os.path.join(tmpdir, "engram")
        payload = json.dumps({
            "date": "2026-08-24",
            "entities": [{
                "name": "MEMORY_INJECTION", "entity_type": "CONCEPT",
                "description": "自动记忆注入性能优化", "confidence": "EXTRACTED",
                "local_path": local_path,
            }],
            "relations": [], "daily_summary": "seed",
        })
        _run(["replay", "--stdin"], vault, stdin_data=payload)
        _run(["context", "--write-cache"], vault)
        with open(os.path.join(vault, "_meta", "entity-index.json")) as f:
            entity = json.load(f)["entities"]["MEMORY_INJECTION"]
        assert "记忆" in entity["keywords"]
        assert "记忆注入" in entity["keywords"]
        assert entity["local_path"] == local_path


if __name__ == "__main__":
    print("Testing replay...")
    test_replay_basic()
    test_replay_daily_note()
    test_replay_dedup()
    test_replay_queue_cleanup()

    print("\nTesting status...")
    test_status_basic()
    test_status_empty_vault()

    print("\nTesting integrate...")
    test_integrate_detect()
    test_integrate_merge()

    print("\nTesting prune...")
    test_prune_report()
    test_prune_archive()

    print("\nTesting community...")
    test_community_detect()
    test_community_save()

    print("\nTesting abstract + save-pattern...")
    test_abstract()
    test_save_pattern()
    test_save_pattern_skips_low_confidence()

    print("\nTesting query...")
    test_query_keyword_match()
    test_query_description_match()
    test_query_no_match()

    print("\nTesting lint...")
    test_lint_clean()
    test_lint_orphan_node()
    test_lint_orphan_markdown()
    test_lint_dead_wikilinks()
    test_lint_incomplete_frontmatter()

    print("\nTesting context...")
    test_context_json_output()
    test_context_write_cache_seeded()
    test_context_write_cache_empty_vault()

    print("\nTesting entity-index...")
    test_entity_index_seeded()
    test_entity_index_empty_vault()

    print("\n🎉 All CLI e2e tests passed!")
