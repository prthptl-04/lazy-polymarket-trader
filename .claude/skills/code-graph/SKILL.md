---
name: code-graph
description: Build a deterministic AST-derived knowledge graph of this codebase (modules, classes, functions; imports/inherits/contains/calls edges). MIT-clean replacement for GitNexus, which has a PolyForm-Noncommercial license unsuitable for our commercial use case. Output feeds the dashboard's force-directed view via Cytoscape.js elements format.
license: MIT
---

# Code Graph Skill

Use this when the user asks:

- *"Show me the codebase as a graph"*
- *"What depends on `X`?"* / *"What does `Y` import?"*
- *"Generate the data for the dashboard's knowledge-graph panel"*
- During architecture reviews (Winston) — quickly inspect coupling

## Why we built our own

GitNexus is licensed PolyForm-Noncommercial-1.0.0 — incompatible with a
commercial trading bot. We built `code_graph/` instead. It covers the
boring 95% of what GitNexus offers for our needs (the file/class/function
graph) without legal entanglement. Dynamic dispatch + decorator rewriting
are out of scope; we accept the trade-off for a clean license.

## How to invoke

```python
from code_graph import build_graph, to_json, to_cytoscape_json

g = build_graph(".")            # walks every .py under root (excludes .venv, etc.)
print(to_json(g))               # human-readable JSON
open("outputs/code_graph.cyto.json", "w").write(to_cytoscape_json(g))
```

Outputs are deterministic — the same source tree produces byte-identical JSON
across runs. That makes them safe to commit or diff.

## Output schema

### Plain JSON

```json
{
  "nodes": [{"id": "...", "kind": "module|class|function|method", "label": "...", ...}],
  "edges": [{"source": "...", "target": "...", "kind": "imports|calls|inherits|contains"}],
  "summary": {"node_count": N, "edge_count": M, "node_kinds": {...}, "edge_kinds": {...}}
}
```

### Cytoscape.js elements

```json
{"elements": [
  {"data": {"id": "agents.orchestrator", "label": "agents.orchestrator", "kind": "module", ...}},
  {"data": {"id": "e:agents.orchestrator->memory.store:imports:0", "source": "...", "target": "...", "kind": "imports"}}
]}
```

The dashboard reads the Cytoscape variant directly into `cy.json({elements})`.

## What this skill does NOT do

- Run on every commit — the graph is a snapshot, not live. Regenerate before
  publishing the dashboard or after a significant refactor.
- Cross-language analysis — Python only. JavaScript / TypeScript stays
  out of scope.
- Edit code based on the graph — read-only insight.
