"""Export the Graph to two formats:

- plain JSON   — for downstream tooling / regression tests
- Cytoscape.js — for the Phase-C dashboard's force-directed view

Both are deterministic w.r.t. graph contents: same graph in → same bytes out.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from code_graph.graph import Graph


def to_json(graph: Graph) -> str:
    payload = {
        "nodes": [asdict(graph.nodes[nid]) for nid in sorted(graph.nodes)],
        "edges": [
            asdict(e) for e in sorted(graph.edges, key=lambda e: (e.source, e.target, e.kind))
        ],
        "summary": {
            "node_count": graph.node_count,
            "edge_count": graph.edge_count,
            "node_kinds": _count_by(graph, "kind", on="nodes"),
            "edge_kinds": _count_by(graph, "kind", on="edges"),
        },
    }
    return json.dumps(payload, indent=2)


def to_cytoscape_json(graph: Graph) -> str:
    """Cytoscape.js elements format: a flat list of {data: ...} entries.

    Edge ids are synthesized so duplicates between same endpoints get
    deterministic distinct ids.
    """
    elements: list[dict[str, Any]] = []
    for nid in sorted(graph.nodes):
        n = graph.nodes[nid]
        elements.append({
            "data": {
                "id": n.id,
                "label": n.label,
                "kind": n.kind,
                "package": n.package,
                "file": n.file,
                "line": n.line,
            }
        })
    seen: dict[tuple[str, str, str], int] = {}
    for e in sorted(graph.edges, key=lambda e: (e.source, e.target, e.kind)):
        key = (e.source, e.target, e.kind)
        idx = seen.get(key, 0)
        seen[key] = idx + 1
        elements.append({
            "data": {
                "id": f"e:{e.source}->{e.target}:{e.kind}:{idx}",
                "source": e.source,
                "target": e.target,
                "kind": e.kind,
            }
        })
    return json.dumps({"elements": elements}, indent=2)


def _count_by(graph: Graph, attr: str, *, on: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    items = graph.nodes.values() if on == "nodes" else graph.edges
    for item in items:
        key = getattr(item, attr)
        counts[key] = counts.get(key, 0) + 1
    return counts
