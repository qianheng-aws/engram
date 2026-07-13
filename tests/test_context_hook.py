"""Tests for Task 4: UserPromptSubmit hook (engram-context)."""

import json
import os
import subprocess
import sys
import tempfile


def _make_vault(tmpdir):
    vault = os.path.join(tmpdir, "vault")
    os.makedirs(os.path.join(vault, "_meta"), exist_ok=True)
    return vault


def test_context_hook_no_hook_enabled_flag():
    """Hook respects kill switch: no hook-enabled flag → empty stdout, exit 0."""
    hook_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "plugin", "bin", "engram-context")

    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        # Do NOT create hook-enabled flag

        config_path = os.path.join(tmpdir, "config.json")
        with open(config_path, "w") as f:
            json.dump({"vault": vault}, f)

        payload = json.dumps({"prompt": "Tell me about testing"})

        result = subprocess.run(
            [sys.executable, hook_path],
            input=payload, capture_output=True, text=True,
            env={**os.environ, "ENGRAM_CONFIG": config_path},
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert result.stdout == "", f"Expected empty stdout, got: {result.stdout}"
        print("  ✅ context hook respects kill switch")


def test_context_hook_empty_vault():
    """Empty vault (no cache, no engram on PATH) → empty stdout, exit 0."""
    hook_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "plugin", "bin", "engram-context")

    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        open(os.path.join(vault, "_meta", "hook-enabled"), "w").close()

        config_path = os.path.join(tmpdir, "config.json")
        with open(config_path, "w") as f:
            json.dump({"vault": vault}, f)

        payload = json.dumps({"prompt": "Tell me about testing"})

        # Use a clean PATH without engram binary
        env = {**os.environ, "ENGRAM_CONFIG": config_path, "PATH": "/usr/bin:/bin"}

        result = subprocess.run(
            [sys.executable, hook_path],
            input=payload, capture_output=True, text=True,
            env=env,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert result.stdout == "", f"Expected empty stdout, got: {result.stdout}"
        print("  ✅ context hook handles empty vault gracefully")


def test_context_hook_malformed_stdin():
    """Hook is fail-silent: malformed stdin → empty stdout, exit 0."""
    hook_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "plugin", "bin", "engram-context")

    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        open(os.path.join(vault, "_meta", "hook-enabled"), "w").close()

        config_path = os.path.join(tmpdir, "config.json")
        with open(config_path, "w") as f:
            json.dump({"vault": vault}, f)

        payload = "{ this is not valid json }"

        result = subprocess.run(
            [sys.executable, hook_path],
            input=payload, capture_output=True, text=True,
            env={**os.environ, "ENGRAM_CONFIG": config_path},
        )
        assert result.returncode == 0, "Hook should exit 0 even on malformed stdin"
        assert result.stdout == "", "Hook should print nothing on error"
        print("  ✅ context hook fail-silent on malformed stdin")


