# planning

Decide *what* each iteration owns. The configured model (default `gpt-5`)
chooses only the top class (main) or extension focus. Slot ownership is
deterministic.

## Files

| File | Role |
| --- | --- |
| `semantic_planner.py` | Top-entity / extension-focus planning, with attempt audits |
| `iteration_assigner.py` | Assign primary-T-Box classes and object properties to profile slots |

`plan_domain_semantics` is the public entry. It writes attempt JSON under
`generated/semantic_planning/<ontology>/`.

Ownership rules (complex profile):

- foundation — top-entity ranges and non-reusable ordered-reference bridges
- ordered — ordered-member classes and operation-local assets
- remainder — everything else that still has a creator surface

The simple profile puts the whole downstream surface on one slot.
