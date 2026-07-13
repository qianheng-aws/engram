"""E2E tests for `engram worker` — background drain of pending.jsonl.

All tests stub the claude binary via the `worker_claude_bin` config key.
No real LLM calls are ever made.
"""

import fcntl
import json
import os
import stat
import subprocess
import sys
import tempfile

CLI = [sys.executable, "-m", "engram_cli"]

EXTRACTION = {
    "date": "2026-07-13",
    "entities": [
        {"name": "WORKER_ENTITY_A", "entity_type": "CONCEPT",
         "description": "Landed by the background worker", "confidence": "EXTRACTED"},
        {"name": "WORKER_ENTITY_B", "entity_type": "TOOL",
         "description": "Second worker entity", "confidence": "EXTRACTED"},
    ],
    "relations": [
        {"source": "WORKER_ENTITY_A", "target": "WORKER_ENTITY_B",
         "description": "worker relation", "weight": 0.7, "confidence": "EXTRACTED"},
    ],
    "daily_summary": "Background worker extraction test.",
}


def _make_vault(tmpdir):
    vault = os.path.join(tmpdir, "vault")
    os.makedirs(os.path.join(vault, "_meta"), exist_ok=True)
    return vault


def _write_fake_claude(tmpdir, extraction=None, exit_code=0, fenced=True):
    """Create a fake claude binary printing a canned envelope JSON."""
    path = os.path.join(tmpdir, "fake-claude")
    if exit_code != 0:
        body = f"#!/bin/sh\necho 'boom' >&2\nexit {exit_code}\n"
    else:
        text = json.dumps(extraction if extraction is not None else EXTRACTION)
        if fenced:
            text = "```json\n" + text + "\n```"
        envelope = json.dumps({"type": "result", "is_error": False, "result": text})
        body = "#!/bin/sh\ncat <<'ENGRAM_FAKE_EOF'\n" + envelope + "\nENGRAM_FAKE_EOF\n"
    with open(path, "w") as f:
        f.write(body)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _write_config(tmpdir, vault, claude_bin, **extra):
    config_path = os.path.join(tmpdir, "config.json")
    cfg = {"vault": vault, "worker_claude_bin": claude_bin}
    cfg.update(extra)
    with open(config_path, "w") as f:
        json.dump(cfg, f)
    return config_path


def _seed_pending(vault, n=2, garbage_at=None):
    """Append n turn records (and optionally a garbage line) to pending.jsonl."""
    pending = os.path.join(vault, "_meta", "pending.jsonl")
    with open(pending, "a") as f:
        for i in range(n):
            if garbage_at is not None and i == garbage_at:
                f.write("{this is not json!!\n")
            f.write(json.dumps({
                "session_id": f"sess-{i}",
                "timestamp": f"2026-07-13T10:0{i}:00",
                "turn_text": f"User asked about topic {i}. Assistant explained it.",
            }) + "\n")
    return pending


def _run_worker(vault, config_path, extra_args=None, check=False):
    env = os.environ.copy()
    env["ENGRAM_CONFIG"] = config_path
    result = subprocess.run(
        CLI + ["worker", "--vault", vault] + (extra_args or []),
        capture_output=True, text=True, env=env,
    )
    if check:
        assert result.returncode == 0, (
            f"worker failed:\nstderr: {result.stderr}\nstdout: {result.stdout}")
    return result


