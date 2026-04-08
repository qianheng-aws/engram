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
                    self._graph = disk_graph

                nx.write_graphml(self._graph, self.graph_path)
                self._export_entities()
                self._export_relation_index()
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
        entities_dir = os.path.join(self.vault_dir, "entities")
        for node_id in self._graph.nodes():
            attrs = self._graph.nodes[node_id]
            entity_type = attrs.get("entity_type", "CONCEPT")
            folder = self.TYPE_FOLDER.get(entity_type.upper(), "concepts")
            dirpath = os.path.join(entities_dir, folder)
            os.makedirs(dirpath, exist_ok=True)

            description = attrs.get("description", "")
            first_desc = description.split(GRAPH_FIELD_SEP)[0] if description else ""
            degree = self._graph.degree(node_id)

            relations = []
            neighbor_types = set()
            for neighbor in self._graph.neighbors(node_id):
                edge = self._graph.edges[node_id, neighbor]
                desc = (edge.get("description", "") or "").split(GRAPH_FIELD_SEP)[0]
                weight = float(edge.get("weight", 1))
                relations.append((neighbor, desc, weight))
                n_type = self._graph.nodes[neighbor].get("entity_type", "")
                if n_type:
                    neighbor_types.add(n_type.lower())

            # Build tags: type hierarchy + neighbor type connections
            type_lower = entity_type.lower()
            tags = [f"entity/{type_lower}"]

            # Build aliases from underscore-separated name
            alias = node_id.replace("_", " ").title()
            aliases = [alias] if alias != node_id else []

            lines = [
                "---",
                f"entity_type: {entity_type}",
                f"tags:",
            ]
            for t in tags:
                lines.append(f"  - {t}")
            if aliases:
                lines.append(f"aliases:")
                for a in aliases:
                    lines.append(f"  - \"{a}\"")
            lines += [
                f"created: {attrs.get('source_id', '').replace('session-', '') or ''}",
                f"last_updated: {attrs.get('last_updated', '')}",
                f"degree: {degree}",
                f"cssclasses:",
                f"  - entity",
                f"  - {type_lower}",
                "---", "",
                f"# {node_id}", "",
                first_desc, "",
            ]

            if relations:
                lines += ["## Relations", ""]
                for n, d, w in sorted(relations, key=lambda x: -x[2]):
                    n_type = self._graph.nodes[n].get("entity_type", "")
                    type_badge = f"`{n_type}`" if n_type else ""
                    lines.append(f"- [[{n}]] {type_badge} — {d} (weight: {w:.1f})")
                lines.append("")

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
            f"created: {_dt.now().strftime('%Y-%m-%d')}",
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
            "| Source | Target | Weight | First Seen | Last Seen | Description |",
            "|--------|--------|--------|------------|-----------|-------------|",
        ]
        for u, v, data in sorted(self._graph.edges(data=True), key=lambda x: -float(x[2].get("weight", 0))):
            desc = (data.get("description", "") or "").split(GRAPH_FIELD_SEP)[0]
            weight = float(data.get("weight", 1))
            first_seen = data.get("first_seen", "")
            last_seen = data.get("last_seen", "")
            lines.append(f"| [[{u}]] | [[{v}]] | {weight:.1f} | {first_seen} | {last_seen} | {desc} |")

        with open(os.path.join(rel_dir, "_index.md"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
