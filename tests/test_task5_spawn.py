"""Tests for Task 5: Stop/SessionEnd detached worker spawn."""

import json
import os
import subprocess
import sys
import tempfile
import time


def _make_vault(tmpdir):
    vault = os.path.join(tmpdir, "vault")
    os.makedirs(os.path.join(vault, "_meta"), exist_ok=True)
    return vault


def _make_stub_engram(stub_dir, marker_path):
    """Create a fake engram script that writes a marker file when called with 'worker'."""
    stub_path = os.path.join(stub_dir, "engram")
    with open(stub_path, "w") as f:
        f.write("#!/usr/bin/env python3\n")
        f.write("import sys, time\n")
        f.write(f"if len(sys.argv) > 1 and sys.argv[1] == 'worker':\n")
        f.write(f"    with open('{marker_path}', 'w') as f:\n")
        f.write(f"        f.write('spawned\\n')\n")
        f.write("    time.sleep(5)  # Sleep to verify detachment\n")
    os.chmod(stub_path, 0o755)
    return stub_path


def test_stop_hook_spawns_worker_on_substantive_turn():
    """Stop hook spawns worker detached after appending substantive turn."""
    hook_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "plugin", "bin", "engram-hook")

    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        open(os.path.join(vault, "_meta", "hook-enabled"), "w").close()

        config_path = os.path.join(tmpdir, "config.json")
        with open(config_path, "w") as f:
            json.dump({"vault": vault}, f)

        # Create stub engram on test-controlled PATH
        stub_dir = os.path.join(tmpdir, "bin")
        os.makedirs(stub_dir)
        marker_path = os.path.join(tmpdir, "worker-spawned.marker")
        _make_stub_engram(stub_dir, marker_path)

        payload = json.dumps({
            "session_id": "test-session-spawn",
            "last_assistant_message": "This is a substantive assistant message that exceeds 50 characters.",
            "stop_reason": "user",
        })

        env = {**os.environ, "ENGRAM_CONFIG": config_path, "PATH": f"{stub_dir}:{os.environ['PATH']}"}
        result = subprocess.run(
            [sys.executable, hook_path, "Stop"],
            input=payload, capture_output=True, text=True,
            env=env,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"

        # Hook should exit promptly (< 2 seconds) despite stub worker sleeping
        # (this verifies detachment)

        # Poll for marker file (worker spawn is async)
        for _ in range(20):  # Poll up to 2 seconds
            if os.path.exists(marker_path):
                break
            time.sleep(0.1)

        assert os.path.exists(marker_path), "Worker was not spawned after substantive turn"
        print("  ✅ stop hook spawns worker on substantive turn")


def test_stop_hook_no_spawn_on_trivial_turn():
    """Stop hook does NOT spawn worker when turn is trivial (nothing appended)."""
    hook_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "plugin", "bin", "engram-hook")

    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        open(os.path.join(vault, "_meta", "hook-enabled"), "w").close()

        config_path = os.path.join(tmpdir, "config.json")
        with open(config_path, "w") as f:
            json.dump({"vault": vault}, f)

        stub_dir = os.path.join(tmpdir, "bin")
        os.makedirs(stub_dir)
        marker_path = os.path.join(tmpdir, "worker-spawned.marker")
        _make_stub_engram(stub_dir, marker_path)

        # Short message (< 50 chars) - trivial turn
        payload = json.dumps({
            "session_id": "short-session",
            "last_assistant_message": "Short",
            "stop_reason": "user",
        })

        env = {**os.environ, "ENGRAM_CONFIG": config_path, "PATH": f"{stub_dir}:{os.environ['PATH']}"}
        result = subprocess.run(
            [sys.executable, hook_path, "Stop"],
            input=payload, capture_output=True, text=True,
            env=env,
        )
        assert result.returncode == 0

        # Wait a bit to ensure no spawn
        time.sleep(0.5)

        assert not os.path.exists(marker_path), "Worker should not spawn on trivial turn"
        print("  ✅ stop hook does not spawn worker on trivial turn")


