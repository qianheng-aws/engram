# Engram Status — Show vault statistics

```bash
engram status```

Report the results:
- **Overview:** node count, edge count, density, daily notes, patterns, pending sessions
- **God nodes:** top entities by degree + PageRank — these are your knowledge hubs
- **Knowledge gaps:** isolated nodes (degree 0) and thin communities (< 3 members) — candidates for connecting or pruning
- **Surprising connections:** cross-community edges ranked by surprise score — non-obvious relationships worth exploring
- **Suggested questions:** bridge nodes between communities — questions the graph is uniquely positioned to answer
