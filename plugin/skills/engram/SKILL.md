---
name: engram
description: Work with the Engram persistent-memory knowledge graph. Use when the user invokes $engram or asks Codex to extract durable knowledge from the current task, recall or query prior work, inspect graph status, process memory feedback, detect communities, run consolidation, or configure automatic capture and injection.
---

# Engram

Use the installed `engram` CLI and select exactly the workflow that matches the request.

## Workflow Routing

- Deep extraction or a bare `$engram`: read `../../commands/engram.md` completely and follow it.
- Query, recall, or search: read `../../commands/engram-query.md` completely and follow it.
- Graph statistics or health: read `../../commands/engram-status.md` completely and follow it.
- Full consolidation: read `../../commands/engram-full.md` completely and follow it.
- Obsidian corrections: read `../../commands/engram-feedback.md` completely and follow it.
- Community detection: read `../../commands/engram-community.md` completely and follow it.
- Enable automatic capture: read `../../commands/engram-on.md` completely and follow it.
- Disable automatic capture: read `../../commands/engram-off.md` completely and follow it.

For independent controls, use `engram auto capture-only` or
`engram auto injection-only`; `engram auto status` reports both features.

Replace `$ARGUMENTS` in a selected workflow with the user's arguments. Do not load unrelated workflow files.

Before running a workflow, verify that `engram` is available on `PATH`. Treat the vault as user data: request approval when the active sandbox requires it, and never report a write as successful until the command succeeds.

Codex lifecycle hooks are installed separately by `engram codex-setup`; installing this skill does not replace that setup.