def test_stop_hook_no_spawn_when_disabled():
    """Stop hook does NOT spawn worker when kill switch is off."""
    hook_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "plugin", "bin", "engram-hook")

    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        # Do NOT create hook-enabled file

        config_path = os.path.join(tmpdir, "config.json")
        with open(config_path, "w") as f:
            json.dump({"vault": vault}, f)

        stub_dir = os.path.join(tmpdir, "bin")
        os.makedirs(stub_dir)
        marker_path = os.path.join(tmpdir, "worker-spawned.marker")
        _make_stub_engram(stub_dir, marker_path)

        payload = json.dumps({
            "session_id": "disabled-session",
            "last_assistant_message": "This message is long enough to pass the 50 char threshold.",
            "stop_reason": "user",
        })

        env = {**os.environ, "ENGRAM_CONFIG": config_path, "PATH": f"{stub_dir}:{os.environ['PATH']}"}
        result = subprocess.run(
            [sys.executable, hook_path, "Stop"],
            input=payload, capture_output=True, text=True,
            env=env,
        )
        assert result.returncode == 0

        time.sleep(0.5)

        assert not os.path.exists(marker_path), "Worker should not spawn when hook disabled"
        print("  ✅ stop hook respects kill switch (no spawn when disabled)")


def test_stop_hook_exits_promptly():
    """Stop hook exits within 2 seconds even though stub worker sleeps."""
    hook_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "plugin", "bin", "engram-hook")

    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        open(os.path.join(vault, "_meta", "hook-enabled"), "w").close()

        config_path = os.path.join(tmpdir, "config.json")
        with open(config_path, "w") as f:
            json.dump({"vault": vault}, f)

        stub_dir = os.path.join(tmpdir, "bin")
        os.makedirs(stub_dir)
        marker_path = os.path.join(tmpdir, "worker-spawned.marker")
        _make_stub_engram(stub_dir, marker_path)

        payload = json.dumps({
            "session_id": "detach-test-session",
            "last_assistant_message": "This is a substantive assistant message that exceeds 50 characters.",
            "stop_reason": "user",
        })

        env = {**os.environ, "ENGRAM_CONFIG": config_path, "PATH": f"{stub_dir}:{os.environ['PATH']}"}

        start = time.time()
        result = subprocess.run(
            [sys.executable, hook_path, "Stop"],
            input=payload, capture_output=True, text=True,
            env=env,
        )
        elapsed = time.time() - start

        assert result.returncode == 0
        assert elapsed < 2.0, f"Hook took {elapsed:.2f}s (expected < 2s) - not properly detached"
        print(f"  ✅ stop hook exits promptly ({elapsed:.3f}s)")


def test_sessionend_spawns_worker_with_unprocessed_content():
    """SessionEnd hook spawns worker when pending.jsonl has unprocessed content."""
    hook_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "plugin", "bin", "engram-hook")

    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        open(os.path.join(vault, "_meta", "hook-enabled"), "w").close()

        config_path = os.path.join(tmpdir, "config.json")
        with open(config_path, "w") as f:
            json.dump({"vault": vault}, f)

        # Create pending.jsonl with unprocessed content
        pending_path = os.path.join(vault, "_meta", "pending.jsonl")
        with open(pending_path, "w") as f:
            f.write(json.dumps({"session_id": "s1", "timestamp": "2024-01-01T00:00:00", "turn_text": "content"}) + "\n")

        # Create worker-state.json with offset=0 (unprocessed)
        state_path = os.path.join(vault, "_meta", "worker-state.json")
        with open(state_path, "w") as f:
            json.dump({"offset": 0}, f)

        stub_dir = os.path.join(tmpdir, "bin")
        os.makedirs(stub_dir)
        marker_path = os.path.join(tmpdir, "worker-spawned.marker")
        _make_stub_engram(stub_dir, marker_path)

        # SessionEnd event with empty stdin
        env = {**os.environ, "ENGRAM_CONFIG": config_path, "PATH": f"{stub_dir}:{os.environ['PATH']}"}
        result = subprocess.run(
            [sys.executable, hook_path, "SessionEnd"],
            input="{}", capture_output=True, text=True,
            env=env,
        )
        assert result.returncode == 0

        # Poll for marker
        for _ in range(20):
            if os.path.exists(marker_path):
                break
            time.sleep(0.1)

        assert os.path.exists(marker_path), "Worker was not spawned on SessionEnd with unprocessed content"
        print("  ✅ SessionEnd hook spawns worker with unprocessed content")


