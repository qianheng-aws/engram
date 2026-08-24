"""Tests for Codex CLI compatibility.

Covers the three integration points:
1. engram-hook parses Codex rollout transcripts (and still parses CC format)
2. engram-pretool handles Codex's exec_command tool + hookSpecificOutput wire
3. `engram codex-setup` merges hooks.json idempotently and installs prompts

No real Codex or model calls — everything runs against fixture files.
"""

import json
import os
import subprocess
import sys
import tempfile

CLI = [sys.executable, "-m", "engram_cli"]
PLUGIN_BIN = os.path.join(os.path.dirname(os.path.dirname(__file__)), "plugin", "bin")


def _make_vault(tmpdir):
    vault = os.path.join(tmpdir, "vault")
    os.makedirs(os.path.join(vault, "_meta"), exist_ok=True)
    open(os.path.join(vault, "_meta", "hook-enabled"), "w").close()
    return vault


def _write_config(tmpdir, vault):
    config_path = os.path.join(tmpdir, "config.json")
    with open(config_path, "w") as f:
        json.dump({"vault": vault}, f)
    return config_path


def _codex_message(role, text, block_type=None):
    """One Codex rollout response_item message line."""
    if block_type is None:
        block_type = "input_text" if role == "user" else "output_text"
    return json.dumps({
        "timestamp": "2026-08-19T10:00:00.000Z",
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": role,
            "content": [{"type": block_type, "text": text}],
        },
    })


def _run_stop_hook(transcript_path, config_path, session_id="codex-sess"):
    payload = json.dumps({
        "session_id": session_id,
        "turn_id": "turn-1",
        "transcript_path": transcript_path,
        "hook_event_name": "Stop",
    })
    return subprocess.run(
        [sys.executable, os.path.join(PLUGIN_BIN, "engram-hook"), "Stop"],
        input=payload, capture_output=True, text=True,
        env={**os.environ, "ENGRAM_CONFIG": config_path},
    )


# ── engram-hook: Codex rollout transcript parsing ─────────

def test_stop_hook_parses_codex_rollout():
    """A Codex rollout transcript yields the last user prompt + assistant
    reply; event_msg duplicates, reasoning, and function calls are ignored."""
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        config = _write_config(tmpdir, vault)

        transcript = os.path.join(tmpdir, "rollout.jsonl")
        user_prompt = "Please investigate why the deployment pipeline is stuck on stage two."
        reply = "The pipeline is stuck because the approval gate has no reviewer assigned to it."
        lines = [
            json.dumps({"type": "session_meta", "payload": {"id": "x"}}),
            _codex_message("user", "<environment_context>\n<cwd>/tmp</cwd>"),
            _codex_message("user", "old prompt from a previous turn, long enough to count"),
            _codex_message("assistant", "old answer from a previous turn"),
            # duplicate stream that must NOT be double-counted
            json.dumps({"type": "event_msg",
                        "payload": {"type": "user_message", "message": user_prompt}}),
            _codex_message("user", user_prompt),
            json.dumps({"type": "response_item", "payload": {"type": "reasoning"}}),
            json.dumps({"type": "response_item",
                        "payload": {"type": "function_call", "name": "exec_command",
                                    "arguments": "{\"cmd\": \"ls\"}"}}),
            json.dumps({"type": "event_msg",
                        "payload": {"type": "agent_message", "message": reply}}),
            _codex_message("assistant", reply),
        ]
        with open(transcript, "w") as f:
            f.write("\n".join(lines) + "\n")

        result = _run_stop_hook(transcript, config)
        assert result.returncode == 0, result.stderr

        with open(os.path.join(vault, "_meta", "pending.jsonl")) as f:
            records = [json.loads(l) for l in f]
        assert len(records) == 1
        turn = records[0]["turn_text"]
        assert turn == f"{user_prompt}\n\n{reply}", turn
        print("  ✅ codex rollout: last turn extracted, duplicates/noise ignored")


