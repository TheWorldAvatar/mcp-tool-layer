# extraction_prompts/contracts

Frozen prompt-authoring contracts and mechanical gates. The configured
model authors `EXTRACTION` / `PRE_EXTRACTION` markdown against these objects.

Do not rewrite the English contract strings. MCP script contracts do not
live here. `generation_contract.py` must not import `validate.report`
(that would create a cycle: authoring → contracts → validate → contracts).

`__init__.py` re-exports the public helpers so callers can keep importing
from `...extraction_prompts.contracts`.

## Files

| File | Role |
| --- | --- |
| `generation_contract.py` | Per-artifact contract: iteration spec, T-Box slice, slots, sub-contracts |
| `role_and_guidance.py` | Prompt role, generation guidance, semantic-ledger rules |
| `sub_contracts.py` | Lexical quantity, PRE types, iter1 top-entity, subclass checklist |
| `tbox_slice.py` | Iteration-owned class/property slice plus warning/comment projections |
| `scope.py` | Resolve the iteration spec and owned semantic scope for one file |
| `materializable.py` | Render and splice `*.materializable.inc` / deterministic T-Box blocks |
| `markers.py` | Begin/end markers for mechanically spliced T-Box text |
| `gates.py` | Hard mechanical gates (placeholders, ledger headings, injected slots) |
| `repair.py` | Validate → semantic review → exact-edits repair loop |
