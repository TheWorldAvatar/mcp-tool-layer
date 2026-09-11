# validate/report/runtime_hygiene

Runtime probes against a generated MCP package: shared retained graph,
lifecycle adapters, and generic RDF mutation bypasses.

These checks are no-ops when `main.py` is absent (prompt-only runs).

## Files

| File | Role |
| --- | --- |
| `__init__.py` | Orchestrate the hygiene report |
| `accumulator.py` | Mutable failure / warning / obligation bag |
| `scan.py` | Static scan for generic `add_object_property` / `create_individual` bypasses |
| `lifecycle.py` | AST checks on `init_memory` / `export_memory` |
| `shared_graph.py` | Create + export must share the retained graph |
| `legacy_materialize.py` | Historical `materialize_hints` audit; kept for parity |