def test_stop_hook_codex_skips_injected_user_items():
    """Injected user response_items (<environment_context>, <user_instructions>)
    never terminate the backwards walk or leak into the turn text."""
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        config = _write_config(tmpdir, vault)

        transcript = os.path.join(tmpdir, "rollout.jsonl")
        user_prompt = "What changed in the retry logic since the last release of the service?"
        reply = "Retries now use exponential backoff with jitter instead of a fixed delay."
        lines = [
            _codex_message("user", user_prompt),
            _codex_message("user", "<user_instructions>always answer briefly</user_instructions>"),
            _codex_message("user", "<environment_context><cwd>/repo</cwd></environment_context>"),
            _codex_message("assistant", reply),
        ]
        with open(transcript, "w") as f:
            f.write("\n".join(lines) + "\n")

        result = _run_stop_hook(transcript, config)
        assert result.returncode == 0, result.stderr

        with open(os.path.join(vault, "_meta", "pending.jsonl")) as f:
            turn = json.loads(f.readline())["turn_text"]
        assert "<environment_context>" not in turn
        assert "<user_instructions>" not in turn
        assert turn.startswith(user_prompt)
        print("  ✅ codex rollout: injected user items excluded from the turn")


def test_stop_hook_claude_format_regression():
    """The original Claude Code transcript format still parses identically."""
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        config = _write_config(tmpdir, vault)

        transcript = os.path.join(tmpdir, "session.jsonl")
        user_prompt = "How should we structure the migration scripts for the vault schema?"
        reply = "Keep one migration per file and record applied versions in _meta/schema."
        lines = [
            json.dumps({"type": "user", "message": {
                "content": [{"type": "text", "text": user_prompt}]}}),
            json.dumps({"type": "assistant", "message": {
                "content": [{"type": "tool_use", "id": "t1", "input": {}},
                            {"type": "text", "text": reply}]}}),
        ]
        with open(transcript, "w") as f:
            f.write("\n".join(lines) + "\n")

        result = _run_stop_hook(transcript, config)
        assert result.returncode == 0, result.stderr

        with open(os.path.join(vault, "_meta", "pending.jsonl")) as f:
            turn = json.loads(f.readline())["turn_text"]
        assert turn == f"{user_prompt}\n\n{reply}"
        print("  ✅ claude transcript format: unchanged behavior")


# ── engram-pretool: Codex payload + output wire ───────────

def _seed_graph(vault, config, name="DEPLOYMENT_PIPELINE"):
    extraction = {
        "date": "2026-08-19",
        "entities": [{"name": name, "entity_type": "PROJECT",
                      "description": "CI/CD pipeline entity for pretool tests",
                      "confidence": "EXTRACTED"}],
        "relations": [],
        "daily_summary": "seed",
    }
    result = subprocess.run(
        CLI + ["replay", "--stdin", "--vault", vault],
        input=json.dumps(extraction), capture_output=True, text=True,
        env={**os.environ, "ENGRAM_CONFIG": config},
    )
    assert result.returncode == 0, result.stderr


def _run_pretool(payload, config):
    return subprocess.run(
        [sys.executable, os.path.join(PLUGIN_BIN, "engram-pretool")],
        input=json.dumps(payload), capture_output=True, text=True,
        env={**os.environ, "ENGRAM_CONFIG": config},
    )


def test_pretool_codex_exec_command_emits_hook_specific_output():
    """Codex payload (turn_id + exec_command) matching an entity emits the
    hookSpecificOutput wire that Codex's strict parser accepts."""
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        config = _write_config(tmpdir, vault)
        _seed_graph(vault, config)

        result = _run_pretool({
            "session_id": "s", "turn_id": "t",
            "tool_name": "exec_command",
            "tool_input": {"cmd": "grep -r deployment_pipeline src/"},
        }, config)
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        specific = out["hookSpecificOutput"]
        assert specific["hookEventName"] == "PreToolUse"
        assert "DEPLOYMENT_PIPELINE" in specific["additionalContext"]
        assert "message" not in out
        print("  ✅ pretool: codex exec_command → hookSpecificOutput wire")


def test_pretool_claude_output_format_regression():
    """Claude Code payload (no turn_id) keeps the legacy {'message': ...}
    output untouched."""
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        config = _write_config(tmpdir, vault)
        _seed_graph(vault, config)

        result = _run_pretool({
            "session_id": "s",
            "tool_name": "Grep",
            "tool_input": {"pattern": "deployment_pipeline"},
        }, config)
        assert result.returncode == 0, result.stderr
        out = json.loads(result.stdout)
        assert "message" in out and "hookSpecificOutput" not in out
        assert "DEPLOYMENT_PIPELINE" in out["message"]
        print("  ✅ pretool: claude payload → legacy message format unchanged")


# ── engram codex-setup ────────────────────────────────────

