# Engram Full — Complete memory consolidation

Run all five engram stages: replay, integrate, prune, community, abstract.

## Steps

**1. Replay** — Run `/engram` first (extract entities from current session).

**2. Integrate** — Find duplicate entities:
```bash
python3 "${CLAUDE_PLUGIN_ROOT}/../engram_cli.py" integrate --vault ~/.engram/vault
```
Review candidates and merge if needed.

**3. Prune** — Check for decaying entities:
```bash
python3 "${CLAUDE_PLUGIN_ROOT}/../engram_cli.py" prune --vault ~/.engram/vault
```
Review fading/archivable entities.

**4. Community** — Detect and summarize knowledge clusters:
```bash
python3 "${CLAUDE_PLUGIN_ROOT}/../engram_cli.py" community --vault ~/.engram/vault
```
For each community, generate a title and summary. Then save:
```bash
echo '<json>' | python3 "${CLAUDE_PLUGIN_ROOT}/../engram_cli.py" community --vault ~/.engram/vault --stdin
```
Community JSON: `{"communities": [{"id": 0, "title": "...", "summary": "...", "members": ["A", "B"]}]}`

**5. Abstract** — Discover behavioral patterns:
```bash
python3 "${CLAUDE_PLUGIN_ROOT}/../engram_cli.py" abstract --vault ~/.engram/vault
```
Analyze the returned daily notes for recurring behaviors, decision preferences, and problem patterns. Output JSON and save:
```bash
echo '<json>' | python3 "${CLAUDE_PLUGIN_ROOT}/../engram_cli.py" save-pattern --vault ~/.engram/vault --stdin
```
Pattern JSON: `{"new_patterns": [{"name": "...", "description": "...", "evidence": ["2026-04-06"], "confidence": 0.7}], "updated_patterns": []}`
