"""Tests for independent automatic capture and injection controls."""

import json
import os
import subprocess
import sys
import tempfile


CLI = [sys.executable, "-m", "engram_cli"]


def _run(mode, vault, config):
    result = subprocess.run(
        CLI + ["auto", mode, "--vault", vault], capture_output=True, text=True,
        env={**os.environ, "ENGRAM_CONFIG": config})
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_auto_modes_and_legacy_marker_migration():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = os.path.join(tmpdir, "vault")
        meta = os.path.join(vault, "_meta")
        os.makedirs(meta)
        config = os.path.join(tmpdir, "config.json")
        with open(config, "w") as f:
            json.dump({"vault": vault}, f)

        expected = {
            "on": (True, True),
            "capture-only": (True, False),
            "injection-only": (False, True),
            "off": (False, False),
        }
        for mode, flags in expected.items():
            state = _run(mode, vault, config)
            assert state["mode"] == mode
            assert (state["capture_enabled"], state["injection_enabled"]) == flags
            assert _run("status", vault, config)["mode"] == mode

        open(os.path.join(meta, "hook-enabled"), "w").close()
        legacy = _run("status", vault, config)
        assert legacy["mode"] == "on"
        assert legacy["capture_enabled"] and legacy["injection_enabled"]
