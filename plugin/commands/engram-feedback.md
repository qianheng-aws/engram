# Engram Feedback — Process human corrections from Obsidian callouts

Scan entity files for `[!correction]`, `[!merge]`, `[!delete]` callouts left by the user in Obsidian, then apply fixes.

## Steps

**1. Scan for feedback callouts:**

```bash
engram feedback```

**2. Review each callout** and decide what action to take:

- `[!correction]` — fix the entity's description, type, or relation as described
- `[!merge]` — merge this entity into the target mentioned in the callout
- `[!delete]` — archive/remove this entity from the graph

**3. Pipe corrections back:**

```bash
echo '<json>' | engram feedback --stdin
```

JSON format:
```json
{
  "corrections": [
    {"entity": "ENTITY_NAME", "description": "Fixed description text"},
    {"entity": "ENTITY_NAME", "entity_type": "PROJECT"}
  ],
  "merges": [
    {"canonical": "KEEP_THIS", "aliases": ["REMOVE_THIS"]}
  ],
  "deletes": ["ENTITY_TO_DELETE"]
}
```

After applying, all processed callouts are automatically removed from the entity files.
