#!/usr/bin/env python3
"""Engram CLI — called by CC /engram command.

Usage:
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
    All commands accept optional --vault PATH to override the saved default.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime

from engram.graph import MemoryGraph

CONFIG_PATH = os.path.expanduser("~/.engram/config.json")
FALLBACK_VAULT = os.path.expanduser("~/.engram/vault")
GRAPH_FIELD_SEP = "<SEP>"


def _load_vault_path():
    """Load vault path from config, falling back to default."""
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f).get("vault", FALLBACK_VAULT)
    except (FileNotFoundError, json.JSONDecodeError):
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
            subprocess.run(["git", "push"], cwd=vault, capture_output=True, timeout=30)
    except Exception:
        pass  # Never fail the engram because of git


# ── init ───────────────────────────────────────────────

def cmd_init(args):
    """Initialize a new engram vault directory."""
    vault = args.init_path or FALLBACK_VAULT
    vault = os.path.abspath(vault)
    os.makedirs(os.path.join(vault, "_meta"), exist_ok=True)
    _save_vault_path(vault)
    print(json.dumps({"status": "ok", "vault": vault}))


def cmd_auto(args):
    """Toggle auto-replay hook on/off."""
    vault = args.vault
    hook_flag = os.path.join(vault, "_meta", "hook-enabled")
    if args.auto_action == "on":
        os.makedirs(os.path.dirname(hook_flag), exist_ok=True)
        open(hook_flag, "w").close()
        print(json.dumps({"status": "ok", "auto_replay": "enabled"}))
    elif args.auto_action == "off":
        if os.path.exists(hook_flag):
            os.remove(hook_flag)
        print(json.dumps({"status": "ok", "auto_replay": "disabled"}))
    else:
        enabled = os.path.exists(hook_flag)
        print(json.dumps({"status": "ok", "auto_replay": "enabled" if enabled else "disabled"}))


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


def cmd_replay(args):
    """Process CC-extracted entities/relations JSON from stdin."""
    data = json.load(sys.stdin)
    vault = args.vault

    # Dedup check: skip if same content was saved within 15 minutes
    if _check_dedup(vault, data):
        print(json.dumps({
            "status": "skipped",
            "reason": "duplicate replay detected within 15-minute window",
        }))
        return

    graph = MemoryGraph(vault)

    entities = data.get("entities", [])
    relations = data.get("relations", [])
    date = data.get("date", datetime.now().strftime("%Y-%m-%d"))
    summary = data.get("daily_summary", "")

    for e in entities:
        graph.upsert_entity(e["name"], {
            "entity_type": e.get("entity_type", "CONCEPT"),
            "description": e.get("description", ""),
            "source_id": f"session-{date}",
            "last_updated": date,
        })

    for r in relations:
        graph.upsert_relation(r["source"], r["target"], {
            "description": r.get("description", ""),
            "weight": r.get("weight", 1.0),
        })

    graph.save()

    # ── FIX: daily note dedup — replace same-date note instead of appending ──
    daily_dir = os.path.join(vault, "daily")
    os.makedirs(daily_dir, exist_ok=True)
    daily_path = os.path.join(daily_dir, f"{date}.md")

    # Accumulate sessions for the day
    existing_entities = set()
    existing_sessions = []
    if os.path.exists(daily_path):
        with open(daily_path, "r") as f:
            content = f.read()
        # Parse existing entity refs
        existing_entities = set(re.findall(r'\[\[([A-Z_]+)\]\]', content))
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

    entity_names = [e["name"] for e in entities]
    all_entities = existing_entities | set(entity_names)
    session_count = len(existing_sessions) + 1

    # Collect entity types for tags
    entity_types = set()
    for e in entities:
        entity_types.add(e.get("entity_type", "CONCEPT").lower())

    lines = [
        "---",
        f"date: {date}",
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
    lines += ["", "## Entities", ""]
    for name in sorted(all_entities):
        e = next((x for x in entities if x["name"] == name), None)
        if e:
            etype = e.get('entity_type', '?')
            lines.append(f"- [[{name}]] `{etype}` — {e.get('description', '')[:120]}")
        else:
            lines.append(f"- [[{name}]]")

    with open(daily_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    # Clear queue
    _clear_queue(vault)

    # Sync vault to git
    _git_sync(vault, f"engram replay {date}: +{len(entities)} entities, +{len(relations)} relations")

    print(json.dumps({
        "status": "ok",
        "entities_added": len(entities),
        "relations_added": len(relations),
        "total_nodes": graph.node_count,
        "total_edges": graph.edge_count,
        "daily_note": daily_path,
    }))


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
                    q["status"] = "processed"
                    with open(qpath, "w") as f:
                        json.dump(q, f)
            except Exception:
                pass


# ── integrate ───────────────────────────────────────────

def cmd_integrate(args):
    """Find duplicates, or execute merge instructions from stdin."""
    graph = MemoryGraph(args.vault)

    if args.stdin:
        # Execute merge: {"merges": [{"canonical": "A", "aliases": ["B", "C"]}]}
        data = json.load(sys.stdin)
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
            if len(token) > 2:
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
                    desc = (attrs.get("description", "") or "").split(GRAPH_FIELD_SEP)[0][:120]
                    details.append({"name": n, "description": desc, "degree": graph._graph.degree(n)})
                candidates.append(details)

    print(json.dumps({
        "status": "ok",
        "total_entities": len(names),
        "duplicate_candidates": candidates[:20],
        "message": "Review candidates. To merge, pipe: {\"merges\": [{\"canonical\": \"KEEP_NAME\", \"aliases\": [\"REMOVE_NAME\"]}]} | engram integrate --vault PATH --stdin",
    }))


_TYPE_FOLDER = {
    "PERSON": "people", "CONCEPT": "concepts", "PROJECT": "projects",
    "TOOL": "tools", "ORGANIZATION": "orgs", "ORG": "orgs",
    "EVENT": "concepts", "LOCATION": "concepts",
}


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
        data = json.load(sys.stdin)
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
        data = json.load(sys.stdin)
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
    print(json.dumps({
        "status": "ok",
        "total_nodes": graph.node_count,
        "total_edges": graph.edge_count,
        "communities_found": len(communities),
        "communities": communities,
        "message": "Review communities. For each, generate a title and summary. Then pipe: {\"communities\": [{\"id\": 0, \"title\": \"...\", \"summary\": \"...\", \"members\": [\"A\", \"B\"]}]} | engram community --vault PATH --stdin",
    }, indent=2))


# ── query (enhanced with graph traversal) ───────────────

def cmd_query(args):
    """Search knowledge graph with keyword match + multi-hop traversal."""
    graph = MemoryGraph(args.vault)
    question = args.question
    names = graph.all_entity_names()
    tokens = [re.sub(r'[^\w]', '', t).upper() for t in question.split() if len(t) > 2]
    tokens = [t for t in tokens if t]  # remove empty after stripping

    # 1. Keyword match on entity names
    matched = [n for n in names if any(tok in n for tok in tokens)]

    # 2. Also match on descriptions
    if len(matched) < 3:
        for n in names:
            if n in matched:
                continue
            attrs = graph.get_entity(n)
            desc = (attrs.get("description", "") or "").lower()
            if any(tok.lower() in desc for tok in tokens):
                matched.append(n)
            if len(matched) >= 10:
                break

    # 3. Multi-hop: expand to neighbors of matched entities
    expanded = set(matched)
    for name in matched[:5]:
        for neighbor, _ in graph.get_neighbors(name):
            expanded.add(neighbor)

    # 4. Build rich context
    context_parts = []
    for name in matched[:10]:
        attrs = graph.get_entity(name)
        neighbors = graph.get_neighbors(name)
        desc = (attrs.get("description", "") or "").split(GRAPH_FIELD_SEP)[0]
        neighbor_strs = []
        for n, d in neighbors[:8]:
            ndesc = (d.get("description", "") or "").split(GRAPH_FIELD_SEP)[0][:80]
            weight = d.get("weight", "?")
            neighbor_strs.append(f"  - [[{n}]]: {ndesc} (w:{weight})")
        context_parts.append(f"## {name}\n{desc}\n### Relations\n" + "\n".join(neighbor_strs))

    # 5. Load community context for matched entities
    community_context = []
    comm_dir = os.path.join(args.vault, "communities")
    if os.path.isdir(comm_dir):
        matched_set = set(matched)
        for fname in os.listdir(comm_dir):
            if not fname.endswith(".md"):
                continue
            fpath = os.path.join(comm_dir, fname)
            with open(fpath, "r") as f:
                content = f.read()
            # Check if any matched entity is a member of this community
            community_members = set(re.findall(r'\[\[([A-Z_]+)\]\]', content))
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
                    expanded |= community_members

    print(json.dumps({
        "question": question,
        "matched_entities": matched[:10],
        "expanded_entities": sorted(expanded - set(matched))[:10],
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
    data = json.load(sys.stdin)
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
            f"last_updated: {datetime.now().strftime('%Y-%m-%d')}",
            f"cssclasses:",
            f"  - pattern",
            "---", "",
            f"# {name}", "",
            p.get("description", ""), "",
            "## Evidence", "",
        ]
        for e in evidence:
            lines.append(f"- [[{e}]]")
        lines.append("")

        with open(os.path.join(pattern_dir, fname), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        saved.append(name)

    _git_sync(vault, f"engram abstract: {len(saved)} patterns")
    print(json.dumps({"status": "ok", "patterns_saved": saved}))


# ── context ────────────────────────────────────────────

def cmd_context(args):
    """Output compact summary for injecting into agent system prompt.

    Inspired by community/engram's mem_context — returns recent activity
    and top entities in a token-efficient format.
    """
    graph = MemoryGraph(args.vault)
    vault = args.vault

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
            desc = (attrs.get("description", "") or "").split(GRAPH_FIELD_SEP)[0][:100]
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

    context_text = "\n\n".join(parts)

    print(json.dumps({
        "context": context_text,
        "entity_count": graph.node_count,
        "edge_count": graph.edge_count,
    }, indent=2))


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

    # Hub entities (top 5 by degree)
    G = graph._graph
    hubs = sorted(
        [(n, G.degree(n)) for n in G.nodes()],
        key=lambda x: -x[1],
    )[:5]

    # Type distribution
    type_dist = defaultdict(int)
    for n in G.nodes():
        etype = G.nodes[n].get("entity_type", "UNKNOWN")
        type_dist[etype] += 1

    # Graph density
    import networkx
    density = networkx.density(G) if G.number_of_nodes() > 1 else 0.0

    print(json.dumps({
        "nodes": graph.node_count,
        "edges": graph.edge_count,
        "density": round(density, 4),
        "type_distribution": dict(type_dist),
        "hub_entities": [{"name": n, "degree": d} for n, d in hubs],
        "daily_notes": count_md("daily"),
        "patterns": count_md("patterns"),
        "communities": count_md("communities"),
        "pending_sessions": len(pending),
        "pending": pending[:5],
        "entities": entities,
        "vault_path": vault,
    }, indent=2))


# ── main ────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Engram CLI")
    parser.add_argument("command", choices=["init", "auto", "replay", "integrate", "prune", "community", "abstract", "save-pattern", "status", "query", "context"])
    parser.add_argument("init_path", nargs="?", default=None, help="Positional arg: vault path for init, on/off/status for auto")
    parser.add_argument("--vault", default=None)
    parser.add_argument("--stdin", action="store_true")
    parser.add_argument("--question", default="")
    args = parser.parse_args()

    # Resolve vault: explicit --vault > config > fallback
    if args.vault is None:
        args.vault = _load_vault_path()

    if args.command == "auto":
        args.auto_action = args.init_path or "status"

    {"init": cmd_init, "auto": cmd_auto, "replay": cmd_replay, "integrate": cmd_integrate,
     "prune": cmd_prune, "community": cmd_community, "abstract": cmd_abstract,
     "save-pattern": cmd_save_pattern,
     "status": cmd_status, "query": cmd_query,
     "context": cmd_context}[args.command](args)


if __name__ == "__main__":
    main()
