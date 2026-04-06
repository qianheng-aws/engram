# Engram Query — Search knowledge graph

$ARGUMENTS

```bash
python3 /workplace/qianheng/ooda-memory/engram_cli.py query --vault ~/.engram/vault --question "$ARGUMENTS"
```

Use the returned context to answer the question. If keyword match is insufficient, review the `all_entities` list and pick relevant ones for deeper lookup.
