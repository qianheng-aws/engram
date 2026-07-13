"""Tests for Task 1: Stop hook upgrade to pending.jsonl queue."""

import json
import os
import subprocess
import sys
import tempfile


def _make_vault(tmpdir):
    vault = os.path.join(tmpdir, "vault")
    os.makedirs(os.path.join(vault, "_meta"), exist_ok=True)
    return vault


def test_stop_hook_appends_to_pending_jsonl():
    """Stop hook appends exactly one well-formed JSON line to pending.jsonl."""
    hook_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "plugin", "bin", "engram-hook")

    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        # Enable hook
        open(os.path.join(vault, "_meta", "hook-enabled"), "w").close()

        # Write config pointing to vault
        config_path = os.path.join(tmpdir, "config.json")
        with open(config_path, "w") as f:
            json.dump({"vault": vault}, f)

        payload = json.dumps({
            "session_id": "test-session-abc",
            "last_assistant_message": "This is a substantive assistant message that exceeds 50 characters.",
            "stop_reason": "user",
        })

        result = subprocess.run(
            [sys.executable, hook_path, "Stop"],
            input=payload, capture_output=True, text=True,
            env={**os.environ, "ENGRAM_CONFIG": config_path},
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"

        # Check: pending.jsonl exists and has exactly one line
        pending_path = os.path.join(vault, "_meta", "pending.jsonl")
        assert os.path.exists(pending_path), "pending.jsonl not created"
        with open(pending_path) as f:
            lines = f.readlines()
        assert len(lines) == 1, f"Expected 1 line, got {len(lines)}"

        # Parse the line and verify structure
        record = json.loads(lines[0])
        assert "session_id" in record
        assert "timestamp" in record
        assert "turn_text" in record
        assert record["session_id"] == "test-session-abc"
        print("  ✅ stop hook appends well-formed JSON line to pending.jsonl")


def test_stop_hook_skips_trivial_turns():
    """Stop hook skips turns with <50 chars last message: no line appended."""
    hook_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "plugin", "bin", "engram-hook")

    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        open(os.path.join(vault, "_meta", "hook-enabled"), "w").close()

        config_path = os.path.join(tmpdir, "config.json")
        with open(config_path, "w") as f:
            json.dump({"vault": vault}, f)

        # Short message (< 50 chars)
        payload = json.dumps({
            "session_id": "short-session",
            "last_assistant_message": "Short",
            "stop_reason": "user",
        })

        result = subprocess.run(
            [sys.executable, hook_path, "Stop"],
            input=payload, capture_output=True, text=True,
            env={**os.environ, "ENGRAM_CONFIG": config_path},
        )
        assert result.returncode == 0

        # Check: pending.jsonl should NOT exist
        pending_path = os.path.join(vault, "_meta", "pending.jsonl")
        assert not os.path.exists(pending_path), "pending.jsonl should not exist for trivial turns"
        print("  ✅ stop hook skips trivial turns (<50 chars)")


def test_stop_hook_respects_kill_switch():
    """Stop hook respects kill switch: no hook-enabled file → no write."""
    hook_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "plugin", "bin", "engram-hook")

    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        # Do NOT create hook-enabled file

        config_path = os.path.join(tmpdir, "config.json")
        with open(config_path, "w") as f:
            json.dump({"vault": vault}, f)

        payload = json.dumps({
            "session_id": "disabled-session",
            "last_assistant_message": "This message is long enough to pass the 50 char threshold.",
            "stop_reason": "user",
        })

        result = subprocess.run(
            [sys.executable, hook_path, "Stop"],
            input=payload, capture_output=True, text=True,
            env={**os.environ, "ENGRAM_CONFIG": config_path},
        )
        assert result.returncode == 0

        # Check: pending.jsonl should NOT exist
        pending_path = os.path.join(vault, "_meta", "pending.jsonl")
        assert not os.path.exists(pending_path), "pending.jsonl should not exist when hook disabled"
        print("  ✅ stop hook respects kill switch")


