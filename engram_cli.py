#!/usr/bin/env python3
"""Engram CLI — called by CC /engram command.

Usage:
    engram install                              # register in ~/.claude/CLAUDE.md
    engram uninstall                            # remove from ~/.claude/CLAUDE.md
    engram init [PATH]                         # init vault, save path to ~/.engram/config.json
    engram auto [on|off|status]                 # toggle auto-capture
    engram replay --stdin                       # process extracted JSON
    engram integrate [--stdin]                  # detect or execute merges
    engram prune [--stdin]                      # score or execute archival
    engram community [--stdin]                  # detect or save community summaries
    engram abstract                             # gather data for pattern discovery
    engram save-pattern --stdin                 # save discovered patterns
    engram status                               # vault statistics
    engram query --question "..."               # search graph
    engram consolidation [--reset]              # show/reset consolidation tracking
    All commands accept optional --vault PATH to override the saved default.
"""

import argparse
import contextlib
import fcntl
import hashlib
import io
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime

from engram.graph import DESC_SNIPPET_LEN, MemoryGraph

CONFIG_PATH = os.environ.get("ENGRAM_CONFIG", os.path.expanduser("~/.engram/config.json"))
FALLBACK_VAULT = os.path.expanduser("~/.engram/vault")
GRAPH_FIELD_SEP = "<|ENGRAM_SEP|>"
ENTITY_WIKILINK_RE = re.compile(r'\[\[([A-Z][A-Z0-9_]+)\]\]')
MIN_TOKEN_LEN = 2
QUERY_STOP_WORDS = {
    "WHAT", "WHEN", "WHERE", "WHICH", "WHO", "WHOM", "WHOSE",
    "HOW", "WHY", "DOES", "DID", "WAS", "WERE", "WILL", "WOULD",
    "CAN", "COULD", "SHALL", "SHOULD", "MAY", "MIGHT", "MUST",
    "THE", "AND", "BUT", "FOR", "NOR", "NOT", "YET", "THAT",
    "THIS", "THESE", "THOSE", "WITH", "FROM", "INTO", "ABOUT",
    "HAVE", "HAS", "HAD", "BEEN", "BEING", "ARE", "ISN",
    "THERE", "THEIR", "THEY", "THEM", "THEN", "THAN", "ALSO",
    "JUST", "ONLY", "VERY", "SOME", "ANY", "ALL", "EACH",
    "EVERY", "OTHER", "SUCH", "LIKE", "USED", "USING",
}

CLAUDE_MD_PATH = os.path.expanduser("~/.claude/CLAUDE.md")
CLAUDE_MD_MARKER = "## engram"
CLAUDE_MD_SECTION = """\
## engram
- Use `engram` to retrieve context from the user's personal knowledge graph when relevant
- Query: `/engram-query <question>` to search the vault
- Save: `/engram` to extract and persist entities/relations from the current session
- Status: `/engram-status` to check current graph state
"""


def _load_vault_path():
    """Load vault path from config, falling back to default.

    Auto-creates the default vault directory if neither config nor vault exists,
    so users can skip `engram init` for the default path.
    """
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f).get("vault", FALLBACK_VAULT)
    except (FileNotFoundError, json.JSONDecodeError):
        # Auto-init: create default vault so `engram init` is optional
        os.makedirs(os.path.join(FALLBACK_VAULT, "_meta"), exist_ok=True)
        _save_vault_path(FALLBACK_VAULT)
        _register_claude_md()
        return FALLBACK_VAULT


def _save_vault_path(vault: str):
    """Save vault path to config."""
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    config = {}
    try:
        with open(CONFIG_PATH) as f:
            config = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    config["vault"] = os.path.abspath(vault)
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)


CONSOLIDATION_DEFAULTS = {
    "remind_after_replays": 10,
    "remind_after_days": 7,
    "force_after_replays": 15,
    "force_after_days": 14,
}


def _load_consolidation_config():
    """Load consolidation thresholds from config, with defaults."""
    try:
        with open(CONFIG_PATH) as f:
            cfg = json.load(f).get("consolidation", {})
        return {k: cfg.get(k, v) for k, v in CONSOLIDATION_DEFAULTS.items()}
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(CONSOLIDATION_DEFAULTS)


def _consolidation_state_path(vault):
    return os.path.join(vault, "_meta", "consolidation.json")


def _read_consolidation_state(vault):
    path = _consolidation_state_path(vault)
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"last_full_timestamp": None, "replay_count": 0}


def _write_consolidation_state(vault, state):
    path = _consolidation_state_path(vault)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(state, f, indent=2)


def _increment_replay_and_check(vault):
    """Increment replay counter and return (tier, details)."""
    state = _read_consolidation_state(vault)
    state["replay_count"] = state.get("replay_count", 0) + 1
    _write_consolidation_state(vault, state)

    cfg = _load_consolidation_config()
    replays = state["replay_count"]
    last_full = state.get("last_full_timestamp")

    days_since = None
    if last_full:
        try:
            last_dt = datetime.fromisoformat(last_full)
            days_since = (datetime.now() - last_dt).days
        except (ValueError, TypeError):
            pass

    details = {
        "replay_count": replays,
        "days_since_full": days_since,
        "thresholds": cfg,
    }

    if replays >= cfg["force_after_replays"] or (
        days_since is not None and days_since >= cfg["force_after_days"]
    ):
        return 2, details

    if replays >= cfg["remind_after_replays"] or (
        days_since is not None and days_since >= cfg["remind_after_days"]
    ):
        return 1, details

    return 0, details


def _git_sync(vault: str, message: str = "auto-sync"):
    """Auto commit + push vault if it's a git repo. Silent on failure."""
    if not os.path.isdir(os.path.join(vault, ".git")):
        return
    try:
        subprocess.run(["git", "add", "-A"], cwd=vault, capture_output=True, timeout=10)
        # Only commit if there are changes
        result = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=vault, capture_output=True, timeout=5)
        if result.returncode != 0:  # has changes
            subprocess.run(["git", "commit", "-m", message], cwd=vault, capture_output=True, timeout=10)
            # Only push if a remote is configured
            has_remote = subprocess.run(["git", "remote"], cwd=vault, capture_output=True, text=True, timeout=5)
            if has_remote.stdout.strip():
                subprocess.run(["git", "push"], cwd=vault, capture_output=True, timeout=30)
    except Exception:
        pass  # Never fail the engram because of git


# ── install / uninstall ────────────────────────────────

def _register_claude_md():
    """Write engram section to ~/.claude/CLAUDE.md. Returns True if written, False if already present."""
    os.makedirs(os.path.dirname(CLAUDE_MD_PATH), exist_ok=True)
    if os.path.exists(CLAUDE_MD_PATH):
        with open(CLAUDE_MD_PATH, "r") as f:
            content = f.read()
        if CLAUDE_MD_MARKER in content:
            return False
        content = content.rstrip() + "\n\n" + CLAUDE_MD_SECTION
    else:
        content = CLAUDE_MD_SECTION
    with open(CLAUDE_MD_PATH, "w") as f:
        f.write(content)
    return True


def cmd_install(args):
    """Register engram in ~/.claude/CLAUDE.md and print plugin install commands."""
    registered = _register_claude_md()
    plugin_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plugin")
    print(f"CLAUDE.md: {'registered' if registered else 'already registered'}")
    print(f"\nRun these to register the CC plugin:\n")
    print(f"  claude plugin marketplace add {plugin_dir}")
    print(f"  claude plugin install engram")


def cmd_uninstall(args):
    """Remove engram section from ~/.claude/CLAUDE.md."""
    if not os.path.exists(CLAUDE_MD_PATH):
        print(json.dumps({"status": "ok", "message": "no CLAUDE.md found"}))
        return
    with open(CLAUDE_MD_PATH, "r") as f:
        content = f.read()
    if CLAUDE_MD_MARKER not in content:
        print(json.dumps({"status": "ok", "message": "engram section not found in CLAUDE.md"}))
        return
    # Remove from marker to next ## heading or EOF
    cleaned = re.sub(r"\n*## engram\n.*?(?=\n## |\Z)", "", content, flags=re.DOTALL).rstrip()
    if cleaned:
        with open(CLAUDE_MD_PATH, "w") as f:
            f.write(cleaned + "\n")
    else:
        os.remove(CLAUDE_MD_PATH)
    print(json.dumps({"status": "ok", "message": "engram section removed from CLAUDE.md"}))


# ── init ───────────────────────────────────────────────

def cmd_init(args):
    """Initialize a new engram vault directory."""
    vault = args.init_path or FALLBACK_VAULT
    vault = os.path.abspath(vault)
    os.makedirs(os.path.join(vault, "_meta"), exist_ok=True)
    _save_vault_path(vault)
    registered = _register_claude_md()
    actions = [
        f"vault created at {vault}",
        f"config saved to {CONFIG_PATH}",
        f"CLAUDE.md {'registered' if registered else 'already registered'} at {CLAUDE_MD_PATH}",
    ]
    print(json.dumps({"status": "ok", "actions": actions}))


