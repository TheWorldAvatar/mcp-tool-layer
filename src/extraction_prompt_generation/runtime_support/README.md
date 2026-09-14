# runtime_support

Human-maintained RDF library plus a compiled OM-2 runtime, copied into each
generated script package. Prompt generation does not author parser logic; it
flattens it.

`pipeline.runtime_support.generate_runtime_support_slice` writes
`_fixed_rdf_runtime.py`, `_fixed_om2_runtime.py`, and `_namespace.json`
next to generated scripts.

## Files

| File | Role |
| --- | --- |
| `om2.py` | Live compact matcher for source tests and OX. Loads tables from the v2 quantity surface. Not copied into packages. |
| `fixed_om2_runtime.py` | Deprecated import alias of `om2.py`. Not copied into packages. |
| [`rdf/`](rdf/README.md) | Graph state, reuse memory, compiled writers |

The v2 MCP generator compiles unit aliases (`om2_unit_aliases.json`) and
unit→quantity-class membership (`om2.ttl`) in `overlay/quantity_surface.py`, then
inlines them into the generic compact matcher (`overlay/om2_compact.py`) as
`_fixed_om2_runtime.py`. That file does not copy `om2.py`. Labels must be
compact `<number> <unit>`. A TemperatureRate unit on a Duration node is rejected.
