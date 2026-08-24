# Engram Memory-UX Redesign — Implementation Report

**Audience:** Claude Code (implementer). **Repo:** `/workplace/qianheng/engram`
**Status:** Design approved, ready to implement. **Author:** design session 2026-07-13.

---

## 0. TL;DR for the implementer

Engram's memory *storage* (NetworkX graph + Obsidian vault + confidence tagging +
god nodes + communities + evidence layer) is good and must be **preserved**.
Its *interaction UX* is the problem. Fix it by copying the architecture that makes
MeshClaw's memory feel effortless:

1. **Reads become PUSH, not PULL** — auto-inject relevant graph context into every
   prompt via a new `UserPromptSubmit` hook. (The payload builder already exists:
   `cmd_context`, `engram_cli.py:1014`. It is currently wired to *nothing*.)
2. **Capture becomes instant + automatic + LLM-free** — the `Stop` hook appends the
   full turn to an append-only queue. No model, no permission prompt, O(1).
3. **Extraction moves OUT-OF-BAND** — a background worker drains the queue
   incrementally and runs a **headless, tool-less `claude -p`** call that returns
   JSON to stdout, then pipes it to the existing `engram replay`. Never blocks the
   interactive session, never asks for permission.

**Hard constraint:** Engram stays **standalone and portable**. Do **NOT** introduce
any dependency on MeshClaw or any external agent runtime. Hooks must be stdlib-only,
fast, and fail-silent.

---

## 1. Background & goal

Engram is a persistent-memory plugin for Claude Code: it extracts entities and
relations from sessions into an Obsidian-vault knowledge graph (GraphML + markdown).
The owner finds the *storage* valuable but the *experience* frustrating, and by
contrast finds MeshClaw's memory "good enough." The goal is to give Engram
MeshClaw-grade UX **while keeping** Obsidian integration and the graph DB, and
**without** depending on MeshClaw (MeshClaw uses Claude Code as its LLM provider, so
depending on it would invert the dependency and bolt a heavy, upgrade-fragile system
onto what should be a lightweight plugin).

### The three reported pain points
1. **It doesn't auto-remember** important things — capture requires an explicit command.
2. **Claude doesn't actively retrieve** from the graph — only when explicitly told to.
3. **Extraction is slow** (tens of minutes) and **prompts for permission constantly**.

---

## 2. Root-cause analysis (grounded in current code)

All three symptoms reduce to **one architectural mismatch**: Engram uses *pull* reads
and *synchronous, foreground, LLM-driven* writes. MeshClaw uses *push* reads
(injection) and *async, background* writes (a consolidator + an instant append tool).

| Pain | Current mechanism (file:line) | Why it fails |
|------|------------------------------|--------------|
| #1 No auto-remember | `plugin/bin/engram-hook` (Stop hook) only writes a **breadcrumb** (`session_id` + `last_message_preview[:500]`) to `vault/_meta/queue/`, gated by `vault/_meta/hook-enabled`. Real extraction is the **`/engram` slash command** (`plugin/commands/engram.md`) — a *prompt* the user must run. | The breadcrumb is never turned into graph data automatically. Nothing bridges queue → extraction. Remembering is a manual ritual. |
| #2 No active retrieval | `plugin/bin/engram-pretool` (PreToolUse on `Grep\|Glob\|Read\|Agent`) does crude full-word token matching (`MIN_TOKEN_LEN=4`, `MAX_MATCHES=3`, `STOP_TOKENS`) against GraphML node ids. Plus manual `engram query` (`cmd_query`, `engram_cli.py:794`). | Matching is too weak and only fires on 4 tools, so it rarely injects anything → feels dead. The real injection payload builder `cmd_context` (`engram_cli.py:1014`) is **not wired to any hook**. There is **no** `UserPromptSubmit`/`SessionStart` injection. |
| #3 Slow + permission spam | Extraction + consolidation run **in the foreground interactive session** via `/engram` and `/engram-full` (`plugin/commands/engram-full.md`): up to 7 stages (replay → feedback → integrate → prune → community → abstract → lint), each an LLM step + a **Bash heredoc pipe** (`engram replay --stdin <<'JSON_EOF'`). | Synchronous and blocking; the main model re-reads the whole conversation; every `Bash`/`Write` triggers an approval; multi-stage = tens of minutes of babysitting. |

### What already exists and can be reused (do not rebuild)
- `cmd_context` (`engram_cli.py:1014`) → produces a compact injection block: top-10 hub
  entities by degree, last-3 daily summaries, patterns, suggested questions. **This is
  90% of the read-hook payload.**