def cmd_auto(args):
    """Toggle auto-replay hook on/off."""
    vault = args.vault
    hook_flag = os.path.join(vault, "_meta", "hook-enabled")
    if args.auto_action == "on":
        os.makedirs(os.path.dirname(hook_flag), exist_ok=True)
        open(hook_flag, "w").close()
        print(json.dumps({
            "status": "ok",
            "auto_replay": "enabled",
            "note": "Automatic background extraction enabled (turns captured and processed in background)"
        }))
    elif args.auto_action == "off":
        if os.path.exists(hook_flag):
            os.remove(hook_flag)
        print(json.dumps({
            "status": "ok",
            "auto_replay": "disabled",
            "note": "Automatic background extraction disabled"
        }))
    else:
        enabled = os.path.exists(hook_flag)
        print(json.dumps({
            "status": "ok",
            "auto_replay": "enabled" if enabled else "disabled",
            "note": "Background extraction " + ("active" if enabled else "inactive")
        }))


# ── replay ──────────────────────────────────────────────

def _check_dedup(vault: str, data: dict, window_minutes: int = 15) -> bool:
    """Check if this replay payload was already processed recently.

    Returns True if duplicate (should skip), False if new.
    Uses content hash + rolling time window to prevent accidental double-saves.
    """
    dedup_dir = os.path.join(vault, "_meta", "dedup")
    os.makedirs(dedup_dir, exist_ok=True)

    # Hash the meaningful content (entities + relations + summary)
    content = json.dumps({
        "entities": sorted([e["name"] for e in data.get("entities", [])]),
        "relations": sorted(
            [f"{r['source']}->{r['target']}" for r in data.get("relations", [])]
        ),
        "summary": data.get("daily_summary", ""),
    }, sort_keys=True)
    content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]

    hash_file = os.path.join(dedup_dir, f"{content_hash}.json")

    if os.path.exists(hash_file):
        try:
            with open(hash_file) as f:
                prev = json.load(f)
            prev_time = datetime.fromisoformat(prev["timestamp"])
            if (datetime.now() - prev_time).total_seconds() < window_minutes * 60:
                return True
        except Exception:
            pass

    # Write new hash
    with open(hash_file, "w") as f:
        json.dump({"timestamp": datetime.now().isoformat(), "hash": content_hash}, f)

    # Clean old hash files (> 1 hour)
    for fname in os.listdir(dedup_dir):
        fpath = os.path.join(dedup_dir, fname)
        try:
            with open(fpath) as f:
                entry = json.load(f)
            entry_time = datetime.fromisoformat(entry["timestamp"])
            if (datetime.now() - entry_time).total_seconds() > 3600:
                os.remove(fpath)
        except Exception:
            pass

    return False


def _build_evidence_maps(evidence_items, date):
    """Build entity/relation → evidence ref mappings from evidence items.

    Returns (ev_by_block_id, entity_evidence_map, relation_evidence_map).
    - ev_by_block_id: {block_id: {"content": ..., "entities": [...], "relations": [...]}}
    - entity_evidence_map: {ENTITY_NAME: [wikilink_ref, ...]}
    - relation_evidence_map: {(SRC, TGT): [wikilink_ref, ...]}
    """
    ev_by_block_id = {}
    entity_evidence_map = defaultdict(list)
    relation_evidence_map = defaultdict(list)

    for ev in evidence_items:
        content = ev.get("content", "")
        if not content:
            continue
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:8]
        block_id = f"ev-{content_hash}"
        wikilink_ref = f"daily/{date}#^{block_id}"

        ev_by_block_id[block_id] = {
            "content": content,
            "entities": [e.upper().strip() for e in ev.get("entities", [])],
            "relations": ev.get("relations", []),
        }

        for entity_name in ev_by_block_id[block_id]["entities"]:
            entity_evidence_map[entity_name].append(wikilink_ref)
        for rel in ev_by_block_id[block_id]["relations"]:
            if len(rel) >= 2:
                src, tgt = rel[0].upper().strip(), rel[1].upper().strip()
                relation_evidence_map[(src, tgt)].append(wikilink_ref)

    return ev_by_block_id, entity_evidence_map, relation_evidence_map


def cmd_replay(args):
    """Process CC-extracted entities/relations JSON from stdin."""
    data = json.load(sys.stdin, strict=False)
    print(json.dumps(_replay_data(args.vault, data)))


