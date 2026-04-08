# Engram Community — Detect and summarize knowledge communities

Detect clusters of related entities in the knowledge graph using Louvain community detection, then generate summaries.

## Steps

**1. Detect communities:**

```bash
engram community --vault ~/.engram/vault
```

**2. Review the detected communities.** For each community with 2+ members:
- Generate a concise **title** (e.g., "Slack Bridge Infrastructure", "Memory System Design")
- Write a **summary** paragraph explaining what connects these entities

**3. Pipe summaries back:**

```bash
echo '<json>' | engram community --vault ~/.engram/vault --stdin
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
