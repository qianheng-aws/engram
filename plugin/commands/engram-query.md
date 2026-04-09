# Engram Query — Search knowledge graph

$ARGUMENTS

```bash
engram query --question "$ARGUMENTS"
```

The query engine uses keyword match + multi-hop graph traversal. Use the returned `context` to answer the question.

If results are sparse:
- Check `neighbors` — the graph may have traversed to relevant entities not matching your keywords
- Review `all_entities` for related names you didn't think to search
- Try rephrasing with entity names from the graph (UPPERCASE format)
