# Engram Query — Search knowledge graph

$ARGUMENTS

The knowledge graph stores all content in English. Extract only the key terms from the user's question — drop stop words, question words, and filler. Translate non-English terms to English. Reply to the user in their original language.

Example: "what is the starship prompt renderer?" → `engram query --question "starship prompt renderer"`

```bash
engram query --question "<english keywords>"
```

The query engine uses keyword match + multi-hop graph traversal. Use the returned `context` to answer the question.

If results are sparse:
- Check `neighbors` — the graph may have traversed to relevant entities not matching your keywords
- Review `all_entities` for related names you didn't think to search
- Try rephrasing with entity names from the graph (UPPERCASE format)
