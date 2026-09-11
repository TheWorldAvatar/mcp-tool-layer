# validate/report

Build `generated/reports/<ontology>/generation_report.json` from independent
checkers. Each checker returns failures, warnings, and optional observation
records.

`assemble.build_validation_report` is the public entry.

## Files

| File | Role |
| --- | --- |
| `assemble.py` | Run checkers and write the report |
| `common.py` | Shared import, AST, graph, and obligation helpers |
| `prompts.py` | Prompt quality, runtime-slot binding, iteration schema |
| `syntax.py` | Parse / import smoke checks on generated Python |
| `tool_surface.py` | MCP tool names, relationship parameters, ordered members |
| `operation_units.py` | Operation-unit contracts vs generated manifests |
| `foreign_symbols.py` | Cross-ontology local-name leakage |
| `ttl.py` | Parse any generated Turtle exports |
| [`stage/`](stage/README.md) | Checks scoped to the artifact just authored |
| [`runtime_hygiene/`](runtime_hygiene/README.md) | Retained-graph and lifecycle probes |
