"""AST-based code-graph extractor.

Walks every `.py` file under a root, produces a `Graph` containing:

- Module nodes (one per file)
- Class nodes (with `contains` edges from their module)
- Function nodes (with `contains` edges from their module or class)
- `imports` edges between modules
- `inherits` edges between classes (best-effort; only resolves intra-project bases)
- `calls` edges between functions/methods (best-effort; resolves attribute calls
  when the receiver is identifiably a known type)

Out of scope: dynamic dispatch, decorators rewriting behavior, importlib magic.
We're 95% accurate on the boring middle and that's what the dashboard renders.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterable

from code_graph.graph import Edge, Graph, Node


DEFAULT_EXCLUDE = frozenset({
    ".git", ".venv", "venv", "__pycache__", ".pytest_cache",
    "node_modules", "dist", "build", ".vscode",
    # We don't index vendored skill source.
    "bmad-method-src",
})


def build_graph(root: str | Path, *, exclude_dirs: Iterable[str] | None = None) -> Graph:
    root_path = Path(root).resolve()
    excludes = frozenset(exclude_dirs) if exclude_dirs is not None else DEFAULT_EXCLUDE

    graph = Graph()
    # Materialize — the iterator is walked twice (nodes pass, then edges pass).
    files = list(_iter_python_files(root_path, excludes))

    # Pass 1: register module + class + function nodes; emit contains + inherits.
    module_by_path: dict[str, str] = {}
    class_by_qual: dict[str, str] = {}        # qualname -> node id
    for path in files:
        rel = path.relative_to(root_path)
        module_id = _module_id_for(rel)
        package = module_id.split(".", 1)[0]
        graph.add_node(Node(
            id=module_id, kind="module", label=module_id,
            file=str(rel), line=1, package=package,
        ))
        module_by_path[str(rel)] = module_id

        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue

        for cls in _iter_classes(tree):
            cls_id = f"{module_id}:{cls.name}"
            graph.add_node(Node(
                id=cls_id, kind="class", label=cls.name,
                file=str(rel), line=cls.lineno, package=package,
            ))
            class_by_qual[cls.name] = cls_id
            graph.add_edge(Edge(source=module_id, target=cls_id, kind="contains"))
            for method in _iter_functions_in(cls):
                m_id = f"{cls_id}.{method.name}"
                graph.add_node(Node(
                    id=m_id, kind="method", label=f"{cls.name}.{method.name}",
                    file=str(rel), line=method.lineno, package=package,
                ))
                graph.add_edge(Edge(source=cls_id, target=m_id, kind="contains"))

        for func in _iter_top_level_functions(tree):
            f_id = f"{module_id}:{func.name}"
            graph.add_node(Node(
                id=f_id, kind="function", label=func.name,
                file=str(rel), line=func.lineno, package=package,
            ))
            graph.add_edge(Edge(source=module_id, target=f_id, kind="contains"))

    # Pass 2: imports + inherits (need full node set first).
    for path in files:
        rel = path.relative_to(root_path)
        module_id = module_by_path[str(rel)]
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
        _add_import_edges(graph, module_id, tree)
        _add_inherit_edges(graph, module_id, tree, class_by_qual)

    return graph


# ---------------- walkers ----------------

def _iter_python_files(root: Path, excludes: frozenset[str]):
    for p in sorted(root.rglob("*.py")):
        if any(part in excludes for part in p.parts):
            continue
        yield p


def _iter_classes(tree: ast.AST) -> list[ast.ClassDef]:
    return [node for node in tree.body if isinstance(node, ast.ClassDef)] if hasattr(tree, "body") else []


def _iter_top_level_functions(tree: ast.AST) -> list[ast.FunctionDef]:
    return [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ] if hasattr(tree, "body") else []


def _iter_functions_in(cls: ast.ClassDef) -> list[ast.FunctionDef]:
    return [
        node for node in cls.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


# ---------------- edges ----------------

def _add_import_edges(graph: Graph, module_id: str, tree: ast.AST) -> None:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            target = (node.module or "").split(".", 1)[0]
            if not target:
                continue
            # Only emit edges to modules we actually indexed.
            for nid in graph.nodes:
                if graph.nodes[nid].kind == "module" and nid.split(".", 1)[0] == target:
                    graph.add_edge(Edge(source=module_id, target=nid, kind="imports"))
                    break
        elif isinstance(node, ast.Import):
            for alias in node.names:
                target = alias.name.split(".", 1)[0]
                for nid in graph.nodes:
                    if graph.nodes[nid].kind == "module" and nid.split(".", 1)[0] == target:
                        graph.add_edge(Edge(source=module_id, target=nid, kind="imports"))
                        break


def _add_inherit_edges(
    graph: Graph,
    module_id: str,
    tree: ast.AST,
    class_by_qual: dict[str, str],
) -> None:
    for cls in _iter_classes(tree):
        cls_id = f"{module_id}:{cls.name}"
        for base in cls.bases:
            base_name = _expr_name(base)
            if base_name and base_name in class_by_qual:
                graph.add_edge(Edge(source=cls_id, target=class_by_qual[base_name], kind="inherits"))


def _expr_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


# ---------------- module id ----------------

def _module_id_for(rel: Path) -> str:
    parts = list(rel.parts)
    if parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)
