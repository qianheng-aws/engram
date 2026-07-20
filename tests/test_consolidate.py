"""E2E tests for `engram consolidate` — headless full consolidation.

All tests stub the claude binary via the `worker_claude_bin` config key.
No real LLM calls are ever made.
"""

import json
import os
import stat
import subprocess
import sys
import tempfile
import time

CLI = [sys.executable, "-m", "engram_cli"]


def _make_vault(tmpdir):
    vault = os.path.join(tmpdir, "vault")
    os.makedirs(os.path.join(vault, "_meta"), exist_ok=True)
    return vault


def _write_config(tmpdir, vault, claude_bin, **extra):
    config_path = os.path.join(tmpdir, "config.json")
    cfg = {"vault": vault, "worker_claude_bin": claude_bin}
    cfg.update(extra)
    with open(config_path, "w") as f:
        json.dump(cfg, f)
    return config_path


def _write_stage_aware_claude(tmpdir):
    """Fake claude that inspects the prompt and answers per stage.

    Each consolidate stage embeds a distinct section header in its prompt;
    the stub keys off those to return stage-appropriate JSON.
    """
    path = os.path.join(tmpdir, "fake-claude")
    script = r'''#!/usr/bin/env python3
import sys, json
prompt = sys.stdin.read()
if "# Callouts" in prompt:
    result = {"corrections": [], "merges": [], "deletes": ["STALE_FACT"]}
elif "# Candidate groups" in prompt:
    result = {"merges": [{"canonical": "TOPIC_A", "aliases": ["TOPIC_A_DUP"]}]}
elif "# Fading" in prompt:
    result = {"archive": ["LONELY_NODE"]}
elif "# Communities" in prompt:
    # Echo back detected communities with canned titles
    start = prompt.index("# Communities")
    end = prompt.index("# Surprising")
    comms = json.loads(prompt[start:end].split("\n\n", 1)[1])
    result = {"communities": [
        {"id": c["id"], "title": f"Community {c['id']}", "summary": "canned",
         "members": [m["name"] for m in c["members"]]} for c in comms]}
elif "# Daily notes" in prompt:
    result = {"new_patterns": [
        {"name": "test-pattern", "description": "canned pattern",
         "evidence": ["2026-07-01", "2026-07-02"], "confidence": 0.7}],
        "updated_patterns": []}
else:
    result = {}
envelope = {"type": "result", "is_error": False, "result": json.dumps(result)}
sys.stdout.write(json.dumps(envelope))
'''
    with open(path, "w") as f:
        f.write(script)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _run_cli(cmd_args, vault, config_path, check=True):
    env = os.environ.copy()
    env["ENGRAM_CONFIG"] = config_path
    result = subprocess.run(
        CLI + cmd_args + ["--vault", vault],
        capture_output=True, text=True, env=env,
    )
    if check:
        assert result.returncode == 0, f"{cmd_args} failed: {result.stderr}"
    return json.loads(result.stdout)


