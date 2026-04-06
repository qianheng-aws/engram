# Dream Full — Complete memory consolidation

Run all four OODA dream stages: replay, integrate, prune, abstract.

## Steps

**1. Replay** — Run `/dream` first (extract entities from current session).

**2. Integrate** — Find duplicate entities:
```bash
python3 /workplace/qianheng/ooda-memory/dream_cli.py integrate --vault ~/.engram/vault
```
Review candidates and merge if needed.

**3. Prune** — Check for decaying entities:
```bash
python3 /workplace/qianheng/ooda-memory/dream_cli.py prune --vault ~/.engram/vault
```
Review fading/archivable entities.

**4. Abstract** — Discover behavioral patterns:
```bash
python3 /workplace/qianheng/ooda-memory/dream_cli.py abstract --vault ~/.engram/vault
```
Analyze the returned daily notes for recurring behaviors, decision preferences, and problem patterns. Output JSON and save:
```bash
echo '<json>' | python3 /workplace/qianheng/ooda-memory/dream_cli.py save-pattern --vault ~/.engram/vault --stdin
```
Pattern JSON: `{"new_patterns": [{"name": "...", "description": "...", "evidence": ["2026-04-06"], "confidence": 0.7}], "updated_patterns": []}`
