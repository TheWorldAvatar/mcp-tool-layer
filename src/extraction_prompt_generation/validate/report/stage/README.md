# validate/report/stage

Validate obligations owned by the *most recently generated* artifact, not
the whole package. Authoring uses this after each exact-edits publish.

`_stage_artifact_contract_report` dispatches on filename suffix.

## Files

| File | Role |
| --- | --- |
| `context.py` | Mutable `StageProbe` for one artifact |
| `load.py` | Resolve, import, and open/close the retained-graph session |
| `prompt_md.py` | Prompt-file stage checks (slots, emptiness) |
| `main.py` | `main.py` lifecycle adapters; forbid `materialize_hints` |
| `base.py` | Shared `_creation_base.py` adapter must stay domain-free |
| `entities.py` | Entity-creator surface, OM-2, range labels |
| `relationships.py` | `add_*` writers and source shape |
| `creators.py` | Shared create-tool runtime probes |
| `checks.py` | Existing-entity lookup and ordered-member integrity |
