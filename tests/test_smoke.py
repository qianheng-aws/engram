"""Smoke test: verify MemoryGraph works with Obsidian export."""

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from engram.graph import MemoryGraph


def test_graph():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault = os.path.join(tmpdir, "vault")
        for d in ["entities/people", "entities/concepts", "relations", "_meta"]:
            os.makedirs(os.path.join(vault, d), exist_ok=True)

        g = MemoryGraph(vault)

        # Add nodes and edges
        g.upsert_entity("ALICE", {"entity_type": "PERSON", "description": "A curious girl", "source_id": "chunk-1"})
        g.upsert_entity("WONDERLAND", {"entity_type": "CONCEPT", "description": "A magical place", "source_id": "chunk-1"})
        g.upsert_relation("ALICE", "WONDERLAND", {"description": "visits", "weight": 0.9})
        print("  ✅ upsert entities + relations")

        # Verify graph
        assert g.get_entity("ALICE") is not None
        assert g.node_count == 2
        assert g.edge_count == 1
        print("  ✅ get_entity + counts")

        # Test merge on re-upsert
        g.upsert_entity("ALICE", {"entity_type": "PERSON", "description": "Falls down rabbit hole"})
        desc = g.get_entity("ALICE")["description"]
        assert "curious" in desc and "rabbit" in desc
        print("  ✅ description merge on re-upsert")

        # Save + export
        g.save()

        # Check markdown
        alice_path = os.path.join(vault, "entities", "people", "ALICE.md")
        assert os.path.exists(alice_path)
        with open(alice_path) as f:
            content = f.read()
        assert "[[WONDERLAND]]" in content
        print("  ✅ markdown export with [[wikilinks]]")

        # Check relation index
        idx_path = os.path.join(vault, "relations", "_index.md")
        assert os.path.exists(idx_path)
        print("  ✅ relation index")

        # Check GraphML persistence
        assert os.path.exists(os.path.join(vault, "_meta", "graph.graphml"))
        g2 = MemoryGraph(vault)
        assert g2.node_count == 2
        print("  ✅ GraphML persistence + reload")


if __name__ == "__main__":
    print("Testing MemoryGraph...")
    test_graph()
    print("\n🎉 All tests passed!")