- `cmd_query` (`engram_cli.py:794`) → keyword + graph-traversal retrieval (matched
  entities, community context, expanded 1–3 hop entities). **Use for prompt-matched
  retrieval.**
- `cmd_replay` (`engram_cli.py:355`) → the landing point for extracted JSON, with
  `_check_dedup` (15-min window + content hash) and evidence-layer handling. **The
  worker pipes into this unchanged.**
- `MemoryGraph.god_nodes(top_n)` (`engram/graph.py:463`), `get_entity`,
  `get_neighbors`, `all_entity_names`, `detect_communities`, `suggested_questions`.
- Consolidation commands: `feedback`, `integrate`, `prune`, `community`, `abstract`,
  `lint`, `consolidation --reset` — reuse from the worker on a slow cadence.
- Kill switch `vault/_meta/hook-enabled` and `cmd_auto` (`engram_cli.py:250`).

---

## 3. Target architecture

```
                         ┌─────────────────────────────────────────────┐
  Claude Code session    │                Engram (standalone)          │
                         │                                             │
  UserPromptSubmit  ─────┼─▶ engram-context hook (NEW, stdlib+CLI)     │
     (every prompt)      │     • reads cached stable digest (god nodes,│
                         │       recent activity)   ── fast, <300ms    │
                         │     • + prompt-matched subgraph (engram     │
                         │       query on the prompt)                  │
                         │     └▶ prints "## Memory Context" to stdout │  ← PUSH read
                         │                                             │
  Stop  ─────────────────┼─▶ engram-hook (UPGRADED, stdlib only)       │
     (turn ends)         │     └▶ append full turn to                  │
                         │        vault/_meta/pending.jsonl  (O(1),    │  ← instant capture
                         │        no LLM, no permission)               │
                         │                                             │
  (out of band) ─────────┼─▶ engram-worker (NEW, detached/periodic)    │
                         │     • drain pending.jsonl since watermark   │
                         │     • claude -p --output-format json        │  ← async write
                         │       (NO tools → NO approvals) → JSON       │
                         │     • pipe JSON to `engram replay --stdin`  │
                         │     • refresh cached stable digest          │
                         │     • every N replays: run consolidation    │
                         └─────────────────────────────────────────────┘
                                    │
                                    ▼
                    NetworkX GraphML + Obsidian vault (UNCHANGED)
```

### 3.1 Read path — `SessionStart` + `UserPromptSubmit` hooks (fixes #2)
- **New file:** `plugin/bin/engram-context` (executable, stdlib-preferred; may shell out
  to `engram context` / `engram query`).
- **Stable digest** — inject god nodes + recent activity + patterns once at
  `SessionStart`, reading `vault/_meta/context-cache.md` and falling back to
  `engram context` when the cache is absent.
- **Prompt-matched subgraph** — on `UserPromptSubmit`, read the entity index and emit
  only matching entities. No match means no injection. The fallback is
  `engram query --question "<keywords from prompt>"` when the index is unavailable.
- **Budget:** cap prompt blocks at 1,000 characters and session digests at 1,200
  characters by default. Truncate with an ellipsis marker.
- **Latency:** target < 300 ms. If the query step risks exceeding it, gate it behind a
  timeout and fall back to the cached digest only.
- **Register** prompt matching under `UserPromptSubmit` and inject the stable digest
  once under `SessionStart`.
- **Retire** the existing `PreToolUse` hook in both Claude Code and Codex.

### 3.2 Capture path — upgraded `Stop` hook (fixes #1)
- **Edit:** `plugin/bin/engram-hook`.
- Instead of a preview breadcrumb, append a structured record to an **append-only**
  `vault/_meta/pending.jsonl`: `{session_id, timestamp, turn_text}` (or
  `{session_id, jsonl_path, turn_range}` if the full transcript is retrievable — prefer
  storing enough text to extract from without re-reading the session file).
- Still **stdlib only**, fail-silent, and skips trivial turns. Capture and injection
  have separate flags with legacy `hook-enabled` compatibility. **No LLM.**
- Keep `timeout` small in `hooks.json` (Stop hook must be sub-100ms).

### 3.3 Extraction path — background worker (fixes #3)
- **New file:** `plugin/bin/engram-worker` (or `engram worker` subcommand in
  `engram_cli.py`). Runs **outside** the interactive turn.
- **Incremental drain:** read `pending.jsonl` from the last-processed offset stored in
  `vault/_meta/worker-state.json` (byte offset or line count watermark). Only new turns
  are processed — never the whole session.