def _seed_graph(vault, config_path):
    """Land a small graph via replay: duplicates, a lonely node, dailies."""
    entities = [
        {"name": "TOPIC_A", "entity_type": "PROJECT",
         "description": "Main project", "confidence": "EXTRACTED"},
        {"name": "TOPIC_A_DUP", "entity_type": "PROJECT",
         "description": "Same project, duplicate name", "confidence": "EXTRACTED"},
        {"name": "TOPIC_B", "entity_type": "CONCEPT",
         "description": "Concept tied to A", "confidence": "EXTRACTED"},
        {"name": "TOPIC_C", "entity_type": "TOOL",
         "description": "Tool used by A", "confidence": "EXTRACTED"},
        {"name": "OTHER_X", "entity_type": "PROJECT",
         "description": "Unrelated project", "confidence": "EXTRACTED"},
        {"name": "OTHER_Y", "entity_type": "CONCEPT",
         "description": "Concept of X", "confidence": "EXTRACTED"},
        {"name": "LONELY_NODE", "entity_type": "CONCEPT",
         "description": "Isolated", "confidence": "EXTRACTED"},
        {"name": "STALE_FACT", "entity_type": "CONCEPT",
         "description": "Marked wrong by a callout", "confidence": "EXTRACTED"},
    ]
    relations = [
        {"source": "TOPIC_A", "target": "TOPIC_B",
         "description": "core", "weight": 0.8, "confidence": "EXTRACTED"},
        {"source": "TOPIC_A_DUP", "target": "TOPIC_B",
         "description": "dup edge", "weight": 0.5, "confidence": "EXTRACTED"},
        {"source": "TOPIC_A", "target": "TOPIC_C",
         "description": "uses", "weight": 0.7, "confidence": "EXTRACTED"},
        {"source": "OTHER_X", "target": "OTHER_Y",
         "description": "core", "weight": 0.8, "confidence": "EXTRACTED"},
    ]
    # Old date so decay scoring puts the isolated node in prune's report
    payload = json.dumps({
        "date": "2026-01-01", "entities": entities, "relations": relations,
        "daily_summary": "Seed day one.",
    })
    env = os.environ.copy()
    env["ENGRAM_CONFIG"] = config_path
    result = subprocess.run(CLI + ["replay", "--stdin", "--vault", vault],
                            input=payload, capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr
    # Second daily note so abstract has >= 2 evidence dates available
    with open(os.path.join(vault, "daily", "2026-07-02.md"), "w") as f:
        f.write("# 2026-07-02\n\n## Summary\n\n- Seed day two.\n")


def test_consolidate_runs_all_stages_headlessly():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        fake = _write_stage_aware_claude(tmpdir)
        config = _write_config(tmpdir, vault, fake)
        _seed_graph(vault, config)

        # Feedback callout on an entity file
        stale_md = os.path.join(vault, "entities", "concepts", "STALE_FACT.md")
        with open(stale_md, "a") as f:
            f.write("\n> [!delete] wrong fact\n> please remove\n")

        # Due marker that a completed run must clear
        marker = os.path.join(vault, "_meta", "consolidation-due")
        with open(marker, "w") as f:
            f.write("2026-07-01T00:00:00\n")

        out = _run_cli(["consolidate"], vault, config)
        assert out["status"] == "ok", out
        stages = out["stages"]
        assert stages["feedback"] == {"applied": 1}
        assert stages["integrate"] == {"merged": 1}
        assert stages["prune"].get("archived", 0) >= 1
        assert stages["community"].get("saved", 0) >= 1
        assert stages["abstract"] == {"patterns_saved": 1}
        assert "error" not in stages["lint"]

        # Merged + deleted + archived entities are gone; canonical remains
        status = _run_cli(["status"], vault, config)
        assert "TOPIC_A" in status["entities"]
        for gone in ("TOPIC_A_DUP", "STALE_FACT", "LONELY_NODE"):
            assert gone not in status["entities"]

        # Pattern file written
        assert os.path.exists(os.path.join(vault, "patterns", "test-pattern.md"))
        # Counter reset removed the due marker; log written
        assert not os.path.exists(marker)
        assert os.path.exists(os.path.join(vault, "_meta", "consolidate.log"))
        print("  ✅ consolidate: all stages ran headlessly and landed")


def test_consolidate_empty_vault_skips_stages():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        fake = _write_stage_aware_claude(tmpdir)
        config = _write_config(tmpdir, vault, fake)

        out = _run_cli(["consolidate"], vault, config)
        assert out["status"] == "ok", out
        for name in ("feedback", "integrate", "prune", "community", "abstract"):
            assert "skipped" in out["stages"][name], (name, out)
        print("  ✅ consolidate: empty vault skips every claude stage")


def test_consolidate_single_flight_lock():
    import fcntl
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        fake = _write_stage_aware_claude(tmpdir)
        config = _write_config(tmpdir, vault, fake)

        lock_path = os.path.join(vault, "_meta", "consolidate.lock")
        holder = open(lock_path, "w")
        fcntl.flock(holder, fcntl.LOCK_EX)
        try:
            out = _run_cli(["consolidate"], vault, config)
            assert out["status"] == "skipped"
        finally:
            holder.close()
        print("  ✅ consolidate: second run skips while lock held")


def test_consolidate_detach_returns_immediately():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        fake = _write_stage_aware_claude(tmpdir)
        config = _write_config(tmpdir, vault, fake)

        start = time.time()
        out = _run_cli(["consolidate", "--detach"], vault, config)
        elapsed = time.time() - start
        assert out["status"] == "started"
        assert "consolidate.log" in out["log"]
        assert elapsed < 10, f"detach blocked for {elapsed:.1f}s"

        # The detached child eventually completes (empty vault → fast)
        for _ in range(50):
            if os.path.exists(os.path.join(vault, "_meta", "consolidate.log")):
                break
            time.sleep(0.2)
        with open(os.path.join(vault, "_meta", "consolidate.log")) as f:
            log = f.read()
        assert "DONE" in log or "START" in log, log
        print("  ✅ consolidate --detach: returns immediately, child runs")


def test_consolidate_stage_error_does_not_abort_run():
    """A claude failure in one stage is recorded but later stages still run."""
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        # This claude always fails — stages with work error, skips still skip
        fake = os.path.join(tmpdir, "fake-claude")
        with open(fake, "w") as f:
            f.write("#!/usr/bin/env python3\nimport sys\nsys.stderr.write('boom')\nsys.exit(1)\n")
        os.chmod(fake, os.stat(fake).st_mode | stat.S_IXUSR)
        config = _write_config(tmpdir, vault, fake)
        _seed_graph(vault, config)

        out = _run_cli(["consolidate"], vault, config)
        assert out["status"] == "ok"
        stages = out["stages"]
        # integrate has candidates → hits claude → records the error
        assert "error" in stages["integrate"]
        # but the run continued through lint and finished
        assert "issues" in stages["lint"]
        print("  ✅ consolidate: stage errors are isolated, run completes")


def test_worker_auto_consolidate_config_spawns():
    """With worker_auto_consolidate, hitting the threshold leaves evidence
    of a spawned consolidate run (its log/lock), not just the due marker."""
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        fake = _write_stage_aware_claude(tmpdir)
        # Extraction-shaped answer for the worker's own claude call
        extraction = {"date": "2026-07-01", "entities": [
            {"name": "AUTO_E", "entity_type": "CONCEPT",
             "description": "d", "confidence": "EXTRACTED"}],
            "relations": [], "daily_summary": "s"}
        worker_claude = os.path.join(tmpdir, "fake-worker-claude")
        envelope = json.dumps({"type": "result", "is_error": False,
                               "result": json.dumps(extraction)})
        with open(worker_claude, "w") as f:
            f.write(f"#!/usr/bin/env python3\nimport sys\nsys.stdin.read()\nsys.stdout.write({envelope!r})\n")
        os.chmod(worker_claude, os.stat(worker_claude).st_mode | stat.S_IXUSR)
        config = _write_config(tmpdir, vault, worker_claude,
                               worker_consolidation_every=1,
                               worker_auto_consolidate=True)

        pending = os.path.join(vault, "_meta", "pending.jsonl")
        with open(pending, "w") as f:
            f.write(json.dumps({"session_id": "s", "timestamp": "t",
                                "turn_text": "turn"}) + "\n")

        env = os.environ.copy()
        env["ENGRAM_CONFIG"] = config
        result = subprocess.run(CLI + ["worker", "--vault", vault],
                                capture_output=True, text=True, env=env)
        assert result.returncode == 0, result.stderr

        # Threshold hit → marker written AND detached consolidate spawned
        assert os.path.exists(os.path.join(vault, "_meta", "consolidation-due")) or \
            os.path.exists(os.path.join(vault, "_meta", "consolidate.log"))
        for _ in range(50):
            if os.path.exists(os.path.join(vault, "_meta", "consolidate.log")):
                break
            time.sleep(0.2)
        assert os.path.exists(os.path.join(vault, "_meta", "consolidate.log"))
        print("  ✅ worker: auto_consolidate spawns detached consolidate")
