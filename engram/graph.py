"""Graph manager — NetworkX graph + Obsidian markdown export.

Standalone graph operations without nano-graphrag runtime dependency.
Reuses ObsidianGraphStorage format for Obsidian compatibility.
"""

import json
import fcntl
import os
import re

import networkx as nx


GRAPH_FIELD_SEP = "<SEP>"


class MemoryGraph:
    """In-memory knowledge graph with Obsidian vault persistence."""

    def __init__(self, vault_dir: str):
        self.vault_dir = vault_dir
        self.meta_dir = os.path.join(vault_dir, "_meta")
        self.graph_path = os.path.join(self.meta_dir, "graph.graphml")
        os.makedirs(self.meta_dir, exist_ok=True)

        # Load or create graph
        if os.path.exists(self.graph_path):
            self._graph = nx.read_graphml(self.graph_path)
        else:
            self._graph = nx.Graph()

        # Hyperedges: stored as JSON metadata (not as graph nodes)
        self._hyperedge_path = os.path.join(self.meta_dir, "hyperedges.json")
        self._hyperedges: list[dict] = []
        if os.path.exists(self._hyperedge_path):
            with open(self._hyperedge_path) as f:
                self._hyperedges = json.load(f)

    @property
    def node_count(self) -> int:
        return self._graph.number_of_nodes()

    @property
    def edge_count(self) -> int:
        return self._graph.number_of_edges()

    def all_entity_names(self) -> list[str]:
        return list(self._graph.nodes())

    def get_entity(self, name: str) -> dict | None:
        if name in self._graph:
            return dict(self._graph.nodes[name])
        return None

    def get_neighbors(self, name: str) -> list[tuple[str, dict]]:
        """Return [(neighbor_name, edge_attrs), ...]"""
        if name not in self._graph:
            return []
        return [(n, dict(self._graph.edges[name, n])) for n in self._graph.neighbors(name)]

    def upsert_entity(self, name: str, attrs: dict):
        """Add or update an entity node."""
        name = name.upper().strip()
        if name in self._graph:
            existing = self._graph.nodes[name]
            # Merge descriptions: keep first + latest (cap at 2 to prevent bloat)
            old_desc = existing.get("description", "")
            new_desc = attrs.get("description", "")
            if new_desc and new_desc not in old_desc:
                parts = old_desc.split(GRAPH_FIELD_SEP) if old_desc else []
                if len(parts) >= 2:
                    # Keep first (original) + replace last with new
                    attrs["description"] = f"{parts[0]}{GRAPH_FIELD_SEP}{new_desc}"
                else:
                    attrs["description"] = f"{old_desc}{GRAPH_FIELD_SEP}{new_desc}" if old_desc else new_desc
            existing.update(attrs)
        else:
            self._graph.add_node(name, **attrs)

    def upsert_relation(self, source: str, target: str, attrs: dict):
        """Add or update a relation edge."""
        from datetime import datetime as _dt
        source, target = source.upper().strip(), target.upper().strip()
        now = _dt.now().strftime("%Y-%m-%d")
        if self._graph.has_edge(source, target):
            existing = self._graph.edges[source, target]
            # Weight: use max instead of sum to prevent unbounded growth
            old_w = float(existing.get("weight", 1))
            new_w = float(attrs.get("weight", 1))
            attrs["weight"] = max(old_w, new_w)
            # Description: keep first + latest
            old_desc = existing.get("description", "")
            new_desc = attrs.get("description", "")
            if new_desc and new_desc not in old_desc:
                parts = old_desc.split(GRAPH_FIELD_SEP) if old_desc else []
                if len(parts) >= 2:
                    attrs["description"] = f"{parts[0]}{GRAPH_FIELD_SEP}{new_desc}"
                else:
                    attrs["description"] = f"{old_desc}{GRAPH_FIELD_SEP}{new_desc}" if old_desc else new_desc
            # Temporal: preserve first_seen, update last_seen
            attrs["last_seen"] = now
            attrs.setdefault("first_seen", existing.get("first_seen", now))
            existing.update(attrs)
        else:
            attrs["first_seen"] = now
            attrs["last_seen"] = now
            self._graph.add_edge(source, target, **attrs)

    def save(self):
        """Persist graph to GraphML + export Obsidian markdown.

        Uses file lock to prevent concurrent writes from corrupting the graph.
        Re-reads graph before merging to pick up changes from other sessions.
        """
        lock_path = os.path.join(self.meta_dir, "graph.lock")
        with open(lock_path, "w") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            try:
                # Re-read disk graph and merge our changes on top
                if os.path.exists(self.graph_path):
                    disk_graph = nx.read_graphml(self.graph_path)
                    # Merge: disk is base, our in-memory changes win
                    for node, attrs in self._graph.nodes(data=True):
                        disk_graph.add_node(node, **attrs)
                    for u, v, attrs in self._graph.edges(data=True):
                        disk_graph.add_edge(u, v, **attrs)
                    # Remove nodes deleted in-memory (e.g. by merge/prune)
                    for node in list(disk_graph.nodes()):
                        if node not in self._graph:
                            disk_graph.remove_node(node)
                    self._graph = disk_graph

                nx.write_graphml(self._graph, self.graph_path)
                self._export_entities()
                self._export_relation_index()
                # Save hyperedges
                with open(self._hyperedge_path, "w") as f:
                    json.dump(self._hyperedges, f, indent=2)
                self._export_hyperedges()
            finally:
                fcntl.flock(lock_file, fcntl.LOCK_UN)

    # --- Obsidian export ---

    TYPE_FOLDER = {
        "PERSON": "people", "CONCEPT": "concepts", "PROJECT": "projects",
        "TOOL": "tools", "ORGANIZATION": "orgs", "ORG": "orgs",
        "EVENT": "concepts", "LOCATION": "concepts",
    }

    def _safe_filename(self, name: str) -> str:
        return re.sub(r'[<>:"/\\|?*]', "_", name)[:200] + ".md"

    def _export_entities(self):
        from datetime import datetime as _dt
        entities_dir = os.path.join(self.vault_dir, "entities")
        G = self._graph

        # Pre-compute bridge nodes (betweenness centrality > 0, top 20% of non-zero)
        bridge_nodes = set()
        if G.number_of_nodes() >= 3:
            betweenness = nx.betweenness_centrality(G, weight="weight")
            nonzero = sorted([bc for bc in betweenness.values() if bc > 0], reverse=True)
            if nonzero:
                threshold = nonzero[min(len(nonzero) - 1, len(nonzero) // 5)]
                bridge_nodes = {n for n, bc in betweenness.items() if bc >= threshold}

        # Pre-compute stale nodes (decay score < 0.3)
        stale_nodes = set()
        today = _dt.now()
        for name in G.nodes():
            last = G.nodes[name].get("last_updated", "")
            try:
                days_ago = (today - _dt.strptime(last, "%Y-%m-%d")).days
            except (ValueError, TypeError):
                days_ago = 365
            degree = G.degree(name)
            time_score = 0.5 ** (days_ago / 30)
            conn_bonus = min(degree / 10, 1.0) * 0.3
            if time_score + conn_bonus < 0.3:
                stale_nodes.add(name)

        for node_id in G.nodes():
            attrs = G.nodes[node_id]
            entity_type = attrs.get("entity_type", "CONCEPT")
            folder = self.TYPE_FOLDER.get(entity_type.upper(), "concepts")
            dirpath = os.path.join(entities_dir, folder)
            os.makedirs(dirpath, exist_ok=True)

            description = attrs.get("description", "")
            # Use latest description (after SEP) if available, else first
            desc_parts = description.split(GRAPH_FIELD_SEP) if description else [""]
            full_desc = desc_parts[-1].strip() if desc_parts else ""
            degree = G.degree(node_id)

            relations = []
            neighbor_types = set()
            for neighbor in G.neighbors(node_id):
                edge = G.edges[node_id, neighbor]
                desc = (edge.get("description", "") or "").split(GRAPH_FIELD_SEP)[-1]
                weight = float(edge.get("weight", 1))
                relations.append((neighbor, desc, weight))
                n_type = G.nodes[neighbor].get("entity_type", "")
                if n_type:
                    neighbor_types.add(n_type.lower())

            # Build tags: type hierarchy + dynamic status
            type_lower = entity_type.lower()
            tags = [f"entity/{type_lower}"]
            for nt in sorted(neighbor_types):
                tags.append(f"has/{nt}")
            if node_id in bridge_nodes:
                tags.append("bridge")
            if node_id in stale_nodes:
                tags.append("stale")

            # Build aliases from underscore-separated name
            alias = node_id.replace("_", " ").title()
            aliases = [alias] if alias != node_id else []

            confidence = attrs.get("confidence", "EXTRACTED")

            lines = [
                "---",
                f"entity_type: {entity_type}",
                f"confidence: {confidence}",
                f"tags:",
            ]
            for t in tags:
                lines.append(f"  - {t}")
            lines.append(f"  - confidence/{confidence.lower()}")
            if aliases:
                lines.append(f"aliases:")
                for a in aliases:
                    lines.append(f"  - \"{a}\"")
            created = attrs.get('source_id', '').replace('session-', '') or ''
            last_updated = attrs.get('last_updated', '')
            local_path = attrs.get("local_path", "")
            url = attrs.get("url", "")
            date_lines = [
                f"created: \"[[daily/{created}|{created}]]\"" if created else "created:",
                f"last_updated: \"[[daily/{last_updated}|{last_updated}]]\"" if last_updated else "last_updated:",
            ]
            if url:
                date_lines.append(f"url: \"{url}\"")
            if local_path:
                date_lines.append(f"local_path: \"{local_path}\"")
            lines += date_lines + [
                f"degree: {degree}",
                f"cssclasses:",
                f"  - entity",
                f"  - {type_lower}",
                "---", "",
                f"# {node_id}", "",
            ]

            # Full markdown description (not truncated)
            if full_desc:
                lines.append(full_desc)
                lines.append("")

            # References section
            refs_raw = attrs.get("references", "")
            if refs_raw:
                try:
                    refs = json.loads(refs_raw) if isinstance(refs_raw, str) else refs_raw
                except (json.JSONDecodeError, TypeError):
                    refs = []
                if refs:
                    lines += ["## References", ""]
                    for url in refs:
                        lines.append(f"- {url}")
                    lines.append("")

            if relations:
                lines += ["## Relations", ""]
                for n, d, w in sorted(relations, key=lambda x: -x[2]):
                    n_type = self._graph.nodes[n].get("entity_type", "")
                    type_badge = f"`{n_type}`" if n_type else ""
                    lines.append(f"- [[{n}]] {type_badge} — {d} (weight: {w:.1f})")
                lines.append("")

            # Community membership (via Dataview)
            lines += [
                "## Communities", "",
                "```dataview",
                f"LIST FROM #community WHERE contains(members, \"{node_id}\")",
                "```", "",
            ]

            path = os.path.join(dirpath, self._safe_filename(node_id))
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")

    def detect_communities(self, min_size: int = 2) -> list[dict]:
        """Detect communities using Louvain algorithm.

        Returns list of community dicts with members and metadata.
        """
        if self._graph.number_of_nodes() < 3:
            return []

        # Louvain handles disconnected graphs — each component is partitioned independently
        communities_raw = nx.community.louvain_communities(
            self._graph, weight="weight", seed=42
        )

        communities = []
        for i, members in enumerate(sorted(communities_raw, key=len, reverse=True)):
            if len(members) < min_size:
                continue
            # Gather member details
            member_details = []
            for name in sorted(members):
                attrs = dict(self._graph.nodes[name])
                desc = (attrs.get("description", "") or "").split(GRAPH_FIELD_SEP)[0][:120]
                member_details.append({
                    "name": name,
                    "entity_type": attrs.get("entity_type", "CONCEPT"),
                    "description": desc,
                })

            # Internal edges
            subgraph = self._graph.subgraph(members)
            internal_edges = []
            for u, v, data in subgraph.edges(data=True):
                desc = (data.get("description", "") or "").split(GRAPH_FIELD_SEP)[0][:80]
                internal_edges.append({
                    "source": u, "target": v,
                    "weight": float(data.get("weight", 1)),
                    "description": desc,
                })

            communities.append({
                "id": i,
                "size": len(members),
                "members": member_details,
                "internal_edges": internal_edges,
                "density": nx.density(subgraph),
            })

        return communities

    def find_surprising_connections(self, communities: list[dict], top_n: int = 10) -> list[dict]:
        """Find cross-community edges ranked by surprise score.

        Surprise = edge weight × (1 / min(community_size_a, community_size_b)).
        High-weight edges between small, distinct communities are most surprising.
        """
        # Build node → community_id mapping
        node_community = {}
        community_sizes = {}
        for c in communities:
            cid = c["id"]
            members = [m["name"] if isinstance(m, dict) else m for m in c["members"]]
            community_sizes[cid] = len(members)
            for m in members:
                node_community[m] = cid

        cross_edges = []
        for u, v, data in self._graph.edges(data=True):
            cu = node_community.get(u)
            cv = node_community.get(v)
            if cu is None or cv is None or cu == cv:
                continue
            weight = float(data.get("weight", 1))
            min_size = min(community_sizes.get(cu, 1), community_sizes.get(cv, 1))
            surprise = weight / min_size
            desc = (data.get("description", "") or "").split(GRAPH_FIELD_SEP)[0][:120]
            cross_edges.append({
                "source": u, "target": v,
                "source_community": cu, "target_community": cv,
                "weight": weight,
                "surprise_score": round(surprise, 3),
                "description": desc,
            })

        cross_edges.sort(key=lambda x: -x["surprise_score"])
        return cross_edges[:top_n]

    def export_community(self, community_id: int, title: str, summary: str, members: list[str]):
        """Write a community summary to the vault."""
        from datetime import datetime as _dt
        comm_dir = os.path.join(self.vault_dir, "communities")
        os.makedirs(comm_dir, exist_ok=True)

        # Calculate density for this community's subgraph
        subgraph = self._graph.subgraph(members)
        density = nx.density(subgraph) if len(members) > 1 else 0.0

        lines = [
            "---",
            f"community_id: {community_id}",
            f"tags:",
            f"  - community",
            f"members: {json.dumps(sorted(members))}",
            f"size: {len(members)}",
            f"density: {density:.3f}",
            f"created: \"[[daily/{_dt.now().strftime('%Y-%m-%d')}|{_dt.now().strftime('%Y-%m-%d')}]]\"",
            f"cssclasses:",
            f"  - community",
            "---", "",
            f"# {title}", "",
            summary, "",
            "## Members", "",
        ]
        for name in sorted(members):
            entity_type = self._graph.nodes[name].get("entity_type", "") if name in self._graph else ""
            type_badge = f" `{entity_type}`" if entity_type else ""
            lines.append(f"- [[{name}]]{type_badge}")
        lines.append("")

        fname = re.sub(r'[<>:"/\\|?*]', "_", title)[:200] + ".md"
        path = os.path.join(comm_dir, fname)
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        return path

    def god_nodes(self, top_n: int = 10) -> list[dict]:
        """Identify the most important entities by degree + PageRank."""
        G = self._graph
        if G.number_of_nodes() == 0:
            return []

        pagerank = nx.pagerank(G, weight="weight") if G.number_of_nodes() > 1 else {n: 1.0 for n in G.nodes()}
        max_pr = max(pagerank.values()) or 1.0
        max_degree = max(G.degree(n) for n in G.nodes()) or 1

        results = []
        for name in G.nodes():
            attrs = dict(G.nodes[name])
            degree = G.degree(name)
            pr = pagerank.get(name, 0)
            # Composite score: 50% degree-based + 50% PageRank-based
            score = 0.5 * (degree / max_degree) + 0.5 * (pr / max_pr)
            desc = (attrs.get("description", "") or "").split(GRAPH_FIELD_SEP)[0][:120]
            results.append({
                "name": name,
                "entity_type": attrs.get("entity_type", "CONCEPT"),
                "degree": degree,
                "pagerank": round(pr, 4),
                "score": round(score, 3),
                "description": desc,
            })

        results.sort(key=lambda x: -x["score"])
        return results[:top_n]

    def knowledge_gaps(self) -> dict:
        """Find isolated nodes and thin communities."""
        G = self._graph

        # Isolated: degree == 0 (no connections)
        isolated = []
        for name in G.nodes():
            degree = G.degree(name)
            if degree == 0:
                attrs = dict(G.nodes[name])
                desc = (attrs.get("description", "") or "").split(GRAPH_FIELD_SEP)[0][:120]
                isolated.append({
                    "name": name,
                    "entity_type": attrs.get("entity_type", "CONCEPT"),
                    "degree": degree,
                    "description": desc,
                })

        # Thin communities: size < 3
        thin = []
        if G.number_of_nodes() >= 3:
            communities_raw = nx.community.louvain_communities(G, weight="weight", seed=42)
            for i, members in enumerate(sorted(communities_raw, key=len, reverse=True)):
                if len(members) < 3:
                    thin.append({
                        "community_id": i,
                        "size": len(members),
                        "members": sorted(members),
                    })

        return {"isolated_nodes": isolated, "thin_communities": thin}

    def suggested_questions(self, top_n: int = 5) -> list[dict]:
        """Generate questions based on betweenness centrality.

        High-betweenness nodes are bridges between communities —
        questions about why they connect clusters reveal non-obvious insights.
        """
        G = self._graph
        if G.number_of_nodes() < 3:
            return []

        betweenness = nx.betweenness_centrality(G, weight="weight")
        if not betweenness or max(betweenness.values()) == 0:
            return []

        # Detect communities for context
        communities_raw = nx.community.louvain_communities(G, weight="weight", seed=42)
        node_community = {}
        community_names = {}
        for i, members in enumerate(sorted(communities_raw, key=len, reverse=True)):
            for m in members:
                node_community[m] = i
            # Use the highest-degree member as community label
            top_member = max(members, key=lambda n: G.degree(n))
            community_names[i] = top_member

        # Rank by betweenness, filter to nodes that actually bridge communities
        candidates = []
        for name, bc in sorted(betweenness.items(), key=lambda x: -x[1]):
            if bc <= 0:
                continue
            neighbor_communities = set()
            for neighbor in G.neighbors(name):
                nc = node_community.get(neighbor)
                if nc is not None:
                    neighbor_communities.add(nc)
            own_community = node_community.get(name)
            if own_community is not None:
                neighbor_communities.discard(own_community)
            if not neighbor_communities:
                continue

            attrs = dict(G.nodes[name])
            desc = (attrs.get("description", "") or "").split(GRAPH_FIELD_SEP)[0][:120]
            connected_communities = sorted(
                [community_names.get(c, f"community-{c}") for c in neighbor_communities]
            )
            question = f"Why does {name} connect to {', '.join(connected_communities[:3])}?"
            candidates.append({
                "node": name,
                "question": question,
                "betweenness": round(bc, 4),
                "description": desc,
                "connected_communities": len(neighbor_communities),
            })
            if len(candidates) >= top_n:
                break

        return candidates

    def add_hyperedge(self, id: str, label: str, members: list[str], relation: str = "form"):
        """Add or update a group relationship."""
        members = [m.upper().strip() for m in members]
        # Upsert by id
        for i, h in enumerate(self._hyperedges):
            if h["id"] == id:
                self._hyperedges[i] = {"id": id, "label": label, "members": members, "relation": relation}
                return
        self._hyperedges.append({"id": id, "label": label, "members": members, "relation": relation})

    def get_hyperedges(self) -> list[dict]:
        """Return all hyperedges."""
        return list(self._hyperedges)

    def _export_relation_index(self):
        rel_dir = os.path.join(self.vault_dir, "relations")
        os.makedirs(rel_dir, exist_ok=True)
        lines = [
            "---",
            f"tags:",
            f"  - index",
            f"total_relations: {self.edge_count}",
            f"total_entities: {self.node_count}",
            "---", "",
            "# Relation Index", "",
            "| Source | Target | Weight | Confidence | Score | First Seen | Last Seen | Description |",
            "|--------|--------|--------|------------|-------|------------|-----------|-------------|",
        ]
        for u, v, data in sorted(self._graph.edges(data=True), key=lambda x: -float(x[2].get("weight", 0))):
            desc = (data.get("description", "") or "").split(GRAPH_FIELD_SEP)[0]
            weight = float(data.get("weight", 1))
            confidence = data.get("confidence", "EXTRACTED")
            conf_score = data.get("confidence_score", "")
            conf_score_str = f"{float(conf_score):.2f}" if conf_score != "" else ""
            first_seen = data.get("first_seen", "")
            last_seen = data.get("last_seen", "")
            fs_link = f"[[daily/{first_seen}|{first_seen}]]" if first_seen else ""
            ls_link = f"[[daily/{last_seen}|{last_seen}]]" if last_seen else ""
            lines.append(f"| [[{u}]] | [[{v}]] | {weight:.1f} | {confidence} | {conf_score_str} | {fs_link} | {ls_link} | {desc} |")

        with open(os.path.join(rel_dir, "_index.md"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    def _export_hyperedges(self):
        """Export hyperedges as MOC (Map of Content) files in groups/ folder."""
        groups_dir = os.path.join(self.vault_dir, "groups")
        os.makedirs(groups_dir, exist_ok=True)
        for h in self._hyperedges:
            lines = [
                "---",
                f"type: hyperedge",
                f"relation: {h['relation']}",
                f"members: {json.dumps(sorted(h['members']))}",
                f"size: {len(h['members'])}",
                f"tags:",
                f"  - hyperedge",
                f"  - group",
                "---", "",
                f"# {h['label']}", "",
                f"This group connects {len(h['members'])} entities via **{h['relation']}** relationship.", "",
                "## Members", "",
            ]
            for name in sorted(h["members"]):
                entity_type = ""
                if name in self._graph:
                    entity_type = self._graph.nodes[name].get("entity_type", "")
                type_badge = f" `{entity_type}`" if entity_type else ""
                lines.append(f"- [[{name}]]{type_badge}")
            lines.append("")

            fname = re.sub(r'[<>:"/\\|?*]', "_", h["label"])[:200] + ".md"
            path = os.path.join(groups_dir, fname)
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
