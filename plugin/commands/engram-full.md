# Engram Full — Complete memory consolidation

Run all six engram stages: replay, feedback, integrate, prune, community, abstract.

## Steps

**1. Replay** — Run `/engram` first (extract entities from current session).

**2. Feedback** — Process human corrections:
```bash
engram feedback```
If callouts exist, review and apply fixes. See `/engram-feedback` for details.

**3. Integrate** — Find duplicate entities:
```bash
engram integrate```
Review candidates and merge if needed.

**4. Prune** — Check for decaying entities:
```bash
engram prune```
Review fading/archivable entities. Also check `knowledge_gaps` from status — isolated nodes (degree 0) are strong prune candidates.

**5. Community** — Detect and summarize knowledge clusters:
```bash
engram community```
For each community, generate a title and summary. If `surprising_connections` exist, also generate a cross-community comparison entry with a structured comparison (table or matrix) of the connected entities. Then save:
```bash
echo '<json>' | engram community --stdin
```
Community JSON: `{"communities": [{"id": 0, "title": "...", "summary": "...", "members": ["A", "B"]}]}`

**6. Abstract** — Discover behavioral patterns:
```bash
engram abstract```
Analyze the returned daily notes for recurring behaviors, decision preferences, and problem patterns. Also review `suggested_questions` from status — bridge nodes often reveal cross-domain patterns. Output JSON and save:
```bash
echo '<json>' | engram save-pattern --stdin
```
Pattern JSON: `{"new_patterns": [{"name": "...", "description": "...", "evidence": ["2026-04-06"], "confidence": 0.7}], "updated_patterns": []}`

**7. Lint** — Validate vault consistency:
```bash
engram lint```
Checks: GraphML ↔ markdown sync, dead wikilinks, orphan nodes (degree 0), frontmatter completeness. If issues > 0, review and fix before finishing.
