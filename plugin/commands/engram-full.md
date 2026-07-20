# Engram Full — Complete memory consolidation

> **Note:** The background worker handles routine replay automatically. This command kicks off **full consolidation** (feedback, integrate, prune, community, abstract, lint) as a **background job** — typically run when the `_meta/consolidation-due` marker exists. It does not block the session.

## Steps

**1. Launch the background consolidation:**

```bash
engram consolidate --detach
```

This returns immediately with `{"status": "started", "log": ...}`. A detached process runs all stages headlessly (each judgment stage — feedback, integrate, prune, community, abstract — calls `claude -p` itself), then lints and resets the consolidation counter.

**2. Tell the user** consolidation is running in the background and where the log is (`_meta/consolidate.log` in the vault). Do NOT wait for it or poll the log.

If the output is `{"status": "skipped", "reason": "another consolidate is running"}`, tell the user a run is already in progress.

## Interactive fallback

Only if the user explicitly asks to run consolidation **interactively** (e.g. to review merges/archives before they apply), run the stages in the session instead — each report command, review its output, then pipe the decision JSON back:

1. **Replay** — run `/engram` first (extract entities from the current session).
2. **Feedback** — `engram feedback`, review callouts, apply via `engram feedback --stdin` (`{"corrections": [...], "merges": [...], "deletes": [...]}`).
3. **Integrate** — `engram integrate`, review duplicate candidates, merge via `engram integrate --stdin` (`{"merges": [{"canonical": "KEEP", "aliases": ["REMOVE"]}]}`).
4. **Prune** — `engram prune`, review fading/archivable, archive via `engram prune --stdin` (`{"archive": ["ENTITY"]}`). Isolated nodes (degree 0) are strong candidates.
5. **Community** — `engram community`, write a title + summary per cluster, save via `engram community --stdin` (`{"communities": [{"id": 0, "title": "...", "summary": "...", "members": [...]}]}`).
6. **Abstract** — `engram abstract`, mine daily notes for recurring behaviors, save via `engram save-pattern --stdin` (`{"new_patterns": [{"name": "...", "description": "...", "evidence": ["YYYY-MM-DD"], "confidence": 0.7}], "updated_patterns": []}`).
7. **Lint** — `engram lint`; fix any issues (dead wikilinks, orphans).
8. **Reset** — `engram consolidation --reset`.