def test_context_hook_cache_file_only():
    """Hook reads cache file directly (fast path) and emits Memory Context block."""
    hook_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "plugin", "bin", "engram-context")

    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        open(os.path.join(vault, "_meta", "hook-enabled"), "w").close()

        # Write a fake cache file
        cache_content = "**Top Entities:** TEST_ENTITY (concept)\n\n**Recent Activity:** 2 new entities added."
        cache_path = os.path.join(vault, "_meta", "context-cache.md")
        with open(cache_path, "w") as f:
            f.write(cache_content)

        config_path = os.path.join(tmpdir, "config.json")
        with open(config_path, "w") as f:
            json.dump({"vault": vault}, f)

        payload = json.dumps({"prompt": "Tell me about testing"})

        result = subprocess.run(
            [sys.executable, hook_path],
            input=payload, capture_output=True, text=True,
            env={**os.environ, "ENGRAM_CONFIG": config_path},
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "## Memory Context" in result.stdout, "Expected Memory Context header"
        assert "TEST_ENTITY" in result.stdout, "Expected cache content"
        print("  ✅ context hook reads cache file (fast path)")


def test_context_hook_over_budget_truncation():
    """Hook truncates output with … marker when over budget."""
    hook_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "plugin", "bin", "engram-context")

    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        open(os.path.join(vault, "_meta", "hook-enabled"), "w").close()

        # Write a large cache file (over default 2000 char budget)
        cache_content = "X" * 2500
        cache_path = os.path.join(vault, "_meta", "context-cache.md")
        with open(cache_path, "w") as f:
            f.write(cache_content)

        config_path = os.path.join(tmpdir, "config.json")
        with open(config_path, "w") as f:
            json.dump({"vault": vault, "context_max_chars": 500}, f)

        payload = json.dumps({"prompt": "Tell me about testing"})

        result = subprocess.run(
            [sys.executable, hook_path],
            input=payload, capture_output=True, text=True,
            env={**os.environ, "ENGRAM_CONFIG": config_path},
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert len(result.stdout) <= 550, f"Output too long: {len(result.stdout)} chars"
        assert "…" in result.stdout, "Expected truncation marker"
        print("  ✅ context hook truncates over-budget output")


def test_context_hook_prompt_matched_subgraph():
    """Hook extracts keywords and queries for matched entities."""
    hook_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "plugin", "bin", "engram-context")

    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        open(os.path.join(vault, "_meta", "hook-enabled"), "w").close()

        config_path = os.path.join(tmpdir, "config.json")
        with open(config_path, "w") as f:
            json.dump({"vault": vault}, f)

        # Create a fake engram script that returns canned query output
        fake_engram_dir = os.path.join(tmpdir, "bin")
        os.makedirs(fake_engram_dir)
        fake_engram = os.path.join(fake_engram_dir, "engram")
        with open(fake_engram, "w") as f:
            f.write(f"""#!/bin/bash
cat << 'ENDJSON'
{{
  "question": "testing deployment",
  "matched_entities": ["DEPLOYMENT_PIPELINE", "TEST_FRAMEWORK"],
  "expanded_entities": [
    {{"name": "CI_CD", "hops": 1, "max_weight": 0.8}}
  ],
  "context": "## DEPLOYMENT_PIPELINE\\nAutomated deployment system\\n### Relations\\n  - [[CI_CD]]: Continuous integration (w:0.8)",
  "community_context": [],
  "message": "Use context + community_context to answer."
}}
ENDJSON
""")
        os.chmod(fake_engram, 0o755)

        payload = json.dumps({"prompt": "Tell me about testing and deployment"})

        env = {**os.environ, "ENGRAM_CONFIG": config_path, "PATH": f"{fake_engram_dir}:{os.environ.get('PATH', '')}"}

        result = subprocess.run(
            [sys.executable, hook_path],
            input=payload, capture_output=True, text=True,
            env=env,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "## Memory Context" in result.stdout, "Expected Memory Context header"
        assert "DEPLOYMENT_PIPELINE" in result.stdout, "Expected matched entity"
        assert "CI_CD" in result.stdout, "Expected expanded entity"
        print("  ✅ context hook queries for prompt-matched entities")


def test_context_hook_budget_preserves_query_results():
    """When over budget, hook truncates digest first to preserve query results."""
    hook_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "plugin", "bin", "engram-context")

    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        open(os.path.join(vault, "_meta", "hook-enabled"), "w").close()

        # Write a large cache file
        cache_content = "DIGEST_CONTENT " * 300  # ~4500 chars
        cache_path = os.path.join(vault, "_meta", "context-cache.md")
        with open(cache_path, "w") as f:
            f.write(cache_content)

        config_path = os.path.join(tmpdir, "config.json")
        with open(config_path, "w") as f:
            json.dump({"vault": vault, "context_max_chars": 1000}, f)

        # Create fake engram that returns query results
        fake_engram_dir = os.path.join(tmpdir, "bin")
        os.makedirs(fake_engram_dir)
        fake_engram = os.path.join(fake_engram_dir, "engram")
        with open(fake_engram, "w") as f:
            f.write(f"""#!/bin/bash
cat << 'ENDJSON'
{{
  "question": "important keyword",
  "matched_entities": ["IMPORTANT_ENTITY"],
  "expanded_entities": [],
  "context": "## IMPORTANT_ENTITY\\nThis is the most relevant content for the prompt",
  "community_context": [],
  "message": "Use context + community_context to answer."
}}
ENDJSON
""")
        os.chmod(fake_engram, 0o755)

        payload = json.dumps({"prompt": "Tell me about important keyword"})

        env = {**os.environ, "ENGRAM_CONFIG": config_path, "PATH": f"{fake_engram_dir}:{os.environ.get('PATH', '')}"}

        result = subprocess.run(
            [sys.executable, hook_path],
            input=payload, capture_output=True, text=True,
            env=env,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        # Query result (IMPORTANT_ENTITY) should be preserved
        assert "IMPORTANT_ENTITY" in result.stdout, "Query results should be preserved"
        # Digest should be truncated
        assert result.stdout.count("DIGEST_CONTENT") < cache_content.count("DIGEST_CONTENT"), \
            "Digest should be truncated"
        print("  ✅ context hook preserves query results when truncating")


def _write_logging_fake_engram(tmpdir, calls_log, sleep_secs=0.0):
    """Fake engram that logs each invocation's subcommand and returns canned JSON."""
    fake_dir = os.path.join(tmpdir, "bin")
    os.makedirs(fake_dir, exist_ok=True)
    fake = os.path.join(fake_dir, "engram")
    with open(fake, "w") as f:
        f.write(f"""#!/usr/bin/env python3
import json, sys, time
with open({calls_log!r}, "a") as log:
    log.write(sys.argv[1] + "\\n")
time.sleep({sleep_secs})
if sys.argv[1] == "context":
    print(json.dumps({{"context": "Digest built by fallback engram context call."}}))
else:
    print(json.dumps({{"matched_entities": ["QUERY_ENTITY"], "expanded_entities": [],
                       "context": "## QUERY_ENTITY\\nDetails"}}))
""")
    os.chmod(fake, 0o755)
    return fake_dir


def test_context_hook_cache_miss_skips_query_step():
    """On cache miss the hook must fit the 3s hook budget: after spending up
    to 2s on the `engram context` fallback, it must NOT also run the 2s
    `engram query` step (2s + 2s > 3s would get the hook killed)."""
    hook_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "plugin", "bin", "engram-context")

    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        open(os.path.join(vault, "_meta", "hook-enabled"), "w").close()
        # NO context-cache.md → fallback path

        config_path = os.path.join(tmpdir, "config.json")
        with open(config_path, "w") as f:
            json.dump({"vault": vault}, f)

        calls_log = os.path.join(tmpdir, "calls.log")
        fake_dir = _write_logging_fake_engram(tmpdir, calls_log)

        payload = json.dumps({"prompt": "Tell me about deployment testing"})
        env = {**os.environ, "ENGRAM_CONFIG": config_path,
               "PATH": f"{fake_dir}:{os.environ.get('PATH', '')}"}
        result = subprocess.run(
            [sys.executable, hook_path],
            input=payload, capture_output=True, text=True, env=env,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "Digest built by fallback" in result.stdout

        with open(calls_log) as f:
            calls = f.read().split()
        assert calls == ["context"], \
            f"expected only the context fallback call on cache miss, got {calls}"
        print("  ✅ context hook skips query step on cache-miss fallback path")


def test_context_hook_cache_hit_still_queries():
    """With a warm cache the query step still runs (cache read costs ~0)."""
    hook_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "plugin", "bin", "engram-context")

    with tempfile.TemporaryDirectory() as tmpdir:
        vault = _make_vault(tmpdir)
        open(os.path.join(vault, "_meta", "hook-enabled"), "w").close()
        with open(os.path.join(vault, "_meta", "context-cache.md"), "w") as f:
            f.write("**Top Entities:** CACHED_ENTITY (concept)")

        config_path = os.path.join(tmpdir, "config.json")
        with open(config_path, "w") as f:
            json.dump({"vault": vault}, f)

        calls_log = os.path.join(tmpdir, "calls.log")
        fake_dir = _write_logging_fake_engram(tmpdir, calls_log)

        payload = json.dumps({"prompt": "Tell me about deployment testing"})
        env = {**os.environ, "ENGRAM_CONFIG": config_path,
               "PATH": f"{fake_dir}:{os.environ.get('PATH', '')}"}
        result = subprocess.run(
            [sys.executable, hook_path],
            input=payload, capture_output=True, text=True, env=env,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "CACHED_ENTITY" in result.stdout
        assert "QUERY_ENTITY" in result.stdout

        with open(calls_log) as f:
            calls = f.read().split()
        assert calls == ["query"], f"expected only the query call on cache hit, got {calls}"
        print("  ✅ context hook runs query step on cache hit")


if __name__ == "__main__":
    print("Testing UserPromptSubmit hook (engram-context)...")
    test_context_hook_no_hook_enabled_flag()
    test_context_hook_empty_vault()
    test_context_hook_malformed_stdin()
    test_context_hook_cache_file_only()
    test_context_hook_over_budget_truncation()
    test_context_hook_prompt_matched_subgraph()
    test_context_hook_budget_preserves_query_results()
    test_context_hook_cache_miss_skips_query_step()
    test_context_hook_cache_hit_still_queries()
    print("\n🎉 All context hook tests passed!")