- **Headless, tool-less extraction (the key to zero approvals):**
  - Build the prompt from the existing extraction rules in `plugin/commands/engram.md`
    (extraction criteria, naming, confidence tagging, JSON schema) + the drained turn text.
  - Call: `claude -p "<prompt>" --output-format json --model <cheap/fast model>`
    with **no tools requested** — the model only *returns JSON text*. Because it uses no
    tools and runs non-interactively, it **cannot trigger permission prompts**.
  - The **worker** (not the model) then pipes that JSON into the existing
    `engram replay --stdin`. All file/graph writes happen in trusted local code.
  - (Alternative if you prefer the model to run the pipe itself: launch with
    `--permission-mode acceptEdits` and an allowlist scoped to `Bash(engram replay:*)`,
    `Write(<vault>/**)`, `Read(<vault>/**)`. The tool-less approach above is simpler and
    strictly safer — prefer it.)
- **Reuse** `_check_dedup` and `cmd_replay` unchanged.
- **Refresh** `vault/_meta/context-cache.md` (the stable digest) at the end of each drain
  so the read hook serves fresh data.
- **Slow-cadence consolidation:** every N replays (reuse the existing `consolidation`
  tier signal, `engram_cli.py:1437` + `engram-full.md` stages), run
  feedback → integrate → prune → community → abstract → lint **inside the worker**, then
  `engram consolidation --reset`. The user never babysits it.
- **Trigger options (pick one; recommend A):**
  - **A. Detached spawn on session end** — a `SessionEnd` hook (or the existing Stop hook)
    fires-and-forgets `engram-worker` as a fully detached process (`setsid`/`nohup`,
    stdout/stderr to a log), so it survives the session and never blocks it.
  - B. Periodic drainer — a user-run daemon (`engram worker --watch`).
  - C. Drain-on-SessionStart — cheaper but delays freshness.
  - **Do NOT** use an external scheduler that couples Engram to another system.
- **Concurrency:** reuse/extend the existing file-lock pattern (fcntl) so overlapping
  worker runs don't corrupt the graph. Single-flight via a lock file in `_meta`.

---

## 4. Constraints & non-goals

**Must preserve:** NetworkX GraphML storage, Obsidian markdown output (wikilinks,
frontmatter, Dataview, Graph View), confidence tagging, god nodes, community detection,
evidence layer, dedup, the `hook-enabled` kill switch.

