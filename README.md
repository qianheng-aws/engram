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

You work in Claude Code as usual. When you're done, run `/engram`. That's it.

```
You: /engram

CC: Analyzing session... Found 5 entities, 3 relations.
    ✅ Saved to vault: STDERR_PIPE_BLOCKING, CLAUDE_SLACK_BRIDGE, ...
    📝 Daily note: 2026-04-06.md
```

Behind the scenes:

```
CC Session → /engram → Entity Extraction → Knowledge Graph → Obsidian Vault
                        (CC as LLM)        (NetworkX)       (Markdown + [[wikilinks]])
```

Your knowledge accumulates across sessions. Query it anytime:

```
You: /engram-query how did I fix the stderr bug?

CC: Found STDERR_PIPE_BLOCKING → Bug where claude process stderr fills 64KB
    pipe buffer, blocking stdout. Fixed by adding _drain_stderr async task.
    Related: [[CLAUDE_SLACK_BRIDGE]]
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

### Setup

```bash
pip install engram-echo
claude plugin marketplace add qianheng-aws/engram
claude plugin install engram@engram-echo
```

That's it. Vault auto-creates at `~/.engram/vault` on first use.

Optional:
- `engram init ~/custom-vault` — use a custom vault path
- `engram auto on` — enable auto-capture on session end

<details>
<summary>Development install (from source)</summary>

```bash
git clone https://github.com/qianheng-aws/engram.git
cd engram
pip install -e .
claude plugin marketplace add ./
claude plugin install engram@engram-echo
```
</details>

## 🎮 Commands

| Command | Description |
|:--------|:------------|
| `/engram` | Extract entities and relations from current session |
| `/engram-full` | Full consolidation: replay → feedback → integrate → prune → community → abstract → lint |
| `/engram-feedback` | Process human corrections from Obsidian callouts |
| `/engram-community` | Detect and summarize knowledge clusters (Louvain) |
| `/engram-status` | Show vault statistics, graph analysis, and pending sessions |
| `/engram-query <question>` | Search knowledge graph (keyword + graph traversal) |
| `/engram-on` | Enable auto-capture on session end |
| `/engram-off` | Disable auto-capture |

### `/engram` vs `/engram-full`

| | `/engram` | `/engram-full` |
|:--|:---------|:--------------|
| Stages | Replay only | All 6 stages + lint |
| Speed | Fast (one extraction) | Slower (multi-step) |
| When | Every session | Auto-triggered when needed |

**Auto-consolidation:** After each `/engram`, the CLI tracks how many replays have occurred since the last full run. When thresholds are reached, `/engram` will remind you (tier 1) or automatically escalate to a full consolidation (tier 2). Thresholds are configurable in `~/.engram/config.json`:

```json
{
  "consolidation": {
    "remind_after_replays": 10,
    "remind_after_days": 7,
    "force_after_replays": 15,
    "force_after_days": 14
  }
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
└── 📁 _meta/                 # System data (GraphML, queue, lock)
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
| **Graph-only retrieval** | No embeddings. CC picks relevant entities from the full list. Scales to ~2000 entities. |
| **nano-graphrag reference** | Reused prompt templates and storage format, not runtime. |
| **Obsidian-native** | All output is valid Obsidian markdown. Open vault → instant graph view. |
| **Description cap** | Keep first + latest description only. Prevents infinite growth. |
| **File lock** | `fcntl.LOCK_EX` + read-merge-write prevents concurrent corruption. |

## 🛠️ CLI Reference

```bash
engram init [PATH]                              # Initialize vault + register in ~/.claude/CLAUDE.md
engram auto [on|off|status]                     # Toggle auto-capture on session end
engram status                                   # Vault statistics + hub entities + density
engram query --question "..."                   # Search graph
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
engram context                                  # Compact summary for system prompt injection
engram lint                                     # Validate vault consistency
engram consolidation                            # Show consolidation tracking state
engram consolidation --reset                    # Reset counter (after full consolidation)
engram install                                  # Re-register in ~/.claude/CLAUDE.md (auto on init)
engram uninstall                                # Remove from ~/.claude/CLAUDE.md
# All commands use the vault from last `engram init`. Override with --vault PATH.
```

## 📄 License

[MIT](LICENSE) — do whatever you want with it.

---

<div align="center">

*Built in one afternoon with Claude Code. The tool that remembers itself.* 🐾

</div>
