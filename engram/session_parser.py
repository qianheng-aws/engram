"""Parse Claude Code session JSONL files into structured conversation text."""

import json
import os
import glob
from datetime import datetime
from pathlib import Path


CC_DIR = os.path.expanduser("~/.claude")


def find_latest_session(project: str = None) -> str | None:
    """Find the most recently modified session JSONL file."""
    if project:
        pattern = os.path.join(CC_DIR, "projects", project, "*.jsonl")
    else:
        pattern = os.path.join(CC_DIR, "projects", "*", "*.jsonl")
    files = glob.glob(pattern)
    files = [f for f in files if "/subagents/" not in f]
    return max(files, key=os.path.getmtime) if files else None


def parse_session(path: str) -> list[dict]:
    """Parse a session JSONL file into a list of message dicts."""
    messages = []
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    messages.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return messages


def extract_conversation_text(messages: list[dict]) -> str:
    """Extract meaningful conversation text from session messages.

    Keeps: user messages, assistant text, tool names
    Skips: progress, permission-mode, system, file-history-snapshot
    """
    parts = []
    for msg in messages:
        t = msg.get("type")

        if t == "user":
            content = msg.get("message", {}).get("content", "")
            if isinstance(content, str) and content.strip():
                parts.append(f"User: {content.strip()}")
            elif isinstance(content, list):
                for c in content:
                    if isinstance(c, dict) and c.get("type") == "text" and c.get("text", "").strip():
                        parts.append(f"User: {c['text'].strip()}")

        elif t == "assistant":
            for c in msg.get("message", {}).get("content", []):
                if c.get("type") == "text" and c.get("text", "").strip():
                    parts.append(f"Assistant: {c['text'].strip()}")
                elif c.get("type") == "tool_use":
                    parts.append(f"Tool: {c.get('name', 'unknown')}")

    return "\n\n".join(parts)


def extract_session_metadata(path: str, messages: list[dict]) -> dict:
    """Extract metadata from a session."""
    mtime = os.path.getmtime(path)
    session_id = Path(path).stem
    project = Path(path).parent.name

    # Find first and last timestamps
    timestamps = []
    for msg in messages:
        ts = msg.get("timestamp")
        if ts:
            timestamps.append(ts)

    return {
        "session_id": session_id,
        "project": project,
        "date": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d"),
        "file": path,
        "message_count": len([m for m in messages if m.get("type") in ("user", "assistant")]),
    }
