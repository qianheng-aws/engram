<div align="center">

# 🧠 Engram

**Persistent memory for Claude Code — your coding sessions become a knowledge graph**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Claude Code Plugin](https://img.shields.io/badge/Claude_Code-Plugin-blueviolet.svg)](https://docs.anthropic.com/en/docs/claude-code)
[![Dependencies](https://img.shields.io/badge/dependencies-1_(networkx)-green.svg)](#installation)

*Like sleep consolidates human memory, `/engram` consolidates your coding sessions into reusable knowledge.*

</div>

---

## ✨ What It Does

You work in Claude Code as usual. Engram captures, extracts, and retrieves your knowledge automatically.

**Automatic flow:**
- **Capture** — Every session is appended to a queue as you work
- **Extraction** — A background worker extracts entities and relations (no API keys, CC does it)
- **Retrieval** — Every prompt gets relevant context from your knowledge graph injected automatically

No manual `/engram` needed. Your knowledge accumulates across sessions and gets injected into future sessions automatically.

Optional power-user commands:
- `/engram` — Deep extraction with the full interactive model (instead of the background worker)
- `/engram-full` — Full 6-stage consolidation (feedback, integrate, prune, community, abstract, lint)
- `/engram-query <question>` — Manual search when you want to see the results directly

Behind the scenes:

```
CC Session → Stop hook → pending.jsonl → engram worker → Entity Extraction → Knowledge Graph
                                          (headless CC)    (NetworkX)        (Markdown)
                                                                ↓
                                                        context-cache.md
                                                                ↓
          UserPromptSubmit hook → Memory Context (digest + query) → injected into next prompt
```

## 🏗️ Architecture

Built on the **OODA loop** — the same decision framework used by fighter pilots:

| Phase | What | How |
|:------|:-----|:----|
| **🔍 Observe** | Capture session conversations | CC session JSONL parser |
| **🧭 Orient** | Extract entities & relations | CC does entity extraction — no external API |
| **🎯 Decide** | Consolidate memory | 7-stage: replay → feedback → integrate → prune → community → abstract → lint |
| **⚡ Act** | Persist to vault | Obsidian markdown + NetworkX GraphML |

### Zero External Dependencies

- **No API keys** — CC itself is the LLM
- **No vector database** — graph-only retrieval with CC entity routing
- **No Docker** — just Python + networkx
- **No cloud services** — everything runs locally

## 📦 Installation

### Prerequisites

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) installed
- Python 3.10+

### From PyPI

```bash
pip install engram-echo
claude plugin marketplace add https://github.com/qianheng-aws/engram.git
claude plugin install engram@engram-echo
```

### From source

```bash
git clone https://github.com/qianheng-aws/engram.git
cd engram
pip install -e .
claude plugin marketplace add ./
claude plugin install engram@engram-echo
```

Vault, config, and CLAUDE.md prompt all auto-initialize on first use. Then enable the automatic flow (one-time):

```bash
engram auto on    # enable auto-capture — the hooks stay inactive until this runs
```

Optional:
- `engram init ~/custom-vault` — use a custom vault path (default: `~/.engram/vault`)
- `engram auto off` — disable auto-capture again at any time

## 🔄 Updating

Engram has two parts — the Python CLI and the Claude Code plugin — update both.

### From PyPI

```bash
pip install --upgrade engram-echo
claude plugin update engram@engram-echo
```

### From source

```bash
cd /path/to/engram
git pull
pip install -e .                              # usually a no-op under -e, safe to run
claude plugin marketplace update engram-echo  # refresh local marketplace
claude plugin update engram@engram-echo
```

### After updating

- `engram install` — re-injects the latest prompt block into `~/.claude/CLAUDE.md` if it changed
- `engram lint` — validates vault consistency (catches schema drift)
- Check `~/.engram/config.json` against the [config example](#engram-vs-engram-full) — new fields are not auto-added to existing configs

## 🎮 Commands

Once auto-capture is enabled (`engram auto on`), Engram works automatically — no manual commands needed for routine sessions.

**Optional power-user commands:**

| Command | Description |
|:--------|:------------|
| `/engram` | Deep extraction with full interactive model (instead of background worker) |
| `/engram-full` | Full consolidation: replay → feedback → integrate → prune → community → abstract → lint |
| `/engram-feedback` | Process human corrections from Obsidian callouts |
| `/engram-community` | Detect and summarize knowledge clusters (Louvain) |
| `/engram-status` | Show vault statistics, graph analysis |
| `/engram-query <question>` | Search knowledge graph (keyword + graph traversal) |
| `/engram-on` | Enable auto-capture (required once — hooks are inactive until enabled) |
| `/engram-off` | Disable auto-capture |

### Automatic flow vs manual commands

| | Automatic (background worker) | Manual `/engram` | `/engram-full` |
|:--|:------------------------------|:-----------------|:---------------|
| Trigger | Every session (Stop/SessionEnd hook) | On-demand | On-demand |
| Model | Headless tool-less CC (`claude -p`) | Full interactive model | Full interactive model |
| Stages | Replay only | Replay only | All 6 stages + lint |
| Speed | Fast, runs in background | Fast (one extraction) | Slower (multi-step) |
| When to use | Runs automatically | Deep extraction, or override auto | Consolidation maintenance |

**Auto-consolidation:** The background worker tracks how many replays have occurred since the last full run. When thresholds are reached, a marker file `_meta/consolidation-due` is created to remind you to run `/engram-full`. Thresholds are configurable in `~/.engram/config.json`:

```json
{
  "worker_consolidation_every": 10
}
```

## 🌙 Consolidation Stages

```
┌──────────┐    ┌──────────┐    ┌───────────┐    ┌─────────┐    ┌───────────┐    ┌──────────┐    ┌────────┐
│  Replay  │ →  │ Feedback │ →  │ Integrate │ →  │  Prune  │ →  │ Community │ →  │ Abstract │ →  │  Lint  │
│          │    │          │    │           │    │         │    │           │    │          │    │        │
│ Extract  │    │  Human   │    │  Merge    │    │ Decay   │    │  Cluster  │    │ Discover │    │ Verify │
│ entities │    │  review  │    │  dupes    │    │ old     │    │  & label  │    │ patterns │    │ vault  │
└──────────┘    └──────────┘    └───────────┘    └─────────┘    └───────────┘    └──────────┘    └────────┘
```

- **Replay** — CC extracts entities/relations from session → writes to graph + daily note
- **Feedback** — Scans entity files for `[!correction]`/`[!merge]`/`[!delete]` callouts left by the user in Obsidian → CC applies fixes
- **Integrate** — Detects duplicate entities (token similarity) → CC decides merge
- **Prune** — Scores entities by decay (30-day half-life) → archives stale ones
- **Community** — Louvain clustering on the graph → CC titles and summarizes each cluster
- **Abstract** — Analyzes daily notes → discovers behavioral patterns (e.g., "user always debugs by observe → hypothesize → verify")
- **Lint** — Validates vault consistency: GraphML ↔ markdown sync, dead wikilinks, orphan nodes, frontmatter completeness

### Human Feedback (Obsidian)

The Feedback stage processes corrections you leave directly in entity files. In Obsidian:

1. Open any entity file (e.g., `entities/concepts/STDERR_PIPE_BLOCKING.md`)
2. Add a callout — either `Cmd+P` → search "callout", or type the syntax directly:

```markdown
> [!correction] More accurate description
> Actually the buffer limit is 64KB, not 32KB

> [!merge] Should be same as ASYNC_DRAIN_STDERR

> [!delete] False extraction, not a real entity
```

3. Run `/engram-feedback` (or `/engram-full`) — CC reads the callouts, applies fixes, and removes them

## 🗂️ Vault Structure

Open the vault in [Obsidian](https://obsidian.md) to get an interactive knowledge graph:

1. Install Obsidian from [obsidian.md](https://obsidian.md)
2. Open Obsidian → **Open folder as vault** → In the file picker, press `Cmd+Shift+G` and type `~/.engram/vault`
   - Alternatively, use a visible path: `engram init ~/engram/vault`
3. Toggle **Graph View** (Ctrl/Cmd + G) to see your knowledge graph

<div align="center">
<img src="docs/obsidian-graph.png" alt="Engram knowledge graph in Obsidian" width="700">

*Entity nodes colored by type, with tags, wikilinks, and Graph View visualization*
</div>

```
~/.engram/vault/
├── 📁 entities/              # Knowledge graph nodes
│   ├── people/               #   PERSON entities
│   ├── concepts/             #   Bugs, patterns, designs
│   ├── projects/             #   Repos, packages
│   ├── tools/                #   Libraries, frameworks
│   └── orgs/                 #   Teams, companies
├── 📁 relations/             # Edge table with weights
├── 📁 communities/           # Louvain cluster summaries
├── 📁 groups/                # Hyperedge MOC (Map of Content) files
├── 📁 daily/                 # Session summaries by date
├── 📁 patterns/              # Discovered behavioral patterns
└── 📁 _meta/                 # System data
    ├── pending.jsonl         #   Queue of captured turns (watermark-based)
    ├── worker-state.json     #   Worker offset + consolidation counter
    ├── context-cache.md      #   Stable digest for UserPromptSubmit injection
    ├── entity-index.json     #   Precomputed entity keywords + snippets for fast prompt matching
    ├── worker.log            #   Worker run log (timestamped entries)
    ├── worker.lock           #   Single-flight lock (fcntl.LOCK_EX)
    ├── consolidation-due     #   Marker file when consolidation threshold reached
    ├── hook-enabled          #   Kill switch for hooks (auto on/off)
    └── graph.graphml         #   NetworkX GraphML
```

### Entity Example

```markdown
---
entity_type: CONCEPT
tags:
  - entity/concept
aliases:
  - "Stderr Pipe Blocking"
created: 2026-04-03
last_updated: 2026-04-03
degree: 1
cssclasses:
  - entity
  - concept
---

# STDERR_PIPE_BLOCKING

Bug in claude-slack-bridge where claude process writes verbose logs
to stderr but daemon never reads it, causing 64KB buffer to fill
and block the entire process. Fixed by adding _drain_stderr task.

## Relations

- [[CLAUDE_SLACK_BRIDGE]] `PROJECT` — Bridge had this bug causing sessions to hang (weight: 0.8)
```

Every entity links to related entities via `[[wikilinks]]` — Obsidian renders these as an interactive graph. Tags, aliases, and cssclasses enable Dataview queries and Graph View styling.

## 🔧 Design Decisions

| Decision | Why |
|:---------|:----|
| **CC as LLM** | No API keys needed. CC extracts entities directly. |
| **Graph-only retrieval** | No embeddings. Hook reads precomputed entity index for prompt matching; falls back to live query when index absent. Scales to ~2000 entities. |
| **nano-graphrag reference** | Reused prompt templates and storage format, not runtime. |
| **Obsidian-native** | All output is valid Obsidian markdown. Open vault → instant graph view. |
| **Description cap** | Keep first + latest description only. Prevents infinite growth. |
| **File lock** | `fcntl.LOCK_EX` + read-merge-write prevents concurrent corruption. |

## 🛠️ CLI Reference

```bash
# Setup
engram init [PATH]                              # Initialize vault + register in ~/.claude/CLAUDE.md
engram auto [on|off|status]                     # Toggle auto-capture (run `on` once to enable hooks)
engram install                                  # Re-register in ~/.claude/CLAUDE.md (auto on init)
engram uninstall                                # Remove from ~/.claude/CLAUDE.md

# Background worker
engram worker                                   # Drain pending.jsonl, extract, replay
                                                # (auto-spawned by hooks, rarely invoked manually)

# Query and context
engram status                                   # Vault statistics + hub entities + density
engram query --question "..."                   # Search graph
engram context                                  # Compact summary for system prompt injection
engram context --write-cache                    # Rebuild _meta/context-cache.md

# Manual extraction and consolidation
engram replay --stdin                           # Process extracted entity/relation JSON
engram integrate                                # Detect duplicate entities
echo '<json>' | engram integrate --stdin        # Execute merges
engram prune                                    # Report decay scores
echo '<json>' | engram prune --stdin            # Archive entities
engram community                                # Detect knowledge clusters
echo '<json>' | engram community --stdin        # Save community summaries
engram abstract                                 # Gather data for pattern discovery
echo '<json>' | engram save-pattern --stdin     # Save discovered patterns
engram feedback                                 # Scan entity files for correction callouts
echo '<json>' | engram feedback --stdin         # Apply corrections/merges/deletes
engram lint                                     # Validate vault consistency
engram consolidation                            # Show consolidation tracking state
engram consolidation --reset                    # Reset counter (after full consolidation)

# All commands use the vault from last `engram init`. Override with --vault PATH.
```

### Config keys (`~/.engram/config.json`)

```json
{
  "vault": "~/.engram/vault",
  "worker_claude_bin": "claude",
  "worker_model": null,
  "worker_consolidation_every": 10,
  "worker_max_turns_per_run": 20,
  "context_max_chars": 2000
}
```

- `worker_claude_bin` — Path to `claude` binary for headless extraction (default: `"claude"`)
- `worker_model` — Model override for worker (default: `null`, inherits from CC session)
- `worker_consolidation_every` — Mark consolidation-due after N replays (default: `10`)
- `worker_max_turns_per_run` — Max queued turns extracted per worker run; leftovers drain on the next spawn (default: `20`)
- `context_max_chars` — Max chars in UserPromptSubmit injection (default: `2000`)

## 📄 License

[MIT](LICENSE) — do whatever you want with it.

---

<div align="center">

*Built in one afternoon with Claude Code. The tool that remembers itself.* 🐾

</div>