def _run_codex_setup(vault, config, codex_home):
    result = subprocess.run(
        CLI + ["codex-setup", "--vault", vault],
        capture_output=True, text=True,
        env={**os.environ, "ENGRAM_CONFIG": config, "CODEX_HOME": codex_home},
    )
    assert result.returncode == 0, f"stderr: {result.stderr}\nstdout: {result.stdout}"
    return json.loads(result.stdout)


def test_codex_setup_writes_hooks_and_prompts():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        config = _write_config(tmpdir, vault)
        codex_home = os.path.join(tmpdir, "codex-home")

        summary = _run_codex_setup(vault, config, codex_home)
        assert summary["status"] == "ok"

        with open(os.path.join(codex_home, "hooks.json")) as f:
            doc = json.load(f)
        hooks = doc["hooks"]
        for event in ("UserPromptSubmit", "Stop", "SessionEnd", "PreToolUse"):
            groups = hooks[event]
            commands = [h["command"] for g in groups for h in g["hooks"]]
            assert any("engram-" in c for c in commands), (event, commands)
            # absolute paths only — no ${CLAUDE_PLUGIN_ROOT}
            assert all(os.path.isabs(c.split()[0]) for c in commands), commands
        assert hooks["PreToolUse"][0]["matcher"] == "exec_command"
        assert hooks["Stop"][0]["hooks"][0]["command"].endswith("engram-hook Stop")

        prompts_dir = os.path.join(codex_home, "prompts")
        assert os.path.isfile(os.path.join(prompts_dir, "engram.md"))
        assert os.path.isfile(os.path.join(prompts_dir, "engram-query.md"))
        assert summary["capture_enabled"] is True
        print("  ✅ codex-setup: hooks.json + prompts installed")


def test_codex_setup_idempotent_and_preserves_foreign_hooks():
    """Re-running setup never duplicates engram entries, and hooks the user
    registered for other tools survive untouched."""
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        config = _write_config(tmpdir, vault)
        codex_home = os.path.join(tmpdir, "codex-home")
        os.makedirs(codex_home)

        foreign = {"matcher": None, "hooks": [
            {"type": "command", "command": "/usr/local/bin/my-linter", "timeout": 5}]}
        with open(os.path.join(codex_home, "hooks.json"), "w") as f:
            json.dump({"description": "mine", "hooks": {"Stop": [foreign]}}, f)

        _run_codex_setup(vault, config, codex_home)
        _run_codex_setup(vault, config, codex_home)

        with open(os.path.join(codex_home, "hooks.json")) as f:
            doc = json.load(f)
        stop_commands = [h["command"] for g in doc["hooks"]["Stop"] for h in g["hooks"]]
        assert stop_commands.count("/usr/local/bin/my-linter") == 1
        assert len([c for c in stop_commands if "engram-hook" in c]) == 1
        assert doc["description"] == "mine"
        print("  ✅ codex-setup: idempotent, foreign hooks preserved")


def test_codex_setup_refuses_corrupt_hooks_json():
    """A hooks.json that fails to parse must abort setup, not be clobbered."""
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        config = _write_config(tmpdir, vault)
        codex_home = os.path.join(tmpdir, "codex-home")
        os.makedirs(codex_home)
        hooks_path = os.path.join(codex_home, "hooks.json")
        with open(hooks_path, "w") as f:
            f.write("{not json at all")

        result = subprocess.run(
            CLI + ["codex-setup", "--vault", vault],
            capture_output=True, text=True,
            env={**os.environ, "ENGRAM_CONFIG": config, "CODEX_HOME": codex_home},
        )
        assert result.returncode == 1
        assert json.loads(result.stdout)["status"] == "error"
        with open(hooks_path) as f:
            assert f.read() == "{not json at all", "corrupt file must be left as-is"
        print("  ✅ codex-setup: corrupt hooks.json aborts without clobbering")


if __name__ == "__main__":
    test_stop_hook_parses_codex_rollout()
    test_stop_hook_codex_skips_injected_user_items()
    test_stop_hook_claude_format_regression()
    test_pretool_codex_exec_command_emits_hook_specific_output()
    test_pretool_claude_output_format_regression()
    test_codex_setup_writes_hooks_and_prompts()
    test_codex_setup_idempotent_and_preserves_foreign_hooks()
    test_codex_setup_refuses_corrupt_hooks_json()
    print("\n🎉 All codex compatibility tests passed!")
