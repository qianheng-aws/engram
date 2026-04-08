# Engram — Extract memories from current session

Extract entities and relations from the current session conversation and persist to the Obsidian vault knowledge graph.

## Steps

**1. Check existing entities:**

```bash
engram-cli status --vault ~/.engram/vault
```

**2. Analyze this conversation** and extract entities + relations following these rules:

### What TO extract (✅)
- Projects, tools, concepts **worked on, debugged, designed, or decided about**
- Bugs with root cause and fix
- Design decisions with rationale

### What NOT to extract (❌)
- Generic tech (PYTHON, LINUX, GIT, JSON) unless central to discussion
- Transient actions (FILE_UPLOAD, VSCODE_RESTART)
- The user themselves

### Naming
- UPPERCASE with underscores: `CLAUDE_SLACK_BRIDGE`
- Match existing entity names when referring to same thing

### Description quality
- Bad ❌: "A tool that was discussed"
- Good ✅: "Bug where stderr fills 64KB pipe buffer, blocking stdout. Fixed by adding _drain_stderr task."

### Weight scale
- 0.1-0.3: weak mention | 0.4-0.6: moderate | 0.7-0.9: core dependency | 1.0: identity

**3. Pipe extracted JSON:**

```bash
echo '<json>' | engram-cli replay --vault ~/.engram/vault --stdin
```

JSON format:
```json
{
  "date": "YYYY-MM-DD",
  "entities": [{"name": "NAME", "entity_type": "PROJECT|TOOL|CONCEPT|PERSON|ORGANIZATION", "description": "..."}],
  "relations": [{"source": "A", "target": "B", "description": "...", "weight": 0.8}],
  "daily_summary": "One paragraph summary"
}
```