def _replay_data(vault, data):
    """Land extracted entities/relations JSON into the graph + vault.

    Shared by `engram replay` (stdin) and `engram worker` (in-process).
    Returns the result dict (status "skipped" on dedup hit, else "ok").
    """
    # Dedup check: skip if same content was saved within 15 minutes
    if _check_dedup(vault, data):
        return {
            "status": "skipped",
            "reason": "duplicate replay detected within 15-minute window",
        }

    graph = MemoryGraph(vault)

    entities = data.get("entities", [])
    relations = data.get("relations", [])
    evidence_items = data.get("evidence", [])
    date = _sanitize_extraction_date(data.get("date"))
    summary = data.get("daily_summary", "")

    # Build evidence maps: entity/relation → wikilink refs
    ev_by_block_id, entity_evidence_map, relation_evidence_map = \
        _build_evidence_maps(evidence_items, date)

    for e in entities:
        attrs = {
            "entity_type": e.get("entity_type", "CONCEPT"),
            "description": e.get("description", ""),
            "confidence": e.get("confidence", "EXTRACTED"),
            "source_id": f"session-{date}",
            "last_updated": date,
        }
        refs = e.get("references", [])
        if refs:
            attrs["references"] = json.dumps(refs)
        local_path = e.get("local_path", "")
        if local_path:
            attrs["local_path"] = local_path
        url = e.get("url", "")
        if url:
            attrs["url"] = url
        name = e["name"].upper().strip()
        if name in entity_evidence_map:
            attrs["evidence"] = json.dumps(entity_evidence_map[name])
        graph.upsert_entity(e["name"], attrs)

    for r in relations:
        attrs = {
            "description": r.get("description", ""),
            "weight": r.get("weight", 1.0),
            "confidence": r.get("confidence", "EXTRACTED"),
        }
        if "confidence_score" in r:
            attrs["confidence_score"] = r["confidence_score"]
        src, tgt = r["source"].upper().strip(), r["target"].upper().strip()
        key = (src, tgt)
        rev_key = (tgt, src)
        ev_refs = relation_evidence_map.get(key) or relation_evidence_map.get(rev_key)
        if ev_refs:
            attrs["evidence"] = json.dumps(ev_refs)
        graph.upsert_relation(r["source"], r["target"], attrs)

    hyperedges = data.get("hyperedges", [])
    for h in hyperedges:
        graph.add_hyperedge(
            id=h["id"],
            label=h["label"],
            members=h.get("members", []),
            relation=h.get("relation", "form"),
        )

    graph.save()

    # ── FIX: daily note dedup — replace same-date note instead of appending ──
    daily_dir = os.path.join(vault, "daily")
    os.makedirs(daily_dir, exist_ok=True)
    daily_path = os.path.join(daily_dir, f"{date}.md")

    # Accumulate sessions for the day
    existing_entities = set()
    existing_sessions = []
    existing_evidence_lines = []  # preserved evidence from prior replays
    existing_ev_block_ids = set()
    if os.path.exists(daily_path):
        with open(daily_path, "r") as f:
            content = f.read()
        # Parse existing entity refs
        existing_entities = set(ENTITY_WIKILINK_RE.findall(content))
        # Keep existing summaries: extract bullet lines from ## Summary section
        if "## Summary" in content:
            idx = content.index("## Summary")
            section = content[idx:]
            # End at next ## heading or end of file
            next_heading = section.find("\n## ", 1)
            if next_heading != -1:
                section = section[:next_heading]
            for line in section.split("\n"):
                if line.strip().startswith("- "):
                    existing_sessions.append(line)
        # Keep existing evidence lines
        if "## Evidence" in content:
            idx = content.index("## Evidence")
            section = content[idx:]
            next_heading = section.find("\n## ", 1)
            if next_heading != -1:
                section = section[:next_heading]
            for line in section.split("\n"):
                if line.strip().startswith("- "):
                    existing_evidence_lines.append(line)
                    # Track existing block IDs to avoid duplicates
                    bid_match = re.search(r'\^(ev-[a-f0-9]{6,8})', line)
                    if bid_match:
                        existing_ev_block_ids.add(bid_match.group(1))

    entity_names = [e["name"] for e in entities]
    all_entities = existing_entities | set(entity_names)
    session_count = len(existing_sessions) + 1

    # Collect entity types for tags
    entity_types = set()
    for e in entities:
        entity_types.add(e.get("entity_type", "CONCEPT").lower())

    lines = [
        "---",
        f"date: \"{date}\"",
        f"tags:",
        f"  - daily",
    ]
    for et in sorted(entity_types):
        lines.append(f"  - has/{et}")
    lines += [
        f"entities: {json.dumps(sorted(all_entities))}",
        f"entity_count: {len(all_entities)}",
        f"sessions: {session_count}",
        f"cssclasses:",
        f"  - daily",
        "---", "",
        f"# {date}", "",
    ]

    # Prev/next day navigation
    daily_files = sorted(f[:-3] for f in os.listdir(daily_dir) if f.endswith(".md")) if os.path.isdir(daily_dir) else []
    prev_day = next_day = None
    if date in daily_files:
        idx = daily_files.index(date)
        if idx > 0:
            prev_day = daily_files[idx - 1]
        if idx < len(daily_files) - 1:
            next_day = daily_files[idx + 1]
    elif daily_files:
        # New date — find nearest previous
        earlier = [d for d in daily_files if d < date]
        if earlier:
            prev_day = earlier[-1]

    nav_parts = []
    if prev_day:
        nav_parts.append(f"← [[{prev_day}|prev]]")
    if next_day:
        nav_parts.append(f"[[{next_day}|next]] →")
    if nav_parts:
        lines.append(" | ".join(nav_parts))
        lines.append("")

    lines += ["## Summary", ""]

    # Append existing + new summary
    for s in existing_sessions:
        lines.append(s)
    new_bullet = f"- {summary}"
    if new_bullet not in existing_sessions:
        lines.append(new_bullet)

    # Evidence section: existing evidence + new evidence from this replay
    new_ev_lines = []
    for block_id, ev_data in ev_by_block_id.items():
        if block_id in existing_ev_block_ids:
            continue  # already in daily note
        entity_links = " ".join(f"[[{e}]]" for e in ev_data["entities"])
        suffix = f" → {entity_links}" if entity_links else ""
        new_ev_lines.append(f"- {ev_data['content']}{suffix} ^{block_id}")

    all_evidence_lines = existing_evidence_lines + new_ev_lines
    if all_evidence_lines:
        lines += ["", "## Evidence", ""]
        for ev_line in all_evidence_lines:
            lines.append(ev_line)

    lines += ["", "## Entities", ""]
    for name in sorted(all_entities):
        e = next((x for x in entities if x["name"] == name), None)
        if e:
            etype = e.get('entity_type', '?')
            lines.append(f"- [[{name}]] `{etype}` — {e.get('description', '')[:DESC_SNIPPET_LEN]}")
        else:
            lines.append(f"- [[{name}]]")

    with open(daily_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    # Clear queue
    _clear_queue(vault)

    # Sync vault to git
    _git_sync(vault, f"engram replay {date}: +{len(entities)} entities, +{len(relations)} relations")

    # Track consolidation state
    tier, consolidation_details = _increment_replay_and_check(vault)

    return {
        "status": "ok",
        "entities_added": len(entities),
        "relations_added": len(relations),
        "evidence_added": len(new_ev_lines),
        "hyperedges_added": len(hyperedges),
        "total_nodes": graph.node_count,
        "total_edges": graph.edge_count,
        "daily_note": daily_path,
        "consolidation": {
            "tier": tier,
            **consolidation_details,
        },
    }


def _clear_queue(vault):
    queue_dir = os.path.join(vault, "_meta", "queue")
    if not os.path.isdir(queue_dir):
        return
    for fname in os.listdir(queue_dir):
        if fname.endswith(".json"):
            qpath = os.path.join(queue_dir, fname)
            try:
                with open(qpath) as f:
                    q = json.load(f)
                if q.get("status") == "pending":
                    os.remove(qpath)
            except Exception:
                pass


# ── integrate ───────────────────────────────────────────

def cmd_integrate(args):
    """Find duplicates, or execute merge instructions from stdin."""
    graph = MemoryGraph(args.vault)

    if args.stdin:
        # Execute merge: {"merges": [{"canonical": "A", "aliases": ["B", "C"]}]}
        data = json.load(sys.stdin, strict=False)
        merged = []
        for m in data.get("merges", []):
            canonical = m["canonical"].upper().strip()
            for alias in m.get("aliases", []):
                alias = alias.upper().strip()
                _merge_entity(graph, canonical, alias)
                merged.append(f"{alias} → {canonical}")
        graph.save()
        _git_sync(args.vault, f"engram integrate: merged {len(merged)} entities")
        print(json.dumps({"status": "ok", "merged": merged}))
        return

    # Detection mode
    names = graph.all_entity_names()
    token_map = defaultdict(set)
    for name in names:
        for token in name.split("_"):
            if len(token) > MIN_TOKEN_LEN:
                token_map[token].add(name)

    candidates = []
    seen = set()
    for token, group in token_map.items():
        if len(group) > 1:
            key = frozenset(group)
            if key not in seen:
                seen.add(key)
                # Include descriptions for CC to judge
                details = []
                for n in group:
                    attrs = graph.get_entity(n)
                    desc = (attrs.get("description", "") or "").split(GRAPH_FIELD_SEP)[0][:DESC_SNIPPET_LEN]
                    details.append({"name": n, "description": desc, "degree": graph._graph.degree(n)})
                candidates.append(details)

    print(json.dumps({
        "status": "ok",
        "total_entities": len(names),
        "duplicate_candidates": candidates[:20],
        "message": "Review candidates. To merge, pipe: {\"merges\": [{\"canonical\": \"KEEP_NAME\", \"aliases\": [\"REMOVE_NAME\"]}]} | engram integrate --vault PATH --stdin",
    }))


_TYPE_FOLDER = MemoryGraph.TYPE_FOLDER


def _remove_entity_md(vault: str, name: str, entity_type: str = "CONCEPT"):
    """Remove an entity's markdown file from the vault."""
    folder = _TYPE_FOLDER.get(entity_type.upper(), "concepts")
    fname = re.sub(r'[<>:"/\\|?*]', "_", name)[:200] + ".md"
    path = os.path.join(vault, "entities", folder, fname)
    if os.path.exists(path):
        os.remove(path)


def _merge_entity(graph, canonical: str, alias: str):
    """Merge alias entity into canonical: move edges, merge descriptions, delete alias."""
    G = graph._graph
    if alias not in G or canonical not in G:
        return

    # Merge descriptions
    alias_desc = G.nodes[alias].get("description", "")
    canon_desc = G.nodes[canonical].get("description", "")
    if alias_desc and alias_desc not in canon_desc:
        G.nodes[canonical]["description"] = f"{canon_desc}{GRAPH_FIELD_SEP}{alias_desc}" if canon_desc else alias_desc

    # Move edges from alias to canonical
    for neighbor in list(G.neighbors(alias)):
        if neighbor == canonical:
            continue
        edge_data = dict(G.edges[alias, neighbor])
        if G.has_edge(canonical, neighbor):
            existing = G.edges[canonical, neighbor]
            existing["weight"] = float(existing.get("weight", 0)) + float(edge_data.get("weight", 0))
        else:
            G.add_edge(canonical, neighbor, **edge_data)

    # Remove alias markdown file from vault
    _remove_entity_md(graph.vault_dir, alias, G.nodes[alias].get("entity_type", "CONCEPT"))

    G.remove_node(alias)


# ── prune ───────────────────────────────────────────────

def cmd_prune(args):
    """Score entities by decay, or execute archive from stdin."""
    graph = MemoryGraph(args.vault)

    if args.stdin:
        # Execute archive: {"archive": ["ENTITY_A", "ENTITY_B"]}
        data = json.load(sys.stdin, strict=False)
        archived = []
        archive_dir = os.path.join(args.vault, "_meta", "archive")
        os.makedirs(archive_dir, exist_ok=True)
        for name in data.get("archive", []):
            name = name.upper().strip()
            attrs = graph.get_entity(name)
            if attrs:
                # Save to archive
                with open(os.path.join(archive_dir, f"{name}.json"), "w") as f:
                    json.dump({"name": name, **attrs, "archived": datetime.now().isoformat()}, f, indent=2)
                # Remove markdown file from vault
                _remove_entity_md(args.vault, name, attrs.get("entity_type", "CONCEPT"))
                graph._graph.remove_node(name)
                archived.append(name)
        graph.save()
        _git_sync(args.vault, f"engram prune: archived {len(archived)} entities")
        print(json.dumps({"status": "ok", "archived": archived}))
        return

    # Report mode
    today = datetime.now()
    scores = []
    for name in graph.all_entity_names():
        attrs = graph.get_entity(name)
        last = attrs.get("last_updated", "2020-01-01")
        try:
            days_ago = (today - datetime.strptime(last, "%Y-%m-%d")).days
        except (ValueError, TypeError):
            days_ago = 365

        degree = graph._graph.degree(name)
        time_score = 0.5 ** (days_ago / 30)
        conn_bonus = min(degree / 10, 1.0) * 0.3
        score = min(time_score + conn_bonus, 1.0)
        scores.append({"name": name, "score": round(score, 3), "days_ago": days_ago, "degree": degree})

    scores.sort(key=lambda x: x["score"])
    fading = [s for s in scores if s["score"] < 0.3]
    archivable = [s for s in scores if s["score"] < 0.1]

    print(json.dumps({
        "status": "ok",
        "total_entities": len(scores),
        "fading": fading[:20],
        "archivable": archivable[:20],
        "message": "To archive, pipe: {\"archive\": [\"ENTITY_A\"]} | engram prune --vault PATH --stdin",
    }))


# ── community ──────────────────────────────────────────

def cmd_community(args):
    """Detect communities, or save CC-generated summaries from stdin."""
    graph = MemoryGraph(args.vault)

    if args.stdin:
        # Save community summaries: {"communities": [{"id": 0, "title": "...", "summary": "...", "members": ["A", "B"]}]}
        data = json.load(sys.stdin, strict=False)
        # Clear old community files before writing new ones
        comm_dir = os.path.join(args.vault, "communities")
        if os.path.isdir(comm_dir):
            for fname in os.listdir(comm_dir):
                if fname.endswith(".md"):
                    os.remove(os.path.join(comm_dir, fname))
        saved = []
        for c in data.get("communities", []):
            path = graph.export_community(
                community_id=c["id"],
                title=c["title"],
                summary=c["summary"],
                members=c["members"],
            )
            saved.append({"title": c["title"], "path": path})
        _git_sync(args.vault, f"engram community: {len(saved)} communities")
        print(json.dumps({"status": "ok", "saved": saved}))
        return

    # Detection mode
    communities = graph.detect_communities(min_size=2)
    surprising = graph.find_surprising_connections(communities)
    print(json.dumps({
        "status": "ok",
        "total_nodes": graph.node_count,
        "total_edges": graph.edge_count,
        "communities_found": len(communities),
        "communities": communities,
        "surprising_connections": surprising,
        "message": "Review communities. For each, generate a title and summary. Then pipe: {\"communities\": [{\"id\": 0, \"title\": \"...\", \"summary\": \"...\", \"members\": [\"A\", \"B\"]}]} | engram community --stdin",
    }, indent=2))


# ── query (enhanced with graph traversal) ───────────────

def cmd_query(args):
    """Search knowledge graph with keyword match + multi-hop traversal."""
    graph = MemoryGraph(args.vault)
    question = args.question
    names = graph.all_entity_names()
    tokens = [re.sub(r'[^\w]', '', t).upper() for t in question.split() if len(t) > MIN_TOKEN_LEN]
    tokens = [t for t in tokens if t and t not in QUERY_STOP_WORDS]

    # Precompile word-boundary patterns for description matching
    tok_patterns = [re.compile(r'\b' + re.escape(t.lower()) + r'\b') for t in tokens]

    # 1. Keyword match on entity names (substring OK — names are structured)
    matched = [n for n in names if any(tok in n for tok in tokens)]

    # 2. Also match on descriptions (word boundary to avoid partial matches)
    if len(matched) < 3:
        for n in names:
            if n in matched:
                continue
            attrs = graph.get_entity(n)
            desc = (attrs.get("description", "") or "").lower()
            if any(p.search(desc) for p in tok_patterns):
                matched.append(n)
            if len(matched) >= 10:
                break

    # 3. If no keyword matches, try god nodes as entry points
    if not matched:
        god = graph.god_nodes(top_n=5)
        for g in god:
            attrs = graph.get_entity(g["name"])
            desc = (attrs.get("description", "") or "").lower()
            if any(p.search(desc) for p in tok_patterns):
                matched.append(g["name"])

    # 4. Multi-hop: expand 3 hops out, track hop distance and max weight
    matched_set = set(matched)
    # {entity_name: (hop_distance, max_weight)}
    expanded_info = {}
    frontier = set(matched[:5])
    visited = set(frontier)
    for hop in range(1, 4):
        next_frontier = set()
        for name in frontier:
            for neighbor, edge_data in graph.get_neighbors(name):
                if neighbor in visited:
                    continue
                w = float(edge_data.get("weight", 0))
                if neighbor in expanded_info:
                    prev_hop, prev_w = expanded_info[neighbor]
                    expanded_info[neighbor] = (prev_hop, max(prev_w, w))
                else:
                    expanded_info[neighbor] = (hop, w)
                next_frontier.add(neighbor)
        visited |= next_frontier
        frontier = next_frontier
    # expanded_ranked computed after community expansion (step 6)

    # 5. Build rich context
    context_parts = []
    for name in matched[:10]:
        attrs = graph.get_entity(name)
        neighbors = graph.get_neighbors(name)
        desc = (attrs.get("description", "") or "").split(GRAPH_FIELD_SEP)[0]
        local_path = attrs.get("local_path", "")
        url = attrs.get("url", "")
        header = f"## {name}"
        if url:
            header += f"\n🔗 {url}"
        if local_path:
            header += f"\n📁 `{local_path}`"
        neighbor_strs = []
        for n, d in neighbors[:8]:
            ndesc = (d.get("description", "") or "").split(GRAPH_FIELD_SEP)[0][:DESC_SNIPPET_LEN]
            weight = d.get("weight", "?")
            neighbor_strs.append(f"  - [[{n}]]: {ndesc} (w:{weight})")
        context_parts.append(f"{header}\n{desc}\n### Relations\n" + "\n".join(neighbor_strs))

    # 6. Load community context for matched entities
    community_context = []
    comm_dir = os.path.join(args.vault, "communities")
    if os.path.isdir(comm_dir):
        for fname in os.listdir(comm_dir):
            if not fname.endswith(".md"):
                continue
            fpath = os.path.join(comm_dir, fname)
            with open(fpath, "r") as f:
                content = f.read()
            # Check if any matched entity is a member of this community
            community_members = set(ENTITY_WIKILINK_RE.findall(content))
            if matched_set & community_members:
                # Extract title and summary (skip frontmatter)
                lines = content.split("\n")
                in_frontmatter = False
                title = ""
                summary_lines = []
                for line in lines:
                    if line.strip() == "---":
                        in_frontmatter = not in_frontmatter
                        continue
                    if in_frontmatter:
                        continue
                    if line.startswith("# "):
                        title = line[2:].strip()
                    elif line.startswith("## "):
                        break
                    elif title and line.strip():
                        summary_lines.append(line.strip())
                if title:
                    community_context.append(f"**Community: {title}**\n" + " ".join(summary_lines))
                    # Also expand with community members
                    for member in community_members:
                        if member not in matched_set and member not in expanded_info:
                            expanded_info[member] = (0, 0)  # community member, no hop/weight

    # Sort expanded: hop asc, weight desc, take top 30
    for name in matched_set:
        expanded_info.pop(name, None)
    expanded_ranked = sorted(expanded_info.items(), key=lambda x: (x[1][0], -x[1][1]))[:30]

    print(json.dumps({
        "question": question,
        "matched_entities": matched[:10],
        "expanded_entities": [
            {"name": name, "hops": hop, "max_weight": round(w, 2)}
            for name, (hop, w) in expanded_ranked
        ],
        "all_entities": names,
        "context": "\n\n".join(context_parts),
        "community_context": community_context,
        "message": "Use context + community_context to answer. expanded_entities include community co-members.",
    }, indent=2))


# ── abstract ────────────────────────────────────────────

def cmd_abstract(args):
    """Gather daily notes + existing patterns for CC to analyze."""
    vault = args.vault
    daily_dir = os.path.join(vault, "daily")
    pattern_dir = os.path.join(vault, "patterns")
    os.makedirs(pattern_dir, exist_ok=True)

    dailies = {}
    if os.path.isdir(daily_dir):
        for fname in sorted(os.listdir(daily_dir), reverse=True):
            if fname.endswith(".md"):
                with open(os.path.join(daily_dir, fname), "r") as f:
                    dailies[fname[:-3]] = f.read()
            if len(dailies) >= 14:
                break

    patterns = {}
    if os.path.isdir(pattern_dir):
        for fname in os.listdir(pattern_dir):
            if fname.endswith(".md"):
                with open(os.path.join(pattern_dir, fname), "r") as f:
                    patterns[fname[:-3]] = f.read()

    print(json.dumps({
        "daily_notes": dailies,
        "existing_patterns": patterns,
        "message": "Analyze daily notes for recurring behaviors. Output JSON with new_patterns and updated_patterns arrays. Each pattern: {name, description, evidence: [dates], confidence: 0.0-1.0}. Pipe result to: engram save-pattern --vault PATH --stdin",
    }, indent=2))


def cmd_save_pattern(args):
    """Save CC-generated patterns to vault."""
    data = json.load(sys.stdin, strict=False)
    vault = args.vault
    pattern_dir = os.path.join(vault, "patterns")
    os.makedirs(pattern_dir, exist_ok=True)

    saved = []
    for p in data.get("new_patterns", []) + data.get("updated_patterns", []):
        name = p["name"]
        fname = name.replace(" ", "-").lower() + ".md"
        evidence = p.get("evidence", [])
        confidence = p.get("confidence", 0.5)
        if confidence < 0.5:
            continue

        # Determine confidence tier for tag
        if confidence >= 0.8:
            conf_tier = "high"
        elif confidence >= 0.6:
            conf_tier = "medium"
        else:
            conf_tier = "low"

        lines = [
            "---",
            f"tags:",
            f"  - pattern",
            f"  - confidence/{conf_tier}",
            f"name: \"{name}\"",
            f"confidence: {confidence}",
            f"evidence_count: {len(evidence)}",
            f"last_updated: \"[[daily/{datetime.now().strftime('%Y-%m-%d')}|{datetime.now().strftime('%Y-%m-%d')}]]\"",
            f"cssclasses:",
            f"  - pattern",
            "---", "",
            f"# {name}", "",
            p.get("description", ""), "",
            "## Evidence", "",
        ]
        for e in evidence:
            lines.append(f"- [[daily/{e}|{e}]]")
        lines.append("")

        with open(os.path.join(pattern_dir, fname), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        saved.append(name)

    _git_sync(vault, f"engram abstract: {len(saved)} patterns")
    print(json.dumps({"status": "ok", "patterns_saved": saved}))


# ── context ────────────────────────────────────────────

def _build_context_text(vault):
    """Build context markdown text and metadata.

    Returns: (context_text: str, metadata: dict)
    """
    graph = MemoryGraph(vault)

    # Top entities by degree (hub nodes)
    G = graph._graph
    entities_by_degree = sorted(
        [(n, G.degree(n), dict(G.nodes[n])) for n in G.nodes()],
        key=lambda x: -x[1],
    )

    # Recent daily summaries (last 3)
    daily_dir = os.path.join(vault, "daily")
    recent_summaries = []
    if os.path.isdir(daily_dir):
        for fname in sorted(os.listdir(daily_dir), reverse=True)[:3]:
            if not fname.endswith(".md"):
                continue
            with open(os.path.join(daily_dir, fname), "r") as f:
                content = f.read()
            bullets = []
            if "## Summary" in content:
                idx = content.index("## Summary")
                section = content[idx:]
                next_heading = section.find("\n## ", 1)
                if next_heading != -1:
                    section = section[:next_heading]
                for line in section.split("\n"):
                    if line.strip().startswith("- "):
                        bullets.append(line.strip()[2:])
            if bullets:
                recent_summaries.append({"date": fname[:-3], "summaries": bullets})

    # Build compact context
    parts = []

    # Hub entities (top 10)
    if entities_by_degree:
        entity_lines = []
        for name, degree, attrs in entities_by_degree[:10]:
            etype = attrs.get("entity_type", "?")
            desc = (attrs.get("description", "") or "").split(GRAPH_FIELD_SEP)[0][:DESC_SNIPPET_LEN]
            entity_lines.append(f"- {name} ({etype}, {degree} connections): {desc}")
        parts.append("## Key Entities\n" + "\n".join(entity_lines))

    # Recent activity
    if recent_summaries:
        activity_lines = []
        for s in recent_summaries:
            activity_lines.append(f"### {s['date']}")
            for b in s["summaries"]:
                activity_lines.append(f"- {b}")
        parts.append("## Recent Activity\n" + "\n".join(activity_lines))

    # Patterns
    pattern_dir = os.path.join(vault, "patterns")
    if os.path.isdir(pattern_dir):
        pattern_lines = []
        for fname in os.listdir(pattern_dir):
            if fname.endswith(".md"):
                with open(os.path.join(pattern_dir, fname), "r") as f:
                    content = f.read()
                # Extract name and description
                name = ""
                desc = ""
                past_frontmatter = False
                frontmatter_count = 0
                for line in content.split("\n"):
                    if line.strip() == "---":
                        frontmatter_count += 1
                        if frontmatter_count == 2:
                            past_frontmatter = True
                        continue
                    if past_frontmatter:
                        if line.startswith("# "):
                            name = line[2:].strip()
                        elif name and line.strip() and not line.startswith("#"):
                            desc = line.strip()
                            break
                if name:
                    pattern_lines.append(f"- {name}: {desc[:80]}")
        if pattern_lines:
            parts.append("## Known Patterns\n" + "\n".join(pattern_lines))

    # Suggested questions
    suggested = graph.suggested_questions(top_n=5)
    if suggested:
        q_lines = []
        for q in suggested:
            q_lines.append(f"- {q['question']} (betweenness: {q['betweenness']})")
        parts.append("## Suggested Questions\n" + "\n".join(q_lines))

    context_text = "\n\n".join(parts)

    metadata = {
        "entity_count": graph.node_count,
        "edge_count": graph.edge_count,
        "suggested_questions": suggested,
    }

    return context_text, metadata


def cmd_context(args):
    """Output compact summary for injecting into agent system prompt.

    Inspired by community/engram's mem_context — returns recent activity
    and top entities in a token-efficient format.
    """
    vault = args.vault
    context_text, metadata = _build_context_text(vault)

    if args.write_cache:
        # Write cache mode: save markdown to _meta/context-cache.md
        meta_dir = os.path.join(vault, "_meta")
        os.makedirs(meta_dir, exist_ok=True)
        cache_path = os.path.join(meta_dir, "context-cache.md")

        with open(cache_path, "w", encoding="utf-8") as f:
            f.write(context_text)

        file_size = len(context_text.encode("utf-8"))
        print(json.dumps({
            "written": cache_path,
            "bytes": file_size,
        }))
    else:
        # Default mode: print JSON
        print(json.dumps({
            "context": context_text,
            "entity_count": metadata["entity_count"],
            "edge_count": metadata["edge_count"],
            "suggested_questions": metadata["suggested_questions"],
        }, indent=2))


# ── feedback ───────────────────────────────────────────

def cmd_feedback(args):
    """Scan entity files for correction/merge/delete callouts, or apply fixes from stdin."""
    vault = args.vault
    graph = MemoryGraph(vault)

    if args.stdin:
        # Execute mode: apply CC's corrections
        data = json.load(sys.stdin, strict=False)
        applied = []

        for fix in data.get("corrections", []):
            name = fix["entity"].upper().strip()
            entity = graph.get_entity(name)
            if not entity:
                continue
            if "description" in fix:
                graph.upsert_entity(name, {"description": fix["description"]})
            if "entity_type" in fix:
                graph.upsert_entity(name, {"entity_type": fix["entity_type"]})
            applied.append({"entity": name, "action": "corrected"})

        for merge in data.get("merges", []):
            canonical = merge["canonical"].upper().strip()
            for alias in merge.get("aliases", []):
                alias = alias.upper().strip()
                _merge_entity(graph, canonical, alias)
                _remove_entity_md(vault, alias, graph.get_entity(alias) and graph.get_entity(alias).get("entity_type", "CONCEPT") or "CONCEPT")
                applied.append({"entity": alias, "action": f"merged into {canonical}"})

        for name in data.get("deletes", []):
            name = name.upper().strip()
            if name in graph._graph:
                _remove_entity_md(vault, name, graph.get_entity(name).get("entity_type", "CONCEPT"))
                graph._graph.remove_node(name)
                applied.append({"entity": name, "action": "deleted"})

        graph.save()

        # Remove processed callouts from entity files
        _clear_feedback_callouts(vault)

        _git_sync(vault, f"engram feedback: {len(applied)} actions applied")
        print(json.dumps({"status": "ok", "applied": applied}))
        return

    # Report mode: scan for callouts
    callouts = _scan_feedback_callouts(vault)
    print(json.dumps({
        "status": "ok",
        "total": len(callouts),
        "callouts": callouts,
        "message": "Review callouts and pipe corrections: {\"corrections\": [...], \"merges\": [...], \"deletes\": [...]} | engram feedback --stdin",
    }, indent=2))


def _iter_entity_files(vault):
    """Yield (entity_name, path) for all markdown files under entities/."""
    entities_dir = os.path.join(vault, "entities")
    if not os.path.isdir(entities_dir):
        return
    for root, _, files in os.walk(entities_dir):
        for fname in files:
            if fname.endswith(".md"):
                yield fname[:-3], os.path.join(root, fname)


def _scan_feedback_callouts(vault):
    """Scan entity markdown files for [!correction], [!merge], [!delete] callouts."""
    callouts = []

    callout_re = re.compile(r'>\s*\[!(correction|merge|delete)\]\s*(.*)')
    for entity_name, path in _iter_entity_files(vault):
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()

            i = 0
            while i < len(lines):
                m = callout_re.match(lines[i])
                if m:
                    callout_type = m.group(1)
                    title = m.group(2).strip()
                    # Collect body lines (continuation lines starting with >)
                    body_lines = []
                    i += 1
                    while i < len(lines) and lines[i].startswith(">"):
                        body_lines.append(lines[i].lstrip("> ").rstrip("\n"))
                        i += 1
                    body = "\n".join(body_lines).strip()
                    callouts.append({
                        "entity": entity_name,
                        "type": callout_type,
                        "title": title,
                        "body": body,
                    })
                else:
                    i += 1

    return callouts


def _clear_feedback_callouts(vault):
    """Remove all [!correction], [!merge], [!delete] callouts from entity files."""
    callout_re = re.compile(r'>\s*\[!(correction|merge|delete)\]\s*')
    for _name, path in _iter_entity_files(vault):
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()

            new_lines = []
            i = 0
            changed = False
            while i < len(lines):
                if callout_re.match(lines[i]):
                    changed = True
                    i += 1
                    # Skip continuation lines
                    while i < len(lines) and lines[i].startswith(">"):
                        i += 1
                    # Skip trailing blank lines after callout
                    while i < len(lines) and lines[i].strip() == "":
                        i += 1
                else:
                    new_lines.append(lines[i])
                    i += 1

            if changed:
                with open(path, "w", encoding="utf-8") as f:
                    f.writelines(new_lines)


# ── lint ───────────────────────────────────────────────

def cmd_lint(args):
    """Validate vault consistency: GraphML ↔ markdown sync, dead wikilinks, orphans, frontmatter."""
    vault = args.vault
    graph = MemoryGraph(vault)
    G = graph._graph
    today = datetime.now().strftime("%Y-%m-%d")

    details = {
        "missing_markdown": [],
        "orphan_markdown": [],
        "dead_wikilinks": [],
        "orphan_nodes": [],
        "incomplete_frontmatter": [],
    }

    # ── Single pass over entity files: sync check + content cache ──
    graph_nodes = set(G.nodes())
    md_files = {}  # entity_name -> (path, content)
    for name, fpath in _iter_entity_files(vault):
        with open(fpath, "r", encoding="utf-8") as f:
            md_files[name] = (fpath, f.read())

    # ── Check 1: GraphML ↔ markdown sync ──
    for node in graph_nodes:
        if node not in md_files:
            entity_type = G.nodes[node].get("entity_type", "CONCEPT")
            folder = _TYPE_FOLDER.get(entity_type.upper(), "concepts")
            expected = os.path.join("entities", folder, graph._safe_filename(node))
            details["missing_markdown"].append({"node": node, "expected_path": expected})

    for name, (path, _content) in md_files.items():
        if name not in graph_nodes:
            details["orphan_markdown"].append({"path": os.path.relpath(path, vault)})

    # ── Check 2: Dead wikilinks ──
    seen_links = set()

    def _scan_wikilinks(rel_path, content):
        content_clean = re.sub(r'```dataview.*?```', '', content, flags=re.DOTALL)
        for target in ENTITY_WIKILINK_RE.findall(content_clean):
            if target not in graph_nodes:
                key = (rel_path, target)
                if key not in seen_links:
                    seen_links.add(key)
                    details["dead_wikilinks"].append({"file": rel_path, "target": target})

    # Scan cached entity files
    for _name, (path, content) in md_files.items():
        _scan_wikilinks(os.path.relpath(path, vault), content)

    # Scan communities + groups (not cached)
    for scan_dir in ("communities", "groups"):
        dir_path = os.path.join(vault, scan_dir)
        if not os.path.isdir(dir_path):
            continue
        for root, _, files in os.walk(dir_path):
            for fname in files:
                if not fname.endswith(".md"):
                    continue
                fpath = os.path.join(root, fname)
                with open(fpath, "r", encoding="utf-8") as f:
                    _scan_wikilinks(os.path.relpath(fpath, vault), f.read())

    # ── Check 3: Orphan nodes (degree=0, not created today) ──
    for node in graph_nodes:
        if G.degree(node) == 0:
            attrs = G.nodes[node]
            last_updated = attrs.get("last_updated", "")
            if last_updated != today:
                details["orphan_nodes"].append({
                    "name": node,
                    "degree": 0,
                    "last_updated": last_updated,
                })

    # ── Check 4: Frontmatter completeness (reuse cached content) ──
    required_fields = {"entity_type", "confidence", "tags", "created"}
    for name, (path, content) in md_files.items():
        rel_path = os.path.relpath(path, vault)
        if not content.startswith("---"):
            details["incomplete_frontmatter"].append({"file": rel_path, "missing": sorted(required_fields)})
            continue
        end = content.find("---", 3)
        if end == -1:
            details["incomplete_frontmatter"].append({"file": rel_path, "missing": sorted(required_fields)})
            continue
        fm = content[3:end]
        present = {line.split(":")[0].strip() for line in fm.split("\n") if line.strip()}
        missing = required_fields - present
        if missing:
            details["incomplete_frontmatter"].append({"file": rel_path, "missing": sorted(missing)})

    total_issues = sum(len(v) for v in details.values())
    print(json.dumps({
        "status": "ok",
        "checks": 4,
        "issues": total_issues,
        "details": details,
    }, indent=2))


# ── worker ─────────────────────────────────────────────

WORKER_DEFAULTS = {
    "worker_claude_bin": "claude",
    "worker_model": None,
    "worker_consolidation_every": 10,
    # Max pending turns drained per worker run. Bounds the extraction prompt
    # (~20KB/turn cap → ≤~400KB) and keeps a huge backlog from producing one
    # unmanageable batch; leftover lines are picked up by the next run.
    "worker_max_turns_per_run": 20,
}

# Fallback extraction rules, used only if plugin/commands/engram.md cannot be
# loaded at runtime (e.g. unusual install layout). The .md file is the source
# of truth; keep this constant a faithful summary of its rules + schema.
FALLBACK_EXTRACTION_RULES = """\
### Extraction criteria
Extract projects, tools, and concepts worked on, debugged, designed, or decided
about — only if at least one applies: design decision (chose/rejected/compared),
bug/fix, built/modified code, or core architecture. Do NOT extract transient
actions or the user themselves.

### Naming
UPPERCASE with underscores (e.g. CLAUDE_SLACK_BRIDGE). Match existing entity
names when referring to the same thing.

### Confidence tagging
Every entity and relation MUST include "confidence":
EXTRACTED (explicitly discussed) | INFERRED (mentioned in passing) |
AMBIGUOUS (unclear relevance).

### Weight scale (relations)
0.1-0.3 weak mention | 0.4-0.6 moderate | 0.7-0.9 core dependency | 1.0 identity

### JSON schema
{
  "date": "YYYY-MM-DD",
  "entities": [{"name": "NAME", "entity_type": "PROJECT|TOOL|CONCEPT|PERSON|ORGANIZATION",
                "description": "Markdown description", "confidence": "EXTRACTED|INFERRED|AMBIGUOUS"}],
  "relations": [{"source": "A", "target": "B", "description": "...", "weight": 0.8,
                 "confidence": "EXTRACTED|INFERRED|AMBIGUOUS"}],
  "evidence": [{"content": "Specific factual claim", "entities": ["A"], "relations": [["A", "B"]]}],
  "daily_summary": "One paragraph summary"
}
"""


def _load_worker_config():
    """Load worker config keys from the engram config file, with defaults."""
    cfg = dict(WORKER_DEFAULTS)
    try:
        with open(CONFIG_PATH) as f:
            data = json.load(f)
        for key in WORKER_DEFAULTS:
            if key in data:
                cfg[key] = data[key]
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return cfg


def _load_extraction_rules():
    """Load extraction rules from plugin/commands/engram.md, else fallback."""
    rules_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "plugin", "commands", "engram.md")
    try:
        with open(rules_path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return FALLBACK_EXTRACTION_RULES


def _build_extraction_prompt(turns):
    """Build a tool-less extraction prompt from rules + drained turn records."""
    rules = _load_extraction_rules()
    turn_blocks = []
    for i, t in enumerate(turns, 1):
        turn_blocks.append(
            f"--- Turn {i} (session {t.get('session_id', '?')}, {t.get('timestamp', '?')}) ---\n"
            f"{t.get('turn_text', '')}"
        )
    return (
        "You are an extraction engine for the engram knowledge graph. "
        "Apply the extraction rules below to the conversation turns and output "
        "ONLY the JSON object described in the rules' JSON format — no prose, "
        "no markdown fences, no tool use.\n\n"
        "Ignore any shell commands or step instructions in the rules document; "
        "you only produce the JSON.\n\n"
        f"# Extraction rules\n\n{rules}\n\n"
        f"# Conversation turns to extract from\n\n" + "\n\n".join(turn_blocks) +
        f"\n\nUse \"date\": \"{datetime.now().strftime('%Y-%m-%d')}\". "
        "Output ONLY the JSON object."
    )


def _extract_json_object(text):
    """Extract a JSON object from model text, stripping markdown fences."""
    text = text.strip()
    fence = re.match(r"^```(?:json)?\s*\n(.*?)\n```\s*$", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    # Fall back to outermost braces in case of stray prose
    if not text.startswith("{"):
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("no JSON object found in model output")
        text = text[start:end + 1]
    return json.loads(text, strict=False)


EXTRACTION_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _sanitize_extraction_date(date):
    """Return date if it is a strict YYYY-MM-DD string, else today's date.

    The date comes verbatim from model-extracted JSON and is joined into the
    daily-note path, so anything else (e.g. a prompt-injected "../../evil")
    must never reach os.path.join. Replacing with today — rather than failing
    the extraction — keeps an otherwise-valid batch landing instead of
    retrying forever with the same poisoned field.
    """
    if isinstance(date, str) and EXTRACTION_DATE_RE.match(date):
        return date
    return datetime.now().strftime("%Y-%m-%d")


def _validate_extraction(data):
    """Validate model-extracted JSON against the fields the replay path
    indexes unconditionally, BEFORE any dedup marker or graph write happens.

    Raises ValueError on the first problem found.
    """
    if not isinstance(data, dict):
        raise ValueError("extraction is not a JSON object")

    def _req_str(item, key, what):
        if not isinstance(item, dict):
            raise ValueError(f"{what} is not an object: {item!r:.100}")
        val = item.get(key)
        if not isinstance(val, str) or not val.strip():
            raise ValueError(f"{what} missing required string field '{key}'")

    for key in ("entities", "relations", "evidence", "hyperedges"):
        if not isinstance(data.get(key, []), list):
            raise ValueError(f"'{key}' is not a list")

    for e in data.get("entities", []):
        _req_str(e, "name", "entity")
    for r in data.get("relations", []):
        _req_str(r, "source", "relation")
        _req_str(r, "target", "relation")
    for h in data.get("hyperedges", []):
        _req_str(h, "id", "hyperedge")
        _req_str(h, "label", "hyperedge")
    for ev in data.get("evidence", []):
        if not isinstance(ev, dict):
            raise ValueError(f"evidence item is not an object: {ev!r:.100}")
        if not isinstance(ev.get("entities", []), list) or not all(
                isinstance(x, str) for x in ev.get("entities", [])):
            raise ValueError("evidence 'entities' must be a list of strings")
        for rel in ev.get("relations", []):
            if not isinstance(rel, (list, tuple)) or not all(
                    isinstance(x, str) for x in rel):
                raise ValueError("evidence 'relations' items must be pairs of strings")


def _worker_log(vault, message):
    """Append one timestamped line to _meta/worker.log."""
    log_path = os.path.join(vault, "_meta", "worker.log")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now().isoformat()} {message}\n")


def _read_worker_state(vault):
    try:
        with open(os.path.join(vault, "_meta", "worker-state.json")) as f:
            state = json.load(f)
        if not isinstance(state, dict):
            raise ValueError
        return state
    except (OSError, ValueError):
        return {"offset": 0, "replays_since_consolidation": 0}


def _write_worker_state(vault, state):
    path = os.path.join(vault, "_meta", "worker-state.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(state, f, indent=2)
    os.rename(tmp_path, path)  # atomic: never leaves a torn state file


def _drain_pending(vault, offset, max_turns=None):
    """Read complete JSON lines from pending.jsonl past offset.

    Returns (turns, new_offset). Malformed lines are skipped but still
    advance the offset. A truncated/rotated file (offset > size) resets to 0.
    With max_turns set, at most that many turn records are consumed and
    new_offset only advances past the lines actually consumed — leftover
    backlog is picked up by the next run.
    """
    pending_path = os.path.join(vault, "_meta", "pending.jsonl")
    if not os.path.exists(pending_path):
        return [], offset if offset == 0 else 0
    size = os.path.getsize(pending_path)
    if offset > size:
        offset = 0  # file was truncated/rotated
    with open(pending_path, "rb") as f:
        f.seek(offset)
        data = f.read()
    last_nl = data.rfind(b"\n")
    if last_nl == -1:
        return [], offset  # no complete new line
    complete = data[:last_nl + 1]
    turns = []
    new_offset = offset
    pos = 0
    while pos < len(complete):
        nl = complete.index(b"\n", pos)
        raw_line = complete[pos:nl + 1]
        pos = nl + 1
        line = raw_line.decode("utf-8", errors="replace").strip()
        if line:
            try:
                record = json.loads(line)
                if isinstance(record, dict) and record.get("turn_text"):
                    turns.append(record)
            except json.JSONDecodeError:
                pass  # skip malformed line, offset still advances
        new_offset += len(raw_line)
        if max_turns is not None and len(turns) >= max_turns:
            break
    return turns, new_offset


def _refresh_context_cache(vault):
    """Rebuild _meta/context-cache.md in-process (Task 2 helper)."""
    context_text, _ = _build_context_text(vault)
    meta_dir = os.path.join(vault, "_meta")
    os.makedirs(meta_dir, exist_ok=True)
    with open(os.path.join(meta_dir, "context-cache.md"), "w", encoding="utf-8") as f:
        f.write(context_text)


def _maybe_consolidate(vault, state, threshold):
    """Slow-cadence maintenance: run non-LLM lint, mark consolidation due."""
    if state.get("replays_since_consolidation", 0) < threshold:
        return
    # Run the non-LLM lint in-process, suppressing its stdout report
    try:
        lint_args = argparse.Namespace(vault=vault)
        with contextlib.redirect_stdout(io.StringIO()):
            cmd_lint(lint_args)
    except Exception:
        pass  # maintenance is best-effort; never fail the worker for it
    marker = os.path.join(vault, "_meta", "consolidation-due")
    with open(marker, "w") as f:
        f.write(datetime.now().isoformat() + "\n")
    state["replays_since_consolidation"] = 0


def cmd_worker(args):
    """Drain pending.jsonl, extract via headless claude, land via replay."""
    vault = args.vault
    meta_dir = os.path.join(vault, "_meta")
    os.makedirs(meta_dir, exist_ok=True)

    # 1. Single-flight lock: another worker running → exit 0 immediately
    lock_file = open(os.path.join(meta_dir, "worker.lock"), "w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        # Another worker holds the lock — not an error
        lock_file.close()
        print(json.dumps({"processed": 0, "entities": 0, "relations": 0}))
        return

    try:
        cfg = _load_worker_config()
        state = _read_worker_state(vault)
        max_turns = cfg["worker_max_turns_per_run"]
        if not isinstance(max_turns, int) or max_turns < 1:
            max_turns = WORKER_DEFAULTS["worker_max_turns_per_run"]
        turns, new_offset = _drain_pending(vault, state.get("offset", 0),
                                           max_turns=max_turns)

        # 2. Nothing new → cheap idempotent no-op
        if not turns:
            if new_offset != state.get("offset", 0):
                state["offset"] = new_offset
                _write_worker_state(vault, state)
            print(json.dumps({"processed": 0, "entities": 0, "relations": 0}))
            return

        # 3. Headless tool-less extraction (model returns JSON text only).
        # The prompt goes via STDIN (`claude -p` reads it there when no
        # positional prompt is given): argv would hit Linux MAX_ARG_STRLEN
        # (~128KB) on a large backlog and leak conversation text into `ps`.
        prompt = _build_extraction_prompt(turns)
        cmd = [cfg["worker_claude_bin"], "-p", "--output-format", "json"]
        if cfg["worker_model"]:
            cmd += ["--model", cfg["worker_model"]]
        if args.verbose:
            print(f"worker: invoking {cfg['worker_claude_bin']} for {len(turns)} turn(s)",
                  file=sys.stderr)
        try:
            proc = subprocess.run(cmd, input=prompt, capture_output=True,
                                  text=True, timeout=args.timeout)
            if proc.returncode != 0:
                raise RuntimeError(
                    f"claude exited {proc.returncode}: {proc.stderr.strip()[:500]}")
            envelope = json.loads(proc.stdout, strict=False)
            if not isinstance(envelope, dict) or "result" not in envelope:
                raise ValueError("claude output missing 'result' envelope field")
            if envelope.get("is_error"):
                raise RuntimeError(
                    f"claude reported an error: {str(envelope['result'])[:500]}")
            data = _extract_json_object(envelope["result"])

            # Validate BEFORE _replay_data so a batch that would crash
            # mid-landing never writes a dedup marker (which would make the
            # retry get "skipped" and permanently drop the batch)
            _validate_extraction(data)

            # 4. Land via the shared replay path (dedup applies unchanged)
            result = _replay_data(vault, data)
            entities = result.get("entities_added", 0)
            relations = result.get("relations_added", 0)

            # 5+6. Refresh cache + slow-cadence consolidation on real landings
            if result.get("status") == "ok":
                _refresh_context_cache(vault)
                state["replays_since_consolidation"] = \
                    state.get("replays_since_consolidation", 0) + 1
                _maybe_consolidate(vault, state, cfg["worker_consolidation_every"])

            # 7. Persist the watermark only after a fully successful run
            state["offset"] = new_offset
            _write_worker_state(vault, state)
        except Exception as e:
            # Leave the watermark unchanged so a later run retries these turns
            _worker_log(vault, f"ERROR turns={len(turns)} {type(e).__name__}: {e}")
            print(json.dumps({"error": str(e), "processed": 0,
                              "entities": 0, "relations": 0}))
            sys.exit(1)

        _worker_log(vault, f"OK turns={len(turns)} entities={entities} "
                           f"relations={relations} status={result.get('status')}")
        print(json.dumps({"processed": len(turns), "entities": entities,
                          "relations": relations}))
    finally:
        lock_file.close()


# ── status ──────────────────────────────────────────────

def cmd_status(args):
    """Show vault statistics."""
    graph = MemoryGraph(args.vault)
    vault = args.vault

    def count_md(d):
        p = os.path.join(vault, d)
        return len([f for f in os.listdir(p) if f.endswith(".md")]) if os.path.isdir(p) else 0

    queue_dir = os.path.join(vault, "_meta", "queue")
    pending = []
    if os.path.isdir(queue_dir):
        for fname in sorted(os.listdir(queue_dir)):
            if fname.endswith(".json"):
                try:
                    with open(os.path.join(queue_dir, fname)) as f:
                        q = json.load(f)
                    if q.get("status") == "pending":
                        pending.append({
                            "session_id": q.get("session_id", "?")[:12],
                            "timestamp": q.get("timestamp", "?"),
                            "preview": q.get("last_message_preview", "")[:100],
                        })
                except Exception:
                    pass

    # Entity list for dedup awareness
    entities = sorted(graph.all_entity_names())

    # God nodes (degree + PageRank)
    god_nodes = graph.god_nodes(top_n=10)

    G = graph._graph

    # Type distribution
    type_dist = defaultdict(int)
    for n in G.nodes():
        etype = G.nodes[n].get("entity_type", "UNKNOWN")
        type_dist[etype] += 1

    # Confidence distribution
    conf_dist = defaultdict(int)
    for n in G.nodes():
        conf = G.nodes[n].get("confidence", "EXTRACTED")
        conf_dist[conf] += 1

    # Graph density
    import networkx
    density = networkx.density(G) if G.number_of_nodes() > 1 else 0.0

    # Knowledge gaps
    knowledge_gaps = graph.knowledge_gaps()

    # Surprising connections + suggested questions
    communities = graph.detect_communities(min_size=2)
    surprising = graph.find_surprising_connections(communities)
    suggested = graph.suggested_questions(top_n=5)

    print(json.dumps({
        "nodes": graph.node_count,
        "edges": graph.edge_count,
        "consolidation_recommended": os.path.exists(
            os.path.join(vault, "_meta", "consolidation-due")),
        "density": round(density, 4),
        "type_distribution": dict(type_dist),
        "confidence_distribution": dict(conf_dist),
        "god_nodes": god_nodes,
        "knowledge_gaps": knowledge_gaps,
        "surprising_connections": surprising,
        "suggested_questions": suggested,
        "daily_notes": count_md("daily"),
        "patterns": count_md("patterns"),
        "communities": count_md("communities"),
        "pending_sessions": len(pending),
        "pending": pending[:5],
        "entities": entities,
        "vault_path": vault,
    }, indent=2))


# ── consolidation ─────────────────────────────────────────

def cmd_consolidation(args):
    """Show or reset consolidation tracking state."""
    vault = args.vault
    if args.reset:
        state = {
            "last_full_timestamp": datetime.now().isoformat(),
            "replay_count": 0,
        }
        _write_consolidation_state(vault, state)
        print(json.dumps({"status": "ok", "action": "reset", "state": state}))
    else:
        state = _read_consolidation_state(vault)
        cfg = _load_consolidation_config()
        tier, details = 0, {}
        replays = state.get("replay_count", 0)
        last_full = state.get("last_full_timestamp")
        days_since = None
        if last_full:
            try:
                days_since = (datetime.now() - datetime.fromisoformat(last_full)).days
            except (ValueError, TypeError):
                pass
        details = {"replay_count": replays, "days_since_full": days_since, "thresholds": cfg}
        if replays >= cfg["force_after_replays"] or (days_since is not None and days_since >= cfg["force_after_days"]):
            tier = 2
        elif replays >= cfg["remind_after_replays"] or (days_since is not None and days_since >= cfg["remind_after_days"]):
            tier = 1
        print(json.dumps({"status": "ok", "tier": tier, **details}, indent=2))


# ── main ────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(prog="engram", description="Persistent memory for Claude Code — knowledge graph in Obsidian vault")
    sub = parser.add_subparsers(dest="command", required=True)

    # Setup commands
    p = sub.add_parser("install", help="Register engram in ~/.claude/CLAUDE.md")
    p = sub.add_parser("uninstall", help="Remove engram from ~/.claude/CLAUDE.md")

    p = sub.add_parser("init", help="Initialize vault and save path to config")
    p.add_argument("init_path", nargs="?", default=None, help="Vault path (default: ~/.engram/vault)")

    p = sub.add_parser("auto", help="Toggle auto-capture on session end")
    p.add_argument("action", nargs="?", default="status", choices=["on", "off", "status"], help="on/off/status (default: status)")

    # Core commands
    p = sub.add_parser("status", help="Show vault statistics, god nodes, and pending sessions")
    p = sub.add_parser("query", help="Search knowledge graph with keyword + graph traversal")
    p.add_argument("--question", required=True, help="Question to search for")

    p = sub.add_parser("replay", help="Process CC-extracted entities/relations JSON from stdin")
    p.add_argument("--stdin", action="store_true", required=True, help="Read entity/relation JSON from stdin")

    p = sub.add_parser("integrate", help="Detect duplicates (no flag) or execute merges (--stdin)")
    p.add_argument("--stdin", action="store_true", help="Read merge instructions JSON from stdin")

    p = sub.add_parser("prune", help="Report decay scores (no flag) or archive entities (--stdin)")
    p.add_argument("--stdin", action="store_true", help="Read archive list JSON from stdin")

    p = sub.add_parser("community", help="Detect clusters (no flag) or save summaries (--stdin)")
    p.add_argument("--stdin", action="store_true", help="Read community summaries JSON from stdin")

    p = sub.add_parser("abstract", help="Gather daily notes for behavioral pattern discovery")

    p = sub.add_parser("save-pattern", help="Save CC-discovered behavioral patterns")
    p.add_argument("--stdin", action="store_true", required=True)

    p = sub.add_parser("feedback", help="Scan for correction callouts (no flag) or apply fixes (--stdin)")
    p.add_argument("--stdin", action="store_true", help="Read correction instructions JSON from stdin")

    p = sub.add_parser("context", help="Compact summary for system prompt injection")
    p.add_argument("--write-cache", action="store_true", help="Write markdown context to _meta/context-cache.md")

    p = sub.add_parser("lint", help="Validate vault consistency (GraphML vs markdown, dead links, orphans)")

    p = sub.add_parser("consolidation", help="Show or reset consolidation tracking state")
    p.add_argument("--reset", action="store_true", help="Reset counter (run after full consolidation)")

    p = sub.add_parser("worker", help="Drain pending turns, extract via headless claude, land in graph")
    p.add_argument("--timeout", type=int, default=300, help="Timeout in seconds for the claude call (default: 300)")
    p.add_argument("--verbose", action="store_true", help="Log progress to stderr")

    # Global option: all subparsers get --vault
    for name, sp in sub.choices.items():
        if name not in ("install", "uninstall"):
            sp.add_argument("--vault", default=None, help="Vault path (default: from config)")

    args = parser.parse_args()

    # Resolve vault: explicit --vault > config > fallback
    if hasattr(args, "vault") and args.vault is None:
        args.vault = _load_vault_path()

    if args.command == "auto":
        args.auto_action = args.action

    {"install": cmd_install, "uninstall": cmd_uninstall,
     "init": cmd_init, "auto": cmd_auto, "replay": cmd_replay, "integrate": cmd_integrate,
     "prune": cmd_prune, "community": cmd_community, "abstract": cmd_abstract,
     "save-pattern": cmd_save_pattern, "feedback": cmd_feedback,
     "status": cmd_status, "query": cmd_query,
     "context": cmd_context, "lint": cmd_lint,
     "consolidation": cmd_consolidation, "worker": cmd_worker}[args.command](args)


if __name__ == "__main__":
    main()