def test_stop_hook_fail_silent_on_malformed_stdin():
    """Stop hook never raises / exit code 0 even on malformed stdin."""
    hook_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "plugin", "bin", "engram-hook")

    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        open(os.path.join(vault, "_meta", "hook-enabled"), "w").close()

        config_path = os.path.join(tmpdir, "config.json")
        with open(config_path, "w") as f:
            json.dump({"vault": vault}, f)

        # Malformed JSON
        payload = "{ this is not valid json }"

        result = subprocess.run(
            [sys.executable, hook_path, "Stop"],
            input=payload, capture_output=True, text=True,
            env={**os.environ, "ENGRAM_CONFIG": config_path},
        )
        assert result.returncode == 0, "Hook should exit 0 even on malformed stdin"
        print("  ✅ stop hook fail-silent on malformed stdin")


def test_stop_hook_full_turn_capture():
    """Stop hook captures full turn (user + assistant) from transcript_path."""
    hook_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "plugin", "bin", "engram-hook")

    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        open(os.path.join(vault, "_meta", "hook-enabled"), "w").close()

        config_path = os.path.join(tmpdir, "config.json")
        with open(config_path, "w") as f:
            json.dump({"vault": vault}, f)

        # Create a fake transcript file with a user + assistant turn
        transcript_path = os.path.join(tmpdir, "transcript.jsonl")
        assistant_msg = "Assistant response to the question that is long enough to pass the 50 character threshold."
        with open(transcript_path, "w") as f:
            f.write(json.dumps({"type": "system", "text": "System message"}) + "\n")
            f.write(json.dumps({"type": "user", "text": "User question about testing"}) + "\n")
            f.write(json.dumps({"type": "assistant", "text": assistant_msg}) + "\n")

        payload = json.dumps({
            "session_id": "full-turn-session",
            "last_assistant_message": assistant_msg,
            "transcript_path": transcript_path,
            "stop_reason": "user",
        })

        result = subprocess.run(
            [sys.executable, hook_path, "Stop"],
            input=payload, capture_output=True, text=True,
            env={**os.environ, "ENGRAM_CONFIG": config_path},
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"

        pending_path = os.path.join(vault, "_meta", "pending.jsonl")
        assert os.path.exists(pending_path)
        with open(pending_path) as f:
            lines = f.readlines()
        assert len(lines) == 1

        record = json.loads(lines[0])
        turn_text = record["turn_text"]
        # Should contain both user and assistant messages
        assert "User question about testing" in turn_text
        assert "Assistant response to the question that is long enough" in turn_text
        print("  ✅ stop hook captures full turn from transcript")


def test_stop_hook_fallback_to_last_message():
    """Stop hook falls back to last_assistant_message if transcript read fails."""
    hook_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "plugin", "bin", "engram-hook")

    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        open(os.path.join(vault, "_meta", "hook-enabled"), "w").close()

        config_path = os.path.join(tmpdir, "config.json")
        with open(config_path, "w") as f:
            json.dump({"vault": vault}, f)

        # transcript_path points to nonexistent file
        payload = json.dumps({
            "session_id": "fallback-session",
            "last_assistant_message": "Fallback assistant message that is long enough to not be skipped.",
            "transcript_path": "/nonexistent/transcript.jsonl",
            "stop_reason": "user",
        })

        result = subprocess.run(
            [sys.executable, hook_path, "Stop"],
            input=payload, capture_output=True, text=True,
            env={**os.environ, "ENGRAM_CONFIG": config_path},
        )
        assert result.returncode == 0

        pending_path = os.path.join(vault, "_meta", "pending.jsonl")
        assert os.path.exists(pending_path)
        with open(pending_path) as f:
            lines = f.readlines()
        assert len(lines) == 1

        record = json.loads(lines[0])
        assert record["turn_text"] == "Fallback assistant message that is long enough to not be skipped."
        print("  ✅ stop hook falls back to last_assistant_message on transcript read failure")