def _run_cli(cmd_args, vault, config_path):
    env = os.environ.copy()
    env["ENGRAM_CONFIG"] = config_path
    result = subprocess.run(
        CLI + cmd_args + ["--vault", vault],
        capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, f"{cmd_args} failed: {result.stderr}"
    return json.loads(result.stdout)


def _read_state(vault):
    with open(os.path.join(vault, "_meta", "worker-state.json")) as f:
        return json.load(f)


# ── watermark drain ───────────────────────────────────────

def test_worker_drains_and_advances_watermark():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        fake = _write_fake_claude(tmpdir)
        config = _write_config(tmpdir, vault, fake)
        pending = _seed_pending(vault, n=2)

        result = _run_worker(vault, config, check=True)
        summary = json.loads(result.stdout)
        assert summary["processed"] == 2
        assert summary["entities"] == 2
        assert summary["relations"] == 1

        # Entities landed in the graph
        status = _run_cli(["status"], vault, config)
        assert "WORKER_ENTITY_A" in status["entities"]
        assert "WORKER_ENTITY_B" in status["entities"]

        # Offset advanced to file size
        state = _read_state(vault)
        assert state["offset"] == os.path.getsize(pending)

        # Second run with unchanged file → no-op
        result2 = _run_worker(vault, config, check=True)
        summary2 = json.loads(result2.stdout)
        assert summary2["processed"] == 0
        print("  ✅ worker: drains pending, lands entities, advances watermark")


def test_worker_no_pending_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        fake = _write_fake_claude(tmpdir)
        config = _write_config(tmpdir, vault, fake)
        result = _run_worker(vault, config, check=True)
        summary = json.loads(result.stdout)
        assert summary == {"processed": 0, "entities": 0, "relations": 0}
        print("  ✅ worker: no pending.jsonl is a cheap no-op")


def test_worker_truncated_pending_resets_offset():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        fake = _write_fake_claude(tmpdir)
        config = _write_config(tmpdir, vault, fake)
        _seed_pending(vault, n=1)
        # Simulate a stale offset beyond file size (file was rotated)
        with open(os.path.join(vault, "_meta", "worker-state.json"), "w") as f:
            json.dump({"offset": 999999}, f)

        result = _run_worker(vault, config, check=True)
        summary = json.loads(result.stdout)
        assert summary["processed"] == 1
        print("  ✅ worker: stale offset past EOF resets to 0 and reprocesses")


# ── single-flight lock ────────────────────────────────────

def test_worker_lock_held_exits_zero_without_processing():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        fake = _write_fake_claude(tmpdir)
        config = _write_config(tmpdir, vault, fake)
        _seed_pending(vault, n=2)

        lock_path = os.path.join(vault, "_meta", "worker.lock")
        with open(lock_path, "w") as lock_f:
            fcntl.flock(lock_f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = _run_worker(vault, config)
            assert result.returncode == 0
            summary = json.loads(result.stdout)
            assert summary["processed"] == 0
            # Nothing processed → no state written / no offset advance
            state_path = os.path.join(vault, "_meta", "worker-state.json")
            assert not os.path.exists(state_path)
        print("  ✅ worker: exits 0 immediately when lock is held")


# ── failure handling ──────────────────────────────────────

def test_worker_claude_failure_keeps_offset_and_logs():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        fake = _write_fake_claude(tmpdir, exit_code=1)
        config = _write_config(tmpdir, vault, fake)
        _seed_pending(vault, n=2)

        result = _run_worker(vault, config)
        assert result.returncode != 0

        # Offset NOT advanced
        state_path = os.path.join(vault, "_meta", "worker-state.json")
        if os.path.exists(state_path):
            assert _read_state(vault).get("offset", 0) == 0

        # Error logged
        log_path = os.path.join(vault, "_meta", "worker.log")
        assert os.path.exists(log_path)
        with open(log_path) as f:
            assert "ERROR" in f.read()

        # A later run with a working claude retries the same turns
        good = _write_fake_claude(tmpdir)
        config2 = _write_config(tmpdir, vault, good)
        result2 = _run_worker(vault, config2, check=True)
        assert json.loads(result2.stdout)["processed"] == 2
        print("  ✅ worker: claude failure leaves offset, logs error, exits non-zero")


def test_worker_unparseable_output_fails():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        path = os.path.join(tmpdir, "fake-claude")
        with open(path, "w") as f:
            f.write("#!/bin/sh\necho 'not json at all'\n")
        os.chmod(path, 0o755)
        config = _write_config(tmpdir, vault, path)
        _seed_pending(vault, n=1)

        result = _run_worker(vault, config)
        assert result.returncode != 0
        with open(os.path.join(vault, "_meta", "worker.log")) as f:
            assert "ERROR" in f.read()
        print("  ✅ worker: unparseable claude output → non-zero exit, logged")


def test_worker_invalid_schema_no_dedup_poisoning():
    """A malformed field NOT in the dedup hash (hyperedge missing 'id') must
    fail BEFORE any dedup marker is written, so a corrected retry can land."""
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        bad_extraction = dict(EXTRACTION)
        # Same entities/relations/summary → same dedup hash as the good payload
        bad_extraction["hyperedges"] = [{"label": "No Id Group",
                                         "members": ["WORKER_ENTITY_A", "WORKER_ENTITY_B"]}]
        bad = _write_fake_claude(tmpdir, extraction=bad_extraction)
        config = _write_config(tmpdir, vault, bad)
        _seed_pending(vault, n=1)

        result = _run_worker(vault, config)
        assert result.returncode != 0
        assert "Traceback" not in result.stderr

        # Offset NOT advanced, error logged
        state_path = os.path.join(vault, "_meta", "worker-state.json")
        if os.path.exists(state_path):
            assert _read_state(vault).get("offset", 0) == 0
        with open(os.path.join(vault, "_meta", "worker.log")) as f:
            assert "ERROR" in f.read()

        # Retry with corrected claude (identical dedup hash) must LAND,
        # proving the failed run never wrote a dedup marker
        good = _write_fake_claude(tmpdir)
        config2 = _write_config(tmpdir, vault, good)
        result2 = _run_worker(vault, config2, check=True)
        summary = json.loads(result2.stdout)
        assert summary["processed"] == 1
        assert summary["entities"] == 2

        status = _run_cli(["status"], vault, config2)
        assert "WORKER_ENTITY_A" in status["entities"]
        print("  ✅ worker: invalid schema fails pre-dedup; corrected retry lands")


def test_worker_entity_missing_name_logged_error():
    """Entity without 'name' → clean ERROR log line + non-zero exit, no traceback."""
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        bad_extraction = {
            "date": "2026-07-13",
            "entities": [{"entity_type": "CONCEPT", "description": "nameless"}],
            "relations": [],
            "daily_summary": "bad",
        }
        bad = _write_fake_claude(tmpdir, extraction=bad_extraction)
        config = _write_config(tmpdir, vault, bad)
        _seed_pending(vault, n=1)

        result = _run_worker(vault, config)
        assert result.returncode != 0
        assert "Traceback" not in result.stderr
        with open(os.path.join(vault, "_meta", "worker.log")) as f:
            assert "ERROR" in f.read()
        state_path = os.path.join(vault, "_meta", "worker-state.json")
        if os.path.exists(state_path):
            assert _read_state(vault).get("offset", 0) == 0
        print("  ✅ worker: entity missing 'name' → logged error, offset kept")


def test_worker_envelope_is_error():
    """Envelope with is_error true → extraction failure logging the API error text."""
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        path = os.path.join(tmpdir, "fake-claude")
        envelope = json.dumps({"type": "result", "is_error": True,
                               "result": "API rate limit exceeded"})
        with open(path, "w") as f:
            f.write("#!/bin/sh\ncat <<'ENGRAM_FAKE_EOF'\n" + envelope + "\nENGRAM_FAKE_EOF\n")
        os.chmod(path, 0o755)
        config = _write_config(tmpdir, vault, path)
        _seed_pending(vault, n=1)

        result = _run_worker(vault, config)
        assert result.returncode != 0
        with open(os.path.join(vault, "_meta", "worker.log")) as f:
            log = f.read()
        assert "ERROR" in log
        assert "rate limit" in log
        print("  ✅ worker: is_error envelope → failure with API error text logged")


# ── dedup honored ─────────────────────────────────────────

def test_worker_dedup_prevents_double_landing():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        fake = _write_fake_claude(tmpdir)  # always returns the same extraction
        config = _write_config(tmpdir, vault, fake)
        _seed_pending(vault, n=1)
        _run_worker(vault, config, check=True)

        nodes_before = _run_cli(["status"], vault, config)["nodes"]

        # New turn arrives, but claude returns the identical extraction
        _seed_pending(vault, n=1)
        result = _run_worker(vault, config, check=True)
        summary = json.loads(result.stdout)
        assert summary["processed"] == 1  # turn was consumed
        assert summary["entities"] == 0   # but dedup blocked re-landing

        nodes_after = _run_cli(["status"], vault, config)["nodes"]
        assert nodes_after == nodes_before
        print("  ✅ worker: 15-min dedup window blocks duplicate extraction")


# ── cache refresh ─────────────────────────────────────────

def test_worker_refreshes_context_cache():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        fake = _write_fake_claude(tmpdir)
        config = _write_config(tmpdir, vault, fake)
        _seed_pending(vault, n=1)

        _run_worker(vault, config, check=True)
        cache_path = os.path.join(vault, "_meta", "context-cache.md")
        assert os.path.exists(cache_path)
        with open(cache_path) as f:
            assert "WORKER_ENTITY_A" in f.read()
        print("  ✅ worker: refreshes _meta/context-cache.md after landing")


# ── malformed pending lines ───────────────────────────────

def test_worker_skips_malformed_pending_lines():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        fake = _write_fake_claude(tmpdir)
        config = _write_config(tmpdir, vault, fake)
        pending = _seed_pending(vault, n=2, garbage_at=1)  # valid, garbage, valid

        result = _run_worker(vault, config, check=True)
        summary = json.loads(result.stdout)
        assert summary["processed"] == 2  # garbage line skipped
        assert summary["entities"] == 2
        # Offset advanced past the garbage line too
        assert _read_state(vault)["offset"] == os.path.getsize(pending)
        print("  ✅ worker: malformed pending lines skipped, valid ones processed")


# ── consolidation hook ────────────────────────────────────

def test_worker_consolidation_marker_and_counter_reset():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        fake = _write_fake_claude(tmpdir)
        config = _write_config(tmpdir, vault, fake, worker_consolidation_every=1)
        _seed_pending(vault, n=1)

        _run_worker(vault, config, check=True)

        marker = os.path.join(vault, "_meta", "consolidation-due")
        assert os.path.exists(marker)
        assert _read_state(vault)["replays_since_consolidation"] == 0

        # engram status surfaces the recommendation
        status = _run_cli(["status"], vault, config)
        assert status.get("consolidation_recommended") is True
        print("  ✅ worker: consolidation marker written, counter reset, status surfaces it")


if __name__ == "__main__":
    test_worker_drains_and_advances_watermark()
    test_worker_no_pending_file()
    test_worker_truncated_pending_resets_offset()
    test_worker_lock_held_exits_zero_without_processing()
    test_worker_claude_failure_keeps_offset_and_logs()
    test_worker_unparseable_output_fails()
    test_worker_invalid_schema_no_dedup_poisoning()
    test_worker_entity_missing_name_logged_error()
    test_worker_envelope_is_error()
    test_worker_dedup_prevents_double_landing()
    test_worker_refreshes_context_cache()
    test_worker_skips_malformed_pending_lines()
    test_worker_consolidation_marker_and_counter_reset()
    print("\n🎉 All worker tests passed!")