**Must NOT:** depend on MeshClaw or any external agent runtime; make hooks import
third-party packages (stdlib only — they run in Claude Code's hook sandbox); let any
hook block or slow the interactive turn; introduce interactive permission prompts in the
extraction path; re-extract the whole session (incremental only).

**Non-goals (this pass):** vector/embedding retrieval, multi-user concurrency, Neo4j
migration. Keep NetworkX — current scale is tiny.

**Portability:** honor `ENGRAM_CONFIG` env + `~/.engram/config.json` `vault` key
(both hooks already do via `_get_vault`). No hardcoded vault paths in new code.

---

## 5. Implementation tasks (ordered)

1. **Capture upgrade** — edit `plugin/bin/engram-hook`: append full-turn records to
   `vault/_meta/pending.jsonl` (append-only), keep stdlib-only + fail-silent + kill-switch.
2. **Worker** — add `engram worker` (subcommand in `engram_cli.py`) or
   `plugin/bin/engram-worker`:
   - watermark drain of `pending.jsonl` (`vault/_meta/worker-state.json`);
   - headless tool-less `claude -p --output-format json` extraction using
     `engram.md` rules;
   - pipe JSON → `cmd_replay` (reuse); single-flight file lock;
   - refresh `context-cache.md`; slow-cadence consolidation via existing commands.
3. **Read hook** — add `plugin/bin/engram-context` (UserPromptSubmit): emit capped
   `## Memory Context` = cached stable digest (`context-cache.md`, fallback
   `engram context`) + prompt-matched subgraph (`engram query`). Register in
   `hooks.json`. Optionally add `SessionStart` digest injection.
4. **Cache builder** — add `engram context --write-cache` (or reuse `cmd_context`) to
   materialize `vault/_meta/context-cache.md`; called by the worker.
5. **Trigger** — add a `SessionEnd` hook (or extend Stop) to detached-spawn the worker;
   register in `hooks.json`; ensure detachment (`setsid`, redirected output).
6. **Wiring/toggles** — `engram auto on` should also ensure the worker trigger is active;
   document `/engram-on` behavior change.
7. **Docs** — update `README.md` and `plugin/commands/*.md` to reflect the automatic
   flow; mark manual `/engram` / `/engram-full` as optional power-user commands.
8. **Tests** — see §7.

---

## 6. Acceptance criteria

- Finishing a substantive turn results in graph updates **with zero user commands** and
  **zero permission prompts**, within a short delay (worker runs out-of-band).
- Extraction never blocks or appears in the interactive session; the user can keep working.
- Every new prompt receives a non-empty, size-capped `## Memory Context` when the graph
  has relevant nodes; the hook adds < ~300 ms latency and never errors the session.
- Re-running on an unchanged queue is a no-op (watermark + dedup).
- No new third-party imports in any file under `plugin/bin/` or `plugin/hooks/`.
- No reference to MeshClaw anywhere.
- Existing vault data remains readable; Obsidian Graph View still renders.

---

## 7. Tests to add/extend

Extend the existing suites (`tests/test_features.py`, `tests/test_cli_e2e.py`,
`tests/test_smoke.py`):
- **Capture:** Stop hook appends one well-formed line per substantive turn; skips trivial
  turns; respects `hook-enabled`; never raises.
- **Worker watermark:** second run on unchanged `pending.jsonl` processes 0 turns; partial
  new turns processed incrementally; file lock prevents concurrent corruption.
- **Extraction (mocked):** stub `claude -p` to return canned JSON; assert it lands via
  `cmd_replay` with dedup honored.
- **Read hook:** given a seeded graph, `engram-context` emits a capped `## Memory Context`
  containing god nodes and prompt-matched entities; empty graph → empty/no block, no error.
- **Cache:** worker refreshes `context-cache.md`; read hook prefers cache over live build.

---

## 8. Reference files (read these before implementing)

All paths relative to repo root `/workplace/qianheng/engram`.

| File | Role | Key anchors |
|------|------|-------------|
| `plugin/hooks/hooks.json` | Hook registration (PreToolUse, Stop). Add UserPromptSubmit + SessionEnd here. | full file |
| `plugin/bin/engram-hook` | **EDIT** — Stop hook; currently writes a breadcrumb to `_meta/queue/`. Upgrade to append full turns to `pending.jsonl`. | `_get_vault`, `main` |
| `plugin/bin/engram-pretool` | Existing PreToolUse retrieval (weak token match). Reference for graph access from a hook; may keep as supplement or retire. | `extract_tokens`, `match_entities`, `main` |
| `engram_cli.py` | CLI. Reuse `cmd_context` (injection payload), `cmd_query` (matched retrieval), `cmd_replay`+`_check_dedup` (landing), `cmd_auto` (toggle), consolidation cmds. Add `worker` (+ optional `context --write-cache`). | `cmd_auto`:250, `cmd_replay`:355, `_check_dedup`:~268, `cmd_query`:794, `cmd_context`:1014, `cmd_consolidation`:1437, `main`:1469 |
| `engram/graph.py` | `MemoryGraph` API. Use for god nodes + neighbors in the read hook/cache. | `god_nodes`:463, `get_entity`:77, `get_neighbors`:82, `all_entity_names`:74, `suggested_questions`:525, `detect_communities`:339 |
| `plugin/commands/engram.md` | The **extraction rules + JSON schema** the worker must embed in its `claude -p` prompt. Source of truth for extraction criteria, naming, confidence tagging, evidence/hyperedge schema. | full file |
| `plugin/commands/engram-full.md` | The 6-stage consolidation flow the worker should run on slow cadence. | full file |
| `plugin/.claude-plugin/plugin.json` | Plugin manifest. | full file |
| `tests/test_features.py`, `tests/test_cli_e2e.py`, `tests/test_smoke.py` | Test patterns to extend. | full files |

### Config / paths reference
- Config: `~/.engram/config.json` → `{"vault": "<path>"}`; env override `ENGRAM_CONFIG`.
- Vault meta dir: `vault/_meta/` — existing: `graph.graphml`, `queue/`, `dedup/`,
  `hook-enabled`. **New:** `pending.jsonl`, `worker-state.json`, `context-cache.md`,
  a worker lock file.
- Obsidian content dirs: `vault/daily/`, `vault/patterns/`, entity markdown files.

---

## 9. Design rationale recap (one line)

Engram already *stores* memory well; it just *interacts* with it like a database
(pull + synchronous writes). MeshClaw feels good because it treats memory like ambient
context (push injection + async background writes). This redesign ports that interaction
model onto Engram's existing graph — nothing about the storage layer changes.