def test_stop_hook_multiple_calls_append():
    """Multiple stop hook calls append multiple lines to pending.jsonl."""
    hook_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "plugin", "bin", "engram-hook")

    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        open(os.path.join(vault, "_meta", "hook-enabled"), "w").close()

        config_path = os.path.join(tmpdir, "config.json")
        with open(config_path, "w") as f:
            json.dump({"vault": vault}, f)

        # First call
        payload1 = json.dumps({
            "session_id": "session-1",
            "last_assistant_message": "First message that is long enough for testing purposes here.",
            "stop_reason": "user",
        })
        subprocess.run(
            [sys.executable, hook_path, "Stop"],
            input=payload1, capture_output=True, text=True,
            env={**os.environ, "ENGRAM_CONFIG": config_path},
        )

        # Second call
        payload2 = json.dumps({
            "session_id": "session-2",
            "last_assistant_message": "Second message that is also long enough for testing.",
            "stop_reason": "user",
        })
        subprocess.run(
            [sys.executable, hook_path, "Stop"],
            input=payload2, capture_output=True, text=True,
            env={**os.environ, "ENGRAM_CONFIG": config_path},
        )

        pending_path = os.path.join(vault, "_meta", "pending.jsonl")
        with open(pending_path) as f:
            lines = f.readlines()
        assert len(lines) == 2, f"Expected 2 lines, got {len(lines)}"

        record1 = json.loads(lines[0])
        record2 = json.loads(lines[1])
        assert record1["session_id"] == "session-1"
        assert record2["session_id"] == "session-2"
        print("  ✅ stop hook appends multiple lines correctly")


def test_stop_hook_no_queue_dir_created():
    """Stop hook should NOT create queue/ directory (pending.jsonl replaces it)."""
    hook_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "plugin", "bin", "engram-hook")

    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        open(os.path.join(vault, "_meta", "hook-enabled"), "w").close()

        config_path = os.path.join(tmpdir, "config.json")
        with open(config_path, "w") as f:
            json.dump({"vault": vault}, f)

        payload = json.dumps({
            "session_id": "no-queue-session",
            "last_assistant_message": "Message long enough to trigger hook processing and write to pending.",
            "stop_reason": "user",
        })

        result = subprocess.run(
            [sys.executable, hook_path, "Stop"],
            input=payload, capture_output=True, text=True,
            env={**os.environ, "ENGRAM_CONFIG": config_path},
        )
        assert result.returncode == 0

        # Check: queue/ should NOT exist
        queue_dir = os.path.join(vault, "_meta", "queue")
        assert not os.path.exists(queue_dir), "queue/ directory should not be created"

        # But pending.jsonl should exist
        pending_path = os.path.join(vault, "_meta", "pending.jsonl")
        assert os.path.exists(pending_path), "pending.jsonl should exist"
        print("  ✅ stop hook does not create queue/ directory")


