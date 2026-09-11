# compile

Turn T-Box + domain config into the generation context that later stages
consume. Almost everything here is deterministic. Class reuse is generated
from T-Box + compiled plan by ten independent GPT-5 trials
(`reuse_judgment.py` → `tbox_reusability_experiment.py`).

`artifact_compiler.build_domain_generation_context` is the public compile
entry. The CLI and `pipeline.experiment` call it before any prompt authoring.

## Files

| File | Role |
| --- | --- |
| `artifact_compiler.py` | Orchestrate planning, reuse, iteration compile, and write derived inputs |
| `context.py` | `AgenticGenerationContext` plus the lower-level context builder |
| `generation_contracts.py` | Facade over contract bundle / publish / observation helpers |
| `contract_bundle.py` | Assemble the generation contract from T-Box + runtime policy |
| `contract_publish.py` | Publish-contract projection of classes, properties, and creators |
| `contract_rdf.py` | rdflib helpers (local names, lists, restrictions, subclass closure) |
| `contract_observations.py` | Stable machine-routable validation observation records |
| `iteration_plan.py` | Bind scheduling intent to T-Box symbols and property closures |
| `operation_units.py` | Atomic materialization operations from creator / edge contracts |
| `materialization_closure.py` | Evidence-driven “how this fact may be materialized” obligations |
| `reuse_judgment.py` | Assemble a reuse policy after 10 independent GPT-5 trials. Fail closed if the unanimous binary+scope gate fails. |
| `reuse_judgment_prompt.md` | Official review prompt (must match `configs/meta_task/gpt5_single_tbox_binary_reusability_prompt.md`) |
| `tbox_reusability_experiment.py` | Independent-trial runner used by `generate_reuse_policy` |
| `reuse_policy.py` | Normalize, attach, and project reuse policy onto lookup tools |
| `reuse_pair_judge.py` | Runtime pair-identity judge used by generated RDF reuse, not by prompt compile |
| `enrichment_sparql.py` | Compile extension `enrichment_target.sparql` from a hop list |
| `extension_bridge.py` | Collect the human-declared extension bridge class IRIs |
| `extension_prompt.py` | Allowed / forbidden runtime slots for extension extraction prompts |

## What this package writes

Under `generated/`:

- `derived_inputs/<ontology>/reuse_policy.json`
- `derived_inputs/<ontology>/reuse_trials/`
- `ontology_structures/<ontology>/generation_contract.json`
- `iterations/<ontology>/iterations.json` (via pipeline runtime-support)
- `sparqls/<ontology>/` (top-entity or enrichment queries)
