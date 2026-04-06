#!/usr/bin/env python3
"""OODA Dream CLI — called by CC /dream command.

Usage:
    dream_cli.py replay --stdin --vault PATH
    dream_cli.py integrate --vault PATH [--stdin]  # --stdin for merge instructions
    dream_cli.py prune --vault PATH [--stdin]      # --stdin for archive confirmation
    dream_cli.py abstract --vault PATH
    dream_cli.py save-pattern --vault PATH --stdin
    dream_cli.py status --vault PATH
    dream_cli.py query --vault PATH --question "..."
"""

import argparse
import json
import os
import re
import shutil
import sys
from collections import defaultdict
from datetime import datetime

from ooda_memory.graph import MemoryGraph

DEFAULT_VAULT = os.path.expanduser("~/.meshclaw/vault")
GRAPH_FIELD_SEP = "<SEP>"


# ── replay ──────────────────────────────────────────────

def cmd_replay(args):
    """Process CC-extracted entities/relations JSON from stdin."""
    data = json.load(sys.stdin)
    vault = args.vault
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
        # Keep existing sessions section
        if "## Sessions" in content:
            idx = content.index("## Sessions")
            existing_sessions = content[idx:].strip().split("\n")[2:]  # skip header + blank

    entity_names = [e["name"] for e in entities]
    all_entities = existing_entities | set(entity_names)

    lines = [
        "---",
        f"date: {date}",
        f"entities: {json.dumps(sorted(all_entities))}",
        f"sessions: {len(existing_sessions) + 1}",
        "---", "",
        f"# {date}", "",
        "## Summary", "",
    ]

    # Append new summary
    if existing_sessions:
        for s in existing_sessions:
            if s.strip():
                lines.append(s)
    lines.append(f"- {summary}")
    lines += ["", "## Entities", ""]
    for name in sorted(all_entities):
        e = next((x for x in entities if x["name"] == name), None)
        if e:
            lines.append(f"- [[{name}]] ({e.get('entity_type', '?')}): {e.get('description', '')[:100]}")
        else:
            lines.append(f"- [[{name}]]")

    with open(daily_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    # Clear queue
    _clear_queue(vault)

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
        "message": "Review candidates. To merge, pipe: {\"merges\": [{\"canonical\": \"KEEP_NAME\", \"aliases\": [\"REMOVE_NAME\"]}]} | dream_cli.py integrate --vault PATH --stdin",
    }))


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
                graph._graph.remove_node(name)
                archived.append(name)
        graph.save()
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
        "message": "To archive, pipe: {\"archive\": [\"ENTITY_A\"]} | dream_cli.py prune --vault PATH --stdin",
    }))


# ── query (enhanced with graph traversal) ───────────────

def cmd_query(args):
    """Search knowledge graph with keyword match + multi-hop traversal."""
    graph = MemoryGraph(args.vault)
    question = args.question
    names = graph.all_entity_names()
    tokens = [t.upper() for t in question.split() if len(t) > 2]

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

    print(json.dumps({
        "question": question,
        "matched_entities": matched[:10],
        "expanded_entities": sorted(expanded - set(matched))[:10],
        "all_entities": names,
        "context": "\n\n".join(context_parts),
        "message": "Use context to answer. expanded_entities are 1-hop neighbors that may be relevant.",
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
        "message": "Analyze daily notes for recurring behaviors. Output JSON with new_patterns and updated_patterns arrays. Each pattern: {name, description, evidence: [dates], confidence: 0.0-1.0}. Pipe result to: dream_cli.py save-pattern --vault PATH --stdin",
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

        lines = [
            "---", f"name: {name}", f"confidence: {confidence}",
            f"evidence_count: {len(evidence)}",
            f"last_updated: {datetime.now().strftime('%Y-%m-%d')}",
            "---", "", f"# {name}", "",
            p.get("description", ""), "", "## Evidence", "",
        ]
        for e in evidence:
            lines.append(f"- [[{e}]]")

        with open(os.path.join(pattern_dir, fname), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        saved.append(name)

    print(json.dumps({"status": "ok", "patterns_saved": saved}))


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

    print(json.dumps({
        "nodes": graph.node_count,
        "edges": graph.edge_count,
        "daily_notes": count_md("daily"),
        "patterns": count_md("patterns"),
        "dreams": count_md("dreams"),
        "pending_sessions": len(pending),
        "pending": pending[:5],
        "entities": entities,
        "vault_path": vault,
    }, indent=2))


# ── main ────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="OODA Dream CLI")
    parser.add_argument("command", choices=["replay", "integrate", "prune", "abstract", "save-pattern", "status", "query"])
    parser.add_argument("--vault", default=DEFAULT_VAULT)
    parser.add_argument("--stdin", action="store_true")
    parser.add_argument("--question", default="")
    args = parser.parse_args()

    {"replay": cmd_replay, "integrate": cmd_integrate, "prune": cmd_prune,
     "abstract": cmd_abstract, "save-pattern": cmd_save_pattern,
     "status": cmd_status, "query": cmd_query}[args.command](args)


if __name__ == "__main__":
    main()
