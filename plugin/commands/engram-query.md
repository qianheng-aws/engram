# Engram Query — Search knowledge graph

$ARGUMENTS

Pass only English keywords (no stop words). Translate if needed.

```bash
engram query --question "<english keywords>"
```

- `context` — matched entities with descriptions and neighbor relations
- `community_context` — cluster summaries for matched entities
- `expanded_entities` — indirectly related entities (2-3 hops out) with hop distance and weight. If context is insufficient, query specific expanded entities for more detail
- `all_entities` — full entity list for discovering related names
