# 🧠 OODA Dream — Persistent Memory for Claude Code

A Claude Code plugin that extracts knowledge from your coding sessions and builds a persistent knowledge graph stored as an Obsidian vault.

**Like sleep consolidates human memory, `/dream` consolidates your coding sessions into reusable knowledge.**

## How It Works

```
CC Session → /dream → Entity Extraction → Knowledge Graph → Obsidian Vault
                         (CC as LLM)       (NetworkX)        (Markdown + [[wikilinks]])
```

1. You work in Claude Code as usual
2. Run `/dream` to extract entities, relations, and insights from the session
3. Knowledge persists as Obsidian-compatible markdown with `[[wikilinks]]`
4. Query past knowledge with `/dream-query`

## Architecture (OODA Loop)

| Phase | What | How |
|-------|------|-----|
| **Observe** | Capture session conversations | CC session JSONL parser |
| **Orient** | Extract entities & relations | CC does entity extraction (no external API) |
| **Decide** | Consolidate memory | Dream 4-stage: replay → integrate → prune → abstract |
| **Act** | Persist to vault | Obsidian markdown + NetworkX GraphML |

## Installation

### Prerequisites

- Claude Code
- Python 3.10+ with `networkx` installed

### Setup

```bash
# 1. Clone
git clone <repo-url> /path/to/ooda-memory
cd /path/to/ooda-memory

# 2. Install dependencies
pip install networkx

# 3. Initialize vault
mkdir -p ~/.meshclaw/vault/{entities/{people,concepts,projects,tools,orgs},relations,communities,daily,dreams,patterns,preferences,_meta}

# 4. Install CC plugin (option A: local marketplace)
# Add to your marketplace's plugins array in .claude-plugin/marketplace.json:
# {"name": "ooda-dream", "source": "./plugins/ooda-dream", "category": "productivity"}
# Then enable in CC: /plugins → ooda-dream@your-marketplace → enable

# 5. Enable auto-capture (optional)
touch ~/.meshclaw/vault/_meta/hook-enabled
```

### Plugin Structure

```
plugins/ooda-dream/
├── .claude-plugin/plugin.json
├── hooks/hooks.json          # Stop hook → auto-queue sessions
├── bin/ooda-dream-hook       # Hook script (stdlib only)
└── commands/
    ├── dream.md              # /dream — extract from session
    ├── dream-full.md         # /dream-full — all 4 stages
    ├── dream-status.md       # /dream-status — vault stats
    ├── dream-query.md        # /dream-query <question>
    ├── dream-on.md           # /dream-on — enable auto-capture
    └── dream-off.md          # /dream-off — disable auto-capture
```

## Commands

| Command | Description |
|---------|-------------|
| `/dream` | Extract entities and relations from current session |
| `/dream-full` | Full consolidation: replay + integrate + prune + abstract |
| `/dream-status` | Show vault statistics and pending sessions |
| `/dream-query <question>` | Search knowledge graph |
| `/dream-on` | Enable session-end auto-capture |
| `/dream-off` | Disable session-end auto-capture |

## Dream Stages

### 1. Replay
Extract entities and relations from session conversations. CC serves as the LLM — no external API needed.

### 2. Integrate
Detect and merge duplicate entities (e.g., `NANO_GRAPHRAG` ↔ `NANOGRAPHRAG`).

### 3. Prune
Decay scoring with 30-day half-life. Archive entities that haven't been referenced recently. Hub nodes and pinned entities are protected.

### 4. Abstract
Discover behavioral patterns from daily notes (e.g., "user always debugs by observing → hypothesizing → verifying").

## Vault Structure

```
~/.meshclaw/vault/
├── entities/                  # Knowledge graph nodes
│   ├── people/                #   PERSON entities
│   ├── concepts/              #   CONCEPT entities
│   ├── projects/              #   PROJECT entities
│   ├── tools/                 #   TOOL entities
│   └── orgs/                  #   ORGANIZATION entities
├── relations/_index.md        # Edge table with weights
├── daily/                     # Daily session summaries
├── patterns/                  # Discovered behavioral patterns
├── dreams/                    # Dream consolidation reports
└── _meta/                     # System data
    ├── graph.graphml          #   NetworkX graph persistence
    ├── hook-enabled           #   Auto-capture flag
    └── queue/                 #   Pending session breadcrumbs
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

## Design Decisions

- **CC as LLM**: No Bedrock/OpenAI API needed. CC itself extracts entities.
- **Graph-only retrieval**: No vector embeddings. CC does entity routing from the full entity list. Efficient for <2000 entities.
- **nano-graphrag reference**: Reuses prompt templates and storage format, not runtime.
- **Obsidian-native**: All output is valid Obsidian markdown with `[[wikilinks]]`. Open the vault in Obsidian for graph visualization.
- **Zero external dependencies**: Only Python stdlib + networkx. Hook script uses stdlib only.

## CLI Reference

```bash
# Core operations
dream_cli.py replay --vault PATH --stdin     # Process extracted JSON
dream_cli.py integrate --vault PATH          # Find duplicate entities
dream_cli.py prune --vault PATH              # Decay scoring
dream_cli.py abstract --vault PATH           # Gather data for pattern discovery
dream_cli.py save-pattern --vault PATH --stdin  # Save discovered patterns

# Query
dream_cli.py status --vault PATH             # Vault statistics
dream_cli.py query --vault PATH --question "..." # Search graph
```

## License

MIT
