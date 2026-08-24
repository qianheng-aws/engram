"""Tests for SessionStart and UserPromptSubmit context injection."""

import json
import os
import subprocess
import sys
import tempfile


HOOK = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                    "plugin", "bin", "engram-context")


def _make_vault(tmpdir, flag="injection-enabled"):
    vault = os.path.join(tmpdir, "vault")
    meta = os.path.join(vault, "_meta")
    os.makedirs(meta)
    if flag:
        open(os.path.join(meta, flag), "w").close()
    return vault


def _config(tmpdir, vault, **extra):
    path = os.path.join(tmpdir, "config.json")
    data = {"vault": vault, **extra}
    with open(path, "w") as f:
        json.dump(data, f)
    return path


def _run(mode, config, payload, path="/usr/bin:/bin"):
    return subprocess.run(
        [sys.executable, HOOK, mode], input=json.dumps(payload),
        capture_output=True, text=True,
        env={**os.environ, "ENGRAM_CONFIG": config, "PATH": path},
    )


def _write_index(vault, entities, version=2):
    path = os.path.join(vault, "_meta", "entity-index.json")
    with open(path, "w") as f:
        json.dump({"version": version, "entities": entities}, f)


def test_injection_flags_and_legacy_compatibility():
    with tempfile.TemporaryDirectory() as tmpdir:
        for flag, expected in ((None, False), ("capture-enabled", False),
                               ("injection-enabled", True), ("hook-enabled", True)):
            case = os.path.join(tmpdir, flag or "off")
            os.makedirs(case)
            vault = _make_vault(case, flag)
            with open(os.path.join(vault, "_meta", "context-cache.md"), "w") as f:
                f.write("stable digest")
            result = _run("session", _config(case, vault), {})
            assert ("stable digest" in result.stdout) is expected, (flag, result.stdout)


def test_session_injects_stable_digest_only():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        with open(os.path.join(vault, "_meta", "context-cache.md"), "w") as f:
            f.write("**Top Entities:** ENGRAM, CODEX")
        result = _run("session", _config(tmpdir, vault), {"prompt": "ignored"})
        assert result.returncode == 0
        assert result.stdout.startswith("## Engram Session Memory")
        assert "ENGRAM, CODEX" in result.stdout


def test_session_default_and_configured_budget():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        with open(os.path.join(vault, "_meta", "context-cache.md"), "w") as f:
            f.write("X" * 3000)
        default = _run("session", _config(tmpdir, vault), {})
        assert len(default.stdout.rstrip("\n")) <= 1200
        assert "…" in default.stdout
        configured = _run(
            "session", _config(tmpdir, vault, context_session_max_chars=300), {})
        assert len(configured.stdout.rstrip("\n")) <= 300


def test_prompt_without_match_injects_nothing_even_with_digest():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        with open(os.path.join(vault, "_meta", "context-cache.md"), "w") as f:
            f.write("digest must not repeat on every prompt")
        _write_index(vault, {
            "ENGRAM": {"keywords": ["engram"], "snippet": "- ENGRAM: memory"},
        })
        result = _run("prompt", _config(tmpdir, vault), {"prompt": "hello there"})
        assert result.returncode == 0
        assert result.stdout == ""


def test_prompt_fast_path_matches_and_caps_output():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        _write_index(vault, {
            "FLOW_FRAMEWORK": {
                "keywords": ["flow", "framework"],
                "snippet": "- FLOW_FRAMEWORK: " + "X" * 1500,
                "local_path": "",
            },
        })
        result = _run("prompt", _config(tmpdir, vault),
                      {"prompt": "explain the flow framework"})
        assert result.stdout.startswith("## Memory Context")
        assert "FLOW_FRAMEWORK" in result.stdout
        assert len(result.stdout.rstrip("\n")) <= 1000
        assert "…" in result.stdout


def test_prompt_supports_chinese_ngram_matching():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        _write_index(vault, {
            "MEMORY_INJECTION": {
                "keywords": ["记忆", "记忆注入", "注入"],
                "snippet": "- MEMORY_INJECTION: 自动记忆注入",
            },
        })
        result = _run("prompt", _config(tmpdir, vault),
                      {"prompt": "如何优化记忆注入性能？"})
        assert "MEMORY_INJECTION" in result.stdout


def test_prompt_ranking_boosts_current_project_path():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        project = os.path.join(tmpdir, "repo")
        os.makedirs(project)
        _write_index(vault, {
            "REMOTE_ENGRAM": {
                "keywords": ["engram"], "snippet": "- REMOTE_ENGRAM",
                "local_path": "/somewhere/else",
            },
            "LOCAL_ENGRAM": {
                "keywords": ["engram"], "snippet": "- LOCAL_ENGRAM",
                "local_path": project,
            },
        })
        result = _run("prompt", _config(tmpdir, vault),
                      {"prompt": "engram", "cwd": project})
        assert result.stdout.index("LOCAL_ENGRAM") < result.stdout.index("REMOTE_ENGRAM")


def test_version_one_index_remains_compatible():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir, "hook-enabled")
        _write_index(vault, {
            "LEGACY": {"keywords": ["legacy"], "snippet": "- LEGACY: v1"},
        }, version=1)
        result = _run("prompt", _config(tmpdir, vault), {"prompt": "legacy"})
        assert "LEGACY" in result.stdout


def test_missing_or_corrupt_index_falls_back_to_query():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        with open(os.path.join(vault, "_meta", "entity-index.json"), "w") as f:
            f.write("{broken")
        fake_dir = os.path.join(tmpdir, "bin")
        os.makedirs(fake_dir)
        fake = os.path.join(fake_dir, "engram")
        with open(fake, "w") as f:
            f.write("#!/bin/sh\nprintf '%s\\n' '{\"matched_entities\":[\"FALLBACK\"],\"expanded_entities\":[],\"context\":\"## FALLBACK\\nquery result\"}'\n")
        os.chmod(fake, 0o755)
        result = _run("prompt", _config(tmpdir, vault),
                      {"prompt": "fallback query"}, f"{fake_dir}:/usr/bin:/bin")
        assert "FALLBACK" in result.stdout


def test_malformed_payload_and_unknown_mode_are_silent():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        config = _config(tmpdir, vault)
        malformed = subprocess.run(
            [sys.executable, HOOK, "prompt"], input="not-json",
            capture_output=True, text=True,
            env={**os.environ, "ENGRAM_CONFIG": config},
        )
        unknown = _run("unknown", config, {"prompt": "engram"})
        assert malformed.returncode == unknown.returncode == 0
        assert malformed.stdout == unknown.stdout == ""
