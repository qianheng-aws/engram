#!/usr/bin/env python3
"""OODA Dream CLI — called by CC /dream command.

Usage:
    dream_cli.py replay --stdin          # Read extracted JSON from stdin, write to graph + vault
    dream_cli.py integrate --vault PATH  # Merge duplicate entities
    dream_cli.py prune --vault PATH      # Decay and archive old entities
    dream_cli.py status --vault PATH     # Show vault statistics
    dream_cli.py query --vault PATH --question "..."  # Search knowledge graph
"""

import argparse
import json
import os
import sys
from datetime import datetime

from ooda_memory.graph import MemoryGraph


DEFAULT_VAULT = os.path.expanduser("~/vault")


def cmd_replay(args):
    """Process CC-extracted entities/relations JSON from stdin."""
    data = json.load(sys.stdin)
    vault = args.vault
    graph = MemoryGraph(vault)

    entities = data.get("entities", [])
    relations = data.get("relations", [])
    date = data.get("date", datetime.now().strftime("%Y-%m-%d"))
    summary = data.get("daily_summary", "")

    # Upsert entities
    for e in entities:
        graph.upsert_entity(e["name"], {
            "entity_type": e.get("entity_type", "CONCEPT"),
            "description": e.get("description", ""),
            "source_id": f"session-{date}",
            "last_updated": date,
        })

    # Upsert relations
    for r in relations:
        graph.upsert_relation(r["source"], r["target"], {
            "description": r.get("description", ""),
            "weight": r.get("weight", 1.0),
        })

    # Save graph + export markdown
    graph.save()

    # Write daily note
    daily_dir = os.path.join(vault, "daily")
    os.makedirs(daily_dir, exist_ok=True)
    daily_path = os.path.join(daily_dir, f"{date}.md")

    entity_names = [e["name"] for e in entities]
    lines = [
        "---",
        f"date: {date}",
        f"entities_extracted: {json.dumps(entity_names)}",
        f"relations_count: {len(relations)}",
        "---", "",
        f"# {date}", "",
        summary, "",
        "## Entities", "",
    ]
    for e in entities:
        lines.append(f"- [[{e['name']}]] ({e.get('entity_type', '?')}): {e.get('description', '')[:100]}")

    # Append if daily note already exists
    mode = "a" if os.path.exists(daily_path) else "w"
    with open(daily_path, mode, encoding="utf-8") as f:
        if mode == "a":
            f.write("\n---\n\n")
        f.write("\n".join(lines) + "\n")

    # Clear any queued breadcrumbs for today
    queue_dir = os.path.join(vault, "_meta", "queue")
    if os.path.isdir(queue_dir):
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

    print(json.dumps({
        "status": "ok",
        "entities_added": len(entities),
        "relations_added": len(relations),
        "total_nodes": graph.node_count,
        "total_edges": graph.edge_count,
        "daily_note": daily_path,
    }))


def cmd_integrate(args):
    """Find and report duplicate entities for CC to merge."""
    graph = MemoryGraph(args.vault)
    names = graph.all_entity_names()

    # Simple duplicate detection: Levenshtein-like grouping by shared tokens
    from collections import defaultdict
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
                candidates.append(list(group))

    print(json.dumps({
        "status": "ok",
        "total_entities": len(names),
        "duplicate_candidates": candidates[:20],
        "message": "Review candidates and merge with: echo '{...}' | dream_cli.py replay --stdin",
    }))


def cmd_prune(args):
    """Report entities by decay score for CC to decide on archival."""
    graph = MemoryGraph(args.vault)
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
        "message": "Review and confirm archival. Fading entities will have descriptions simplified.",
    }))


def cmd_status(args):
    """Show vault statistics."""
    graph = MemoryGraph(args.vault)
    vault = args.vault

    # Count files
    daily_count = len([f for f in os.listdir(os.path.join(vault, "daily")) if f.endswith(".md")]) if os.path.isdir(os.path.join(vault, "daily")) else 0
    pattern_count = len([f for f in os.listdir(os.path.join(vault, "patterns")) if f.endswith(".md")]) if os.path.isdir(os.path.join(vault, "patterns")) else 0
    dream_count = len([f for f in os.listdir(os.path.join(vault, "dreams")) if f.endswith(".md")]) if os.path.isdir(os.path.join(vault, "dreams")) else 0

    # Count pending queue
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

    print(json.dumps({
        "nodes": graph.node_count,
        "edges": graph.edge_count,
        "daily_notes": daily_count,
        "patterns": pattern_count,
        "dreams": dream_count,
        "pending_sessions": len(pending),
        "pending": pending[:5],
        "vault_path": vault,
    }, indent=2))


def cmd_query(args):
    """Search knowledge graph and return context for CC."""
    graph = MemoryGraph(args.vault)
    question = args.question
    names = graph.all_entity_names()

    # Return entity list + any exact matches for CC to do routing
    # CC will pick relevant entities and ask for details
    exact = [n for n in names if any(token.upper() in n for token in question.split() if len(token) > 2)]

    context_parts = []
    for name in exact[:10]:
        attrs = graph.get_entity(name)
        neighbors = graph.get_neighbors(name)
        desc = (attrs.get("description", "") or "").split("<SEP>")[0]
        neighbor_strs = [f"[[{n}]]: {d.get('description', '').split('<SEP>')[0][:60]}" for n, d in neighbors[:5]]
        context_parts.append(f"## {name}\n{desc}\nRelations: {', '.join(neighbor_strs)}")

    print(json.dumps({
        "question": question,
        "matched_entities": exact[:10],
        "all_entities": names,
        "context": "\n\n".join(context_parts),
        "message": "Use context to answer. If no match, pick relevant entities from all_entities list.",
    }, indent=2))


def cmd_abstract(args):
    """Gather daily notes + existing patterns for CC to analyze."""
    vault = args.vault
    daily_dir = os.path.join(vault, "daily")
    pattern_dir = os.path.join(vault, "patterns")
    os.makedirs(pattern_dir, exist_ok=True)

    # Read recent daily notes (last 14 days or all if fewer)
    dailies = {}
    if os.path.isdir(daily_dir):
        for fname in sorted(os.listdir(daily_dir), reverse=True):
            if fname.endswith(".md"):
                with open(os.path.join(daily_dir, fname), "r") as f:
                    dailies[fname[:-3]] = f.read()
            if len(dailies) >= 14:
                break

    # Read existing patterns
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
            "---",
            f"name: {name}",
            f"confidence: {confidence}",
            f"evidence_count: {len(evidence)}",
            f"last_updated: {datetime.now().strftime('%Y-%m-%d')}",
            "---", "",
            f"# {name}", "",
            p.get("description", ""), "",
            "## Evidence", "",
        ]
        for e in evidence:
            lines.append(f"- [[{e}]]")

        with open(os.path.join(pattern_dir, fname), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        saved.append(name)

    print(json.dumps({"status": "ok", "patterns_saved": saved}))


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
