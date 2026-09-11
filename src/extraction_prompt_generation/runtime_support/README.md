# runtime_support

Human-maintained RDF and OM-2 libraries copied into each generated script
package. Prompt generation does not author these files; it flattens them.

`pipeline.runtime_support.generate_runtime_support_slice` writes
`_fixed_rdf_runtime.py`, `_fixed_om2_runtime.py`, and `_namespace.json`
next to generated scripts.

## Files

| File | Role |
| --- | --- |
| `om2.py` | Shared OM-2 quantity / unit helpers used by source-side tests |
| `fixed_om2_runtime.py` | Official OM-2 helpers copied into generated `_fixed_om2_runtime.py` |
| [`rdf/`](rdf/README.md) | Graph state, reuse memory, compiled writers |
