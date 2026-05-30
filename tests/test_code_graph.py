import json
from pathlib import Path

import pytest

from code_graph import Graph, build_graph, to_cytoscape_json, to_json
from code_graph.graph import Edge, Node


def _write(tmp_path: Path, rel: str, body: str) -> None:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)


# -------- graph data model --------

def test_node_dedup():
    g = Graph()
    g.add_node(Node(id="a", kind="module", label="a"))
    g.add_node(Node(id="a", kind="module", label="a"))
    assert g.node_count == 1


def test_edge_dedup():
    g = Graph()
    g.add_edge(Edge(source="a", target="b", kind="imports"))
    g.add_edge(Edge(source="a", target="b", kind="imports"))
    assert g.edge_count == 1


def test_distinct_edge_kinds_are_kept():
    g = Graph()
    g.add_edge(Edge(source="a", target="b", kind="imports"))
    g.add_edge(Edge(source="a", target="b", kind="calls"))
    assert g.edge_count == 2


# -------- extractor --------

def test_module_and_class_nodes(tmp_path: Path):
    _write(tmp_path, "pkg/__init__.py", "")
    _write(tmp_path, "pkg/a.py", "class Foo:\n    def bar(self):\n        pass\n")
    g = build_graph(tmp_path)
    assert g.has_node("pkg.a")
    assert g.has_node("pkg.a:Foo")
    assert g.has_node("pkg.a:Foo.bar")


def test_contains_edges_from_module_to_class(tmp_path: Path):
    _write(tmp_path, "pkg/a.py", "class Foo: pass\n")
    g = build_graph(tmp_path)
    assert Edge("pkg.a", "pkg.a:Foo", "contains") in g.edges


def test_top_level_function_nodes(tmp_path: Path):
    _write(tmp_path, "pkg/u.py", "def helper():\n    return 1\n")
    g = build_graph(tmp_path)
    assert g.has_node("pkg.u:helper")


def test_import_edges_between_indexed_modules(tmp_path: Path):
    _write(tmp_path, "pkg/__init__.py", "")
    _write(tmp_path, "pkg/a.py", "from pkg import b\n")
    _write(tmp_path, "pkg/b.py", "x = 1\n")
    g = build_graph(tmp_path)
    assert any(e.source == "pkg.a" and e.target.startswith("pkg") and e.kind == "imports" for e in g.edges)


def test_inherits_edge_within_module(tmp_path: Path):
    _write(tmp_path, "pkg/x.py", "class Base: pass\nclass Child(Base): pass\n")
    g = build_graph(tmp_path)
    assert Edge("pkg.x:Child", "pkg.x:Base", "inherits") in g.edges


def test_excludes_dotvenv_and_dotgit(tmp_path: Path):
    _write(tmp_path, ".venv/lib/a.py", "x = 1\n")
    _write(tmp_path, ".git/hooks/post.py", "y = 2\n")
    g = build_graph(tmp_path)
    assert g.node_count == 0


def test_skips_syntax_error_files(tmp_path: Path):
    _write(tmp_path, "ok.py", "x = 1\n")
    _write(tmp_path, "broken.py", "def x(:\n")
    g = build_graph(tmp_path)
    assert g.has_node("ok")
    assert g.has_node("broken")     # module node still registers


# -------- export --------

def test_to_json_is_deterministic(tmp_path: Path):
    _write(tmp_path, "pkg/a.py", "class Foo: pass\n")
    g = build_graph(tmp_path)
    a, b = to_json(g), to_json(g)
    assert a == b
    payload = json.loads(a)
    assert "nodes" in payload and "edges" in payload and "summary" in payload
    assert payload["summary"]["node_count"] == g.node_count


def test_cytoscape_export_shape(tmp_path: Path):
    _write(tmp_path, "pkg/a.py", "class Foo: pass\n")
    g = build_graph(tmp_path)
    out = json.loads(to_cytoscape_json(g))
    assert "elements" in out
    sample = out["elements"][0]
    assert "data" in sample and "id" in sample["data"]


def test_self_scan_runs_clean_against_repo():
    # Smoke test: building over our own repo shouldn't throw and should
    # produce a nontrivial graph.
    repo_root = Path(__file__).resolve().parent.parent
    g = build_graph(repo_root)
    assert g.node_count > 50
    assert g.edge_count > 50