def test_stop_hook_real_transcript_format():
    """Stop hook extracts text from the real Claude Code transcript format.

    Real transcripts nest text under message.content: a plain string for user
    prompts, or a list of blocks for assistant entries where text lives in
    {"type": "text", "text": ...} blocks. tool_use / tool_result payloads must
    not leak into turn_text, and the backward scan must not stop at a
    tool_result entry (which also has type == "user" in real transcripts).
    """
    hook_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "plugin", "bin", "engram-hook")

    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        open(os.path.join(vault, "_meta", "hook-enabled"), "w").close()

        config_path = os.path.join(tmpdir, "config.json")
        with open(config_path, "w") as f:
            json.dump({"vault": vault}, f)

        assistant_text = "Here is the final answer, explained in enough detail to pass the length threshold."
        transcript_path = os.path.join(tmpdir, "transcript.jsonl")
        with open(transcript_path, "w") as f:
            # Previous turn (must NOT be captured)
            f.write(json.dumps({
                "type": "user",
                "message": {"role": "user", "content": "An earlier prompt from a previous turn"},
            }) + "\n")
            f.write(json.dumps({
                "type": "assistant",
                "message": {"role": "assistant", "content": [
                    {"type": "text", "text": "An earlier answer from a previous turn"},
                ]},
            }) + "\n")
            # Current turn: user prompt (string content)
            f.write(json.dumps({
                "type": "user",
                "message": {"role": "user", "content": "Please explain the nested transcript format"},
            }) + "\n")
            # Assistant entry with text + tool_use blocks
            f.write(json.dumps({
                "type": "assistant",
                "message": {"role": "assistant", "content": [
                    {"type": "text", "text": "Let me look that up."},
                    {"type": "tool_use", "id": "toolu_01", "name": "Read",
                     "input": {"file_path": "/secret/tool-use-payload.txt"}},
                ]},
            }) + "\n")
            # Tool result entry (type "user" in real transcripts)
            f.write(json.dumps({
                "type": "user",
                "message": {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "toolu_01",
                     "content": "secret tool result payload"},
                ]},
            }) + "\n")
            # Final assistant entry with the answer
            f.write(json.dumps({
                "type": "assistant",
                "message": {"role": "assistant", "content": [
                    {"type": "text", "text": assistant_text},
                ]},
            }) + "\n")

        payload = json.dumps({
            "session_id": "real-format-session",
            "last_assistant_message": assistant_text,
            "transcript_path": transcript_path,
            "stop_reason": "user",
        })

        result = subprocess.run(
            [sys.executable, hook_path, "Stop"],
            input=payload, capture_output=True, text=True,
            env={**os.environ, "ENGRAM_CONFIG": config_path},
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"

        pending_path = os.path.join(vault, "_meta", "pending.jsonl")
        assert os.path.exists(pending_path), "pending.jsonl not created"
        with open(pending_path) as f:
            lines = f.readlines()
        assert len(lines) == 1

        record = json.loads(lines[0])
        turn_text = record["turn_text"]
        # User prompt (string content) and assistant text (block content) captured
        assert "Please explain the nested transcript format" in turn_text
        assert "Let me look that up." in turn_text
        assert assistant_text in turn_text
        # tool_use / tool_result payloads must NOT leak into turn_text
        assert "tool-use-payload" not in turn_text
        assert "secret tool result payload" not in turn_text
        # Previous turn must not be captured
        assert "previous turn" not in turn_text
        print("  ✅ stop hook extracts full turn from real nested transcript format")


def test_stop_hook_real_payload_without_last_assistant_message():
    """Real Stop payloads carry NO last_assistant_message — the hook must
    derive the turn from transcript_path alone and still append."""
    hook_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "plugin", "bin", "engram-hook")

    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        open(os.path.join(vault, "_meta", "hook-enabled"), "w").close()

        config_path = os.path.join(tmpdir, "config.json")
        with open(config_path, "w") as f:
            json.dump({"vault": vault}, f)

        transcript_path = os.path.join(tmpdir, "transcript.jsonl")
        with open(transcript_path, "w") as f:
            f.write(json.dumps({
                "type": "user",
                "message": {"role": "user", "content": "How does the watermark drain work?"},
            }) + "\n")
            f.write(json.dumps({
                "type": "assistant",
                "message": {"role": "assistant", "content": [
                    {"type": "text",
                     "text": "The worker seeks to the stored offset and reads complete lines only."},
                ]},
            }) + "\n")

        # Shape of a REAL Stop payload: no last_assistant_message field
        payload = json.dumps({
            "session_id": "real-payload-session",
            "transcript_path": transcript_path,
            "cwd": tmpdir,
            "hook_event_name": "Stop",
            "stop_hook_active": False,
        })

        result = subprocess.run(
            [sys.executable, hook_path, "Stop"],
            input=payload, capture_output=True, text=True,
            env={**os.environ, "ENGRAM_CONFIG": config_path},
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"

        pending_path = os.path.join(vault, "_meta", "pending.jsonl")
        assert os.path.exists(pending_path), \
            "pending.jsonl not created for a real payload without last_assistant_message"
        with open(pending_path) as f:
            lines = f.readlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert "How does the watermark drain work?" in record["turn_text"]
        assert "seeks to the stored offset" in record["turn_text"]
        print("  ✅ stop hook works on real payload shape (no last_assistant_message)")


def test_stop_hook_no_transcript_and_no_last_message():
    """Neither transcript_path nor last_assistant_message → no line, exit 0."""
    hook_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "plugin", "bin", "engram-hook")

    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        open(os.path.join(vault, "_meta", "hook-enabled"), "w").close()

        config_path = os.path.join(tmpdir, "config.json")
        with open(config_path, "w") as f:
            json.dump({"vault": vault}, f)

        payload = json.dumps({
            "session_id": "empty-session",
            "hook_event_name": "Stop",
            "stop_hook_active": False,
        })

        result = subprocess.run(
            [sys.executable, hook_path, "Stop"],
            input=payload, capture_output=True, text=True,
            env={**os.environ, "ENGRAM_CONFIG": config_path},
        )
        assert result.returncode == 0
        pending_path = os.path.join(vault, "_meta", "pending.jsonl")
        assert not os.path.exists(pending_path), \
            "pending.jsonl should not exist without transcript or last message"
        print("  ✅ stop hook no-ops cleanly with neither transcript nor last message")


