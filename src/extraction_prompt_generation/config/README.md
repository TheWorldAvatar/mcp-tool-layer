# config

Load the only non-T-Box, human-maintained generation input:
`configs/domains/<ontology>.json`.

Class lists, property lists, SPARQL strings, and prompt bodies are forbidden
here. `derivation.py` rejects those leftover semantic inventories.

## Files

| File | Role |
| --- | --- |
| `domain_config.py` | Schema `domain-generation-config.v1`, `WORKFLOW_PROFILES`, and the loader |
| `derivation.py` | Allowlist / denylist checks; strip leftover semantic fields |
| `namespace.py` | Load `configs/namespace.json` and write the generated `_namespace.json` sidecar |

`WORKFLOW_PROFILES` is the source of slot shapes:

- `simple` — one downstream slot (`iter2`), JSON `ref-entity-relations.v1`
- `simple_semantic` — same slot shape, `SEMANTIC_HINTS_V1` like OntoSyn 2/3/4
- `complex` — foundation / ordered / remainder (`iter2` / `iter3` / `iter4`)

`simple_extension` (OntoMOPs / OntoSpecies) is locked to `simple_semantic`.
The loader rejects `workflow_profile=simple` for those domains. Medical
`simple_main` keeps `simple`.

Execution profiles (`complex_main`, `simple_main`, `simple_extension`) live
on the domain JSON and select planning keys, not slot geometry.

`models.*` names are free. Missing planning / reuse-judgment / prompt-
generation keys default to `gpt-5`.
