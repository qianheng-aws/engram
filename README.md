<div align="center">

# 🧠 OODA Dream

**Persistent memory for Claude Code — your coding sessions become a knowledge graph**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Claude Code Plugin](https://img.shields.io/badge/Claude_Code-Plugin-blueviolet.svg)](https://docs.anthropic.com/en/docs/claude-code)
[![Dependencies](https://img.shields.io/badge/dependencies-1_(networkx)-green.svg)](#installation)

*Like sleep consolidates human memory, `/dream` consolidates your coding sessions into reusable knowledge.*

</div>

---

## ✨ What It Does

You work in Claude Code as usual. When you're done, run `/dream`. That's it.

```
You: /dream

CC: Analyzing session... Found 5 entities, 3 relations.
    ✅ Saved to vault: STDERR_PIPE_BLOCKING, CLAUDE_SLACK_BRIDGE, ...
    📝 Daily note: 2026-04-06.md
```

Behind the scenes:

```
CC Session → /dream → Entity Extraction → Knowledge Graph → Obsidian Vault
                        (CC as LLM)        (NetworkX)       (Markdown + [[wikilinks]])
```

Your knowledge accumulates across sessions. Query it anytime:

```
You: /dream-query how did I fix the stderr bug?

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
| **🎯 Decide** | Consolidate memory | Dream 4-stage: replay → integrate → prune → abstract |
| **⚡ Act** | Persist to vault | Obsidian markdown + NetworkX GraphML |

### Zero External Dependencies

- **No API keys** — CC itself is the LLM
- **No vector database** — graph-only retrieval with CC entity routing
- **No Docker** — just Python + networkx
- **No cloud services** — everything runs locally

## 📦 Installation

### Prerequisites

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) installed
- Python 3.10+ with `networkx`: `pip install networkx`

### Setup

```bash
# 1. Clone
git clone https://github.com/qianheng-aws/ooda-dream.git
cd ooda-dream

# 2. Initialize vault
mkdir -p ~/.meshclaw/vault/{entities/{people,concepts,projects,tools,orgs},relations,communities,daily,dreams,patterns,preferences,_meta}

# 3. Register as CC plugin
# Add to ~/.claude/settings.json:
```

```jsonc
{
  "extraKnownMarketplaces": {
    "ooda-dream": {
      "source": { "source": "directory", "path": "/path/to/ooda-dream" }
    }
  },
  "enabledPlugins": {
    "ooda-dream@ooda-dream": true
  }
}
```

```bash
# 4. (Optional) Enable auto-capture on session end
touch ~/.meshclaw/vault/_meta/hook-enabled
```

## 🎮 Commands

| Command | Description |
|:--------|:------------|
| `/dream` | Extract entities and relations from current session |
| `/dream-full` | Full consolidation: replay → integrate → prune → abstract |
| `/dream-status` | Show vault statistics and pending sessions |
| `/dream-query <question>` | Search knowledge graph (keyword + graph traversal) |
| `/dream-on` | Enable auto-capture on session end |
| `/dream-off` | Disable auto-capture |

### `/dream` vs `/dream-full`

| | `/dream` | `/dream-full` |
|:--|:---------|:--------------|
| Stages | Replay only | All 4 stages |
| Speed | Fast (one extraction) | Slower (multi-step) |
| When | Every session | Daily/weekly cleanup |

## 🌙 Dream Stages

```
┌──────────┐    ┌───────────┐    ┌─────────┐    ┌──────────┐
│  Replay  │ →  │ Integrate │ →  │  Prune  │ →  │ Abstract │
│          │    │           │    │         │    │          │
│ Extract  │    │  Merge    │    │ Decay   │    │ Discover │
│ entities │    │  dupes    │    │ old     │    │ patterns │
└──────────┘    └───────────┘    └─────────┘    └──────────┘
```

- **Replay** — CC extracts entities/relations from session → writes to graph + daily note
- **Integrate** — Detects duplicate entities (token similarity) → CC decides merge
- **Prune** — Scores entities by decay (30-day half-life) → archives stale ones
- **Abstract** — Analyzes daily notes → discovers behavioral patterns (e.g., "user always debugs by observe → hypothesize → verify")

## 🗂️ Vault Structure

Open `~/.meshclaw/vault/` in [Obsidian](https://obsidian.md) for graph visualization.

```
~/.meshclaw/vault/
├── 📁 entities/              # Knowledge graph nodes
│   ├── people/               #   PERSON entities
│   ├── concepts/             #   Bugs, patterns, designs
│   ├── projects/             #   Repos, packages
│   ├── tools/                #   Libraries, frameworks
│   └── orgs/                 #   Teams, companies
├── 📁 relations/             # Edge table with weights
├── 📁 daily/                 # Session summaries by date
├── 📁 patterns/              # Discovered behavioral patterns
└── 📁 _meta/                 # System data (GraphML, queue, lock)
```

### Entity Example

```markdown
---
entity_type: CONCEPT
source_id: session-2026-04-03
---

# STDERR_PIPE_BLOCKING

Bug in claude-slack-bridge where claude process writes verbose logs
to stderr but daemon never reads it, causing 64KB buffer to fill
and block the entire process. Fixed by adding _drain_stderr task.

## Relations

- [[CLAUDE_SLACK_BRIDGE]] — Bridge had this bug causing sessions to hang (weight: 0.8)
```

Every entity links to related entities via `[[wikilinks]]` — Obsidian renders these as an interactive graph.

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
dream_cli.py replay --vault PATH --stdin       # Process extracted JSON
dream_cli.py integrate --vault PATH [--stdin]   # Detect or execute merges
dream_cli.py prune --vault PATH [--stdin]       # Score or execute archival
dream_cli.py abstract --vault PATH              # Gather data for pattern discovery
dream_cli.py save-pattern --vault PATH --stdin   # Save discovered patterns
dream_cli.py status --vault PATH                # Vault statistics
dream_cli.py query --vault PATH --question "..." # Search graph
```

## 📄 License

[MIT](LICENSE) — do whatever you want with it.

---

<div align="center">

*Built in one afternoon with Claude Code. The tool that remembers itself.* 🐾

</div>
