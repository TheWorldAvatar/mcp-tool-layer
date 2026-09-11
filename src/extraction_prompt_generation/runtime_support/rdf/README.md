# runtime_support/rdf

Domain-independent RDF graph state used at extraction runtime.

Source lives as this package. Generated MCP scripts still receive one flat
`_fixed_rdf_runtime.py` from `flatten_rdf_runtime()` /
`write_fixed_rdf_runtime()`.

## Files

| File | Role |
| --- | --- |
| `__init__.py` | Public imports plus flatten / write |
| `constants.py` | Registry keys, sidecar anchors, tool-text limits |
| `registry.py` | Process-wide graph, scope, reuse-grant, rejection registries |
| `paths.py` | Scoped / central / document memory filenames |
| `lifecycle.py` | `init_memory` / `export_memory` and identity seeding |
| `reuse.py` | Central and document reuse memory load / publish |
| `capabilities.py` | T-Box-compiled relationship, entity, datatype, OM-2 writers |
| `envelopes.py` | Success / error JSON envelopes for generated tools |