def test_stop_hook_trivial_transcript_turn_skipped():
    """Transcript-derived turn shorter than 50 chars → skip (substantiveness gate)."""
    hook_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "plugin", "bin", "engram-hook")

    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        open(os.path.join(vault, "_meta", "hook-enabled"), "w").close()

        config_path = os.path.join(tmpdir, "config.json")
        with open(config_path, "w") as f:
            json.dump({"vault": vault}, f)

        transcript_path = os.path.join(tmpdir, "transcript.jsonl")
        with open(transcript_path, "w") as f:
            f.write(json.dumps({"type": "user", "text": "hi"}) + "\n")
            f.write(json.dumps({"type": "assistant", "text": "hello"}) + "\n")

        payload = json.dumps({
            "session_id": "trivial-transcript-session",
            "transcript_path": transcript_path,
            "hook_event_name": "Stop",
        })

        result = subprocess.run(
            [sys.executable, hook_path, "Stop"],
            input=payload, capture_output=True, text=True,
            env={**os.environ, "ENGRAM_CONFIG": config_path},
        )
        assert result.returncode == 0
        pending_path = os.path.join(vault, "_meta", "pending.jsonl")
        assert not os.path.exists(pending_path), \
            "pending.jsonl should not exist for a trivial transcript turn"
        print("  ✅ stop hook skips trivial transcript-derived turns (<50 chars)")


def test_stop_hook_turn_text_capped():
    """Stop hook caps turn_text at a reasonable limit (e.g., 20,000 chars)."""
    hook_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "plugin", "bin", "engram-hook")

    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        open(os.path.join(vault, "_meta", "hook-enabled"), "w").close()

        config_path = os.path.join(tmpdir, "config.json")
        with open(config_path, "w") as f:
            json.dump({"vault": vault}, f)

        # Create a transcript with a very long assistant message
        transcript_path = os.path.join(tmpdir, "transcript.jsonl")
        long_text = "x" * 25000  # Exceeds the 20k cap
        with open(transcript_path, "w") as f:
            f.write(json.dumps({"type": "user", "text": "User question"}) + "\n")
            f.write(json.dumps({"type": "assistant", "text": long_text}) + "\n")

        payload = json.dumps({
            "session_id": "long-turn-session",
            "last_assistant_message": long_text,
            "transcript_path": transcript_path,
            "stop_reason": "user",
        })

        result = subprocess.run(
            [sys.executable, hook_path, "Stop"],
            input=payload, capture_output=True, text=True,
            env={**os.environ, "ENGRAM_CONFIG": config_path},
        )
        assert result.returncode == 0

        pending_path = os.path.join(vault, "_meta", "pending.jsonl")
        with open(pending_path) as f:
            lines = f.readlines()
        record = json.loads(lines[0])
        turn_text = record["turn_text"]
        # Should be capped at the 20,000 char limit (marker included)
        assert len(turn_text) <= 20000, f"turn_text too long: {len(turn_text)} chars"
        # Should contain truncation marker
        assert "[...truncated]" in turn_text
        print("  ✅ stop hook caps turn_text at reasonable limit")


if __name__ == "__main__":
    print("Testing stop hook upgrade (pending.jsonl)...")
    test_stop_hook_appends_to_pending_jsonl()
    test_stop_hook_skips_trivial_turns()
    test_stop_hook_respects_kill_switch()
    test_stop_hook_fail_silent_on_malformed_stdin()
    test_stop_hook_full_turn_capture()
    test_stop_hook_fallback_to_last_message()
    test_stop_hook_multiple_calls_append()
    test_stop_hook_no_queue_dir_created()
    test_stop_hook_real_transcript_format()
    test_stop_hook_real_payload_without_last_assistant_message()
    test_stop_hook_no_transcript_and_no_last_message()
    test_stop_hook_trivial_transcript_turn_skipped()
    test_stop_hook_turn_text_capped()
    print("\n🎉 All stop hook tests passed!")
