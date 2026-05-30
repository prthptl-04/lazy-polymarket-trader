"""Code-graph data model.

A directed labeled graph keyed by stable string ids. Nodes are modules,
classes, functions; edges are imports, calls, inherits, contains. Schema
is the contract the dashboard reads — keep it tight.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


NodeKind = Literal["module", "class", "function", "method"]
EdgeKind = Literal["imports", "calls", "inherits", "contains"]


@dataclass(frozen=True)
class Node:
    id: str                  # e.g. "agents.orchestrator:run_specialist"
    kind: NodeKind
    label: str               # short display name
    file: str | None = None
    line: int | None = None
    package: str | None = None     # top-level package (e.g. "agents")


@dataclass(frozen=True)
class Edge:
    source: str              # Node.id
    target: str              # Node.id
    kind: EdgeKind


@dataclass
class Graph:
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)

    def add_node(self, node: Node) -> None:
        if node.id not in self.nodes:
            self.nodes[node.id] = node

    def add_edge(self, edge: Edge) -> None:
        # Cheap dedupe — graphs at our scale (<10k edges) make linear OK.
        if edge not in self.edges:
            self.edges.append(edge)

    def has_node(self, node_id: str) -> bool:
        return node_id in self.nodes

    @property
    def node_count(self) -> int:
        return len(self.nodes)

    @property
    def edge_count(self) -> int:
        return len(self.edges)
