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

    print("\n🎉 All CLI e2e tests passed!")
