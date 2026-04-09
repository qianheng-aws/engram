# Engram — Extract memories from current session

Extract entities and relations from the current session conversation and persist to the Obsidian vault knowledge graph.

## Steps

**1. Check existing entities:**

```bash
engram status```

**2. Analyze this conversation** and extract entities + relations following these rules:

### Extraction criteria
Extract projects, tools, and concepts **worked on, debugged, designed, or decided about** — but only if they meet at least one:
- **Design decision** — chose it, rejected it, or compared it with alternatives
- **Bug/fix** — discovered or fixed a bug in it
- **Built/modified** — implemented or changed its code
- **Core architecture** — the project's design would be fundamentally different without it (not just "used it")

Examples:
- Session uses Python to write code → do NOT extract PYTHON
- Compared Python vs Go as implementation language → extract both
- Obsidian wikilinks/Dataview shaped engram's export format → extract OBSIDIAN
- Used NetworkX for graph storage → extract only if discussed why NetworkX over alternatives

### What NOT to extract (❌)
- Transient actions (FILE_UPLOAD, VSCODE_RESTART)
- The user themselves

### Naming
- UPPERCASE with underscores: `CLAUDE_SLACK_BRIDGE`
- Match existing entity names when referring to same thing

### Description quality
Use markdown in descriptions. Include code blocks, links, bullet lists where relevant.

- Bad ❌: `"A tool that was discussed"`
- Bad ❌: `"Bug in the bridge"` (no detail, no fix)
- Good ✅:
```
Bug where stderr fills 64KB pipe buffer, blocking stdout.\n\n**Root cause:** `subprocess.PIPE` for stderr without consumer.\n**Fix:** Added `_drain_stderr` async task.\n\n```python\nasync def _drain_stderr(proc):\n    await proc.stderr.read()\n```
```

### Weight scale
- 0.1-0.3: weak mention | 0.4-0.6: moderate | 0.7-0.9: core dependency | 1.0: identity

### Confidence tagging
Every entity and relation MUST include a `confidence` field:
- `EXTRACTED` — explicitly discussed, debugged, or decided in this session
- `INFERRED` — mentioned in passing, referenced indirectly, or deduced from context
- `AMBIGUOUS` — unclear whether relevant, mentioned tangentially

**3. Pipe extracted JSON:**

```bash
echo '<json>' | engram replay --stdin
```

JSON format:
```json
{
  "date": "YYYY-MM-DD",
  "entities": [{
    "name": "NAME",
    "entity_type": "PROJECT|TOOL|CONCEPT|PERSON|ORGANIZATION",
    "description": "Markdown description with **bold**, `code`, bullet lists, code blocks",
    "confidence": "EXTRACTED|INFERRED|AMBIGUOUS",
    "references": ["https://github.com/..."],
    "local_path": "/absolute/path/to/project"
  }],
  "relations": [{
    "source": "A", "target": "B",
    "description": "...", "weight": 0.8,
    "confidence": "EXTRACTED|INFERRED|AMBIGUOUS",
    "confidence_score": 0.75
  }],
  "hyperedges": [{
    "id": "snake_case_id",
    "label": "Human Readable Group Name",
    "members": ["ENTITY_A", "ENTITY_B", "ENTITY_C"],
    "relation": "form"
  }],
  "daily_summary": "One paragraph summary"
}
```

Notes:
- `description`: Use markdown. Include code blocks for key snippets, bullet lists for key points.
- `references`: Optional. URLs from the session (GitHub repos, docs, issues, PRs) relevant to this entity.
- `local_path`: Optional. Absolute path to local directory/file for this entity (repos, projects, data files). Skip for concepts/bugs without a local presence.
- `confidence_score`: Optional. Numeric 0.0-1.0 on relations — how confident the relationship is. Use alongside string `confidence`.
- `hyperedges`: Optional. Group 3+ entities that form a logical unit (e.g., "OODA Pipeline" grouping 5 stages, "Auth Stack" grouping 3 tools). Skip if no natural groupings exist.
