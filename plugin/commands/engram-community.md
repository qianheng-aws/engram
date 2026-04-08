# Engram Community — Detect and summarize knowledge communities

Detect clusters of related entities in the knowledge graph using Louvain community detection, then generate summaries.

## Steps

**1. Detect communities:**

```bash
engram community```

**2. Review the detected communities.** For each community with 2+ members:
- Generate a concise **title** (e.g., "Slack Bridge Infrastructure", "Memory System Design")
- Write a **summary** paragraph explaining what connects these entities

**3. Review `surprising_connections`.** If cross-community edges exist, generate an additional **cross-community comparison** entry:
- Title should reflect the shared theme (e.g., "AI Memory Systems Comparison")
- Summary should include a **structured comparison** (markdown table or bullet matrix) covering key dimensions across the connected entities
- Members list includes all entities involved in the surprising connections

**4. Pipe summaries back:**

```bash
echo '<json>' | engram community --stdin
```

JSON format:
```json
{
  "communities": [
    {
      "id": 0,
      "title": "Descriptive Community Title",
      "summary": "One paragraph explaining the theme and connections.",
      "members": ["ENTITY_A", "ENTITY_B", "ENTITY_C"]
    }
  ]
}
```