def test_sessionend_no_spawn_when_fully_processed():
    """SessionEnd hook does NOT spawn worker when queue is fully processed."""
    hook_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "plugin", "bin", "engram-hook")

    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        open(os.path.join(vault, "_meta", "hook-enabled"), "w").close()

        config_path = os.path.join(tmpdir, "config.json")
        with open(config_path, "w") as f:
            json.dump({"vault": vault}, f)

        # Create pending.jsonl
        pending_path = os.path.join(vault, "_meta", "pending.jsonl")
        record = json.dumps({"session_id": "s1", "timestamp": "2024-01-01T00:00:00", "turn_text": "content"})
        with open(pending_path, "w") as f:
            f.write(record + "\n")

        # Create worker-state.json with offset equal to file size (fully processed)
        file_size = os.path.getsize(pending_path)
        state_path = os.path.join(vault, "_meta", "worker-state.json")
        with open(state_path, "w") as f:
            json.dump({"offset": file_size}, f)

        stub_dir = os.path.join(tmpdir, "bin")
        os.makedirs(stub_dir)
        marker_path = os.path.join(tmpdir, "worker-spawned.marker")
        _make_stub_engram(stub_dir, marker_path)

        env = {**os.environ, "ENGRAM_CONFIG": config_path, "PATH": f"{stub_dir}:{os.environ['PATH']}"}
        result = subprocess.run(
            [sys.executable, hook_path, "SessionEnd"],
            input="{}", capture_output=True, text=True,
            env=env,
        )
        assert result.returncode == 0

        time.sleep(0.5)

        assert not os.path.exists(marker_path), "Worker should not spawn when queue is fully processed"
        print("  ✅ SessionEnd hook does not spawn when queue fully processed")


def test_sessionend_no_spawn_when_disabled():
    """SessionEnd hook respects kill switch."""
    hook_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "plugin", "bin", "engram-hook")

    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        # Do NOT create hook-enabled file

        config_path = os.path.join(tmpdir, "config.json")
        with open(config_path, "w") as f:
            json.dump({"vault": vault}, f)

        # Create unprocessed content
        pending_path = os.path.join(vault, "_meta", "pending.jsonl")
        with open(pending_path, "w") as f:
            f.write(json.dumps({"session_id": "s1", "timestamp": "2024-01-01T00:00:00", "turn_text": "content"}) + "\n")

        state_path = os.path.join(vault, "_meta", "worker-state.json")
        with open(state_path, "w") as f:
            json.dump({"offset": 0}, f)

        stub_dir = os.path.join(tmpdir, "bin")
        os.makedirs(stub_dir)
        marker_path = os.path.join(tmpdir, "worker-spawned.marker")
        _make_stub_engram(stub_dir, marker_path)

        env = {**os.environ, "ENGRAM_CONFIG": config_path, "PATH": f"{stub_dir}:{os.environ['PATH']}"}
        result = subprocess.run(
            [sys.executable, hook_path, "SessionEnd"],
            input="{}", capture_output=True, text=True,
            env=env,
        )
        assert result.returncode == 0

        time.sleep(0.5)

        assert not os.path.exists(marker_path), "Worker should not spawn when hook disabled"
        print("  ✅ SessionEnd hook respects kill switch")


if __name__ == "__main__":
    print("Testing Task 5: Stop/SessionEnd detached worker spawn...")
    test_stop_hook_spawns_worker_on_substantive_turn()
    test_stop_hook_no_spawn_on_trivial_turn()
    test_stop_hook_no_spawn_when_disabled()
    test_stop_hook_exits_promptly()
    test_sessionend_spawns_worker_with_unprocessed_content()
    test_sessionend_no_spawn_when_fully_processed()
    test_sessionend_no_spawn_when_disabled()
    print("\n🎉 All Task 5 tests passed!")
