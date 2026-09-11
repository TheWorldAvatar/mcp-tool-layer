# Config and artifact catalog

Every path below is relative to the repository root. **Human-written** files
are tracked. **Generated** files are produced by the configured model (default `gpt-5`) or by a compiler
script and must stay under `generated/runs/<batch>/` (gitignored). The
legacy flat `generated/` tree is only a fallback.

Do not put class lists, property lists, SPARQL strings, or prompt bodies in
a domain config. Those are planned or compiled.

## Human-written inputs

### Namespace config — `configs/namespace.json`

Human-written minting IRIs. The compiler copies them into
`generated/runs/<batch>/scripts/<ontology>/_namespace.json` so flattened runtimes do not
hardcode a host IRI.

| Field | Author | Meaning |
| --- | --- | --- |
| `schema_version` | Human | Must be `namespace-config.v1`. |
| `instance_base_iri` | Human | Prefix for minted A-Box individuals. |
| `generated_graph_iri` | Human | Fallback graph / namespace IRI when the T-Box does not supply one. |

The compile-time sidecar may also carry `main_ontology_name`, derived from the
domain adapter (`ontologies.main.name`), so extension memory paths can stay
separate from the parent package. Runtime `--test` MCP launchers copy that same
pipeline-owned name into `TWA_MAIN_ONTOLOGY_NAME`; they must not set the env to
the extension package name.

### Domain configs — `configs/domains/*.json`

Schema: `domain-generation-config.v1`. One file per ontology. This is the
only non-T-Box generation input besides MCP sets.

| Field | Author | Meaning |
| --- | --- | --- |
| `schema_version` | Human | Must be `domain-generation-config.v1`. |
| `domain_id` | Human | Stable domain key. |
| `ontology_name` | Human | CLI positional name. Default config is `configs/domains/<ontology_name>.json`. For OntoMOPs / OntoSpecies this is the extension name; the parent ontology is `runtime.binding.upstream_ontology`. |
| `execution_profile` | Human | `complex_main` (OntoSyn), `simple_main` (medical), or `simple_extension` (OntoMOPs / OntoSpecies). |
| `workflow_profile` | Human | `complex` (OntoSyn iters 2/3/4), `simple` (medical JSON `ref-entity-relations.v1`), or `simple_semantic` (extensions). `simple_extension` is locked to `simple_semantic`; the loader rejects `simple`. Slot shapes live in `src/extraction_prompt_generation/config/domain_config.py` (`WORKFLOW_PROFILES`). |
| `tbox.primary` | Human | Primary T-Box path. |
| `tbox.supporting` | Human | Supporting T-Boxes used for planning / foreign classes. Empty for medical. |
| `models.*` | Human | Model names. Default is `gpt-5` when a key is omitted. Planning, `reuse_judgment`, `prompt_generation`, `semantic_review`, and `repair` may name other models. `models.runtime_extraction` is the extraction-step model; runtime `tbox_slim` uses that same name after PDF→MD. Runtime keys are for later extraction, not this generator. |
| `reuse_policy.path` | Optional | Debug-only copy of a review file. Default generation omits this and runs 10/10 GPT-5 trials. |
| `mcp_capabilities.kg_building` | Human | MCP set filename + tool names used when compiling KG scripts / prompt contracts. |
| `mcp_capabilities.external_enrichment` | Human | External tools (PubChem / CCDC / websearch) injected into `*.materializable.inc` when `use_agent` is true. |
| `mcp_capabilities.extensions` | Human | OntoSyn-only declaration of extension MCP tools. |
| `runtime.scenario_domain` | Human | Run-folder family under `scenarios/<name>/`. Defaults to `domain_id` if omitted. |
| `runtime.vision_required` | Human | Default PDF conversion mode. `true` copies vision transcripts. |
| `runtime.extra_steps` | Human | Extra pipeline steps appended after the execution-profile list, e.g. `mop_derivation`. |
| `runtime.derivation.agents` | Human | Python modules run after published TTL exists. Empty means the derivation step is a no-op. |
| `runtime.output` | Human | Runtime TTL directory and filename patterns. |
| `runtime.external_identity_bindings` | Human | OntoSyn: lock `{doi}` onto Document / BibliographicResource in iteration 1. |
| `runtime.extensions[]` | Human | OntoSyn-only extension wiring: `name`, `bridge_class_iri`, `ttl_file`, MCP list, output patterns. `ttl_file` must also appear in `tbox.supporting`. |
| `runtime.extensions[].bridge_class_iri` | Human | The only allowed non-T-Box class fact for an extension. The child MCP compiler treats this class (together with planned `extension_focus`) as the adopted parent: `create_*` for that class reuses the SPARQL-seeded IRI, and unique incoming children still get public `create_*` even when reusable. |
| `runtime.enrichment_target` | Human | Extension-only hop list. **Do not** put `query_file` or a SPARQL string here. |
| `runtime.enrichment_target` | Human | Extension-only hop list. **Do not** put `query_file` or a SPARQL string here. |
| `runtime.enrichment_target.path` | Human | Property local names, e.g. `["hasChemicalOutput", "isRepresentedBy"]`. |
| `runtime.enrichment_target.root_variable` | Human | Bound to the current parent-entity IRI (compiler default `root`). |
| `runtime.enrichment_target.target_variable` | Human | Selected bridge individual. |
| `runtime.enrichment_target.cardinality` | Human | `exactly_one`. |
| `runtime.binding.role` | Human | `main` or `extension`. |
| `runtime.binding.upstream_ontology` | Human | Extension only: parent ontology name. Any name is allowed; the generator does not hardcode OntoSyn. |
| `runtime.binding.upstream_tbox` | Human | Extension only: parent T-Box path. Must also appear in `tbox.supporting` and must match that parent's `tbox.primary`. |
| `runtime.workflow.pipeline_iteration_number` | Human | Extension only: pipeline iteration index (OntoMOPs `1`, OntoSpecies `2`). |
| `runtime.workflow.iterations[]` | Human | One object per workflow-profile slot. Count must match the profile. |
| `runtime.workflow.iterations[].use_agent` | Human | Whether that slot may call external MCP tools. |
| `runtime.workflow.iterations[].hint_representation` | Human | Must match the profile: `semantic-text.v1` (`complex` and `simple_semantic`) or `ref-entity-relations.v1` (`simple`). Extension slots are locked to `semantic-text.v1`. |
| `runtime.workflow.iterations[].inputs.source` | Human | Runtime paper body. The historical name is `stitched_paper`; the loader now reads `{hash}_slim.md` or conversion markdown. |
| `runtime.workflow.iterations[].enrichment` | Human | Must be `[]` unless the profile declares enrichment slots. |
| `runtime.workflow.iterations[].pre_extraction_validation.closed_ledger` | Human | Required on the complex ordered slot (iter3). |
| `agents.*` | Human | Which runtime agents exist. Generation reads this as wiring, not as a class list. |

Forbidden in domain configs (compiler rejects them): `classes`, `properties`,
`responsibilities`, `top_entity`, `required_links`, `query_file`, prompt
allowlists, `execution_channel`, `upstream_scope`, and other leftover or
semantic inventories. Class reuse is generated from T-Box + compiled plan
(10 independent GPT-5 trials, unanimous gate). `reuse_policy.path` is an
optional debug override, not a required human input. The inherited parent
top entity is planned, not configured.

### T-Boxes — `data/ontologies/`

| File | Author | Used by |
| --- | --- | --- |
| `ontosynthesis.ttl` | Human | OntoSyn primary; extension supporting |
| `ontomops-subgraph.ttl` | Human | OntoMOPs primary; OntoSyn supporting |
| `ontospecies-subgraph.ttl` | Human | OntoSpecies primary; OntoSyn supporting |
| `om2.ttl` | Human | OntoSyn supporting (units) |
| `medical_case_schema_de_non_flat_v4.ttl` | Human | Medical primary |

These are the semantic authority. Change comments here, then re-run
`--stage prompts` to refresh `*.materializable.inc`.

### MCP sets — `configs/mcp/`

Filenames are referenced by `mcp_capabilities.*.set_name`. The loader looks
in `configs/mcp/` first.

| File | Author | Role |
| --- | --- | --- |
| `chemistry.json` | Human | OntoSyn iter2/3 external tools. `_mcp_set_policy.tool_purposes` is copied into materializable companions. `_mcp_set_policy.extraction_validation` is compiled into iteration contracts. |
| `run_created_mcp.json` | Human | OntoSyn KG `llm_created_mcp` launch stub. Points at `generated.scripts.ontosynthesis.main`. |
| `extension.json` | Human | Extension MCP launch stubs + CCDC purpose text. |
| `ontology_mcps.json` | Human | Medical `medical_mcp` launch stub. |

`_mcp_set_policy` is generation-facing and should be edited by a person.
`command` / `args` / `env` are launch wiring for a later extraction runtime.
They are human-maintained stubs in this repo (portable `python -m` form).
Machine-local interpreter paths belong in an untracked overlay, not in git.

Class reuse is generated during `--stage context` / `--stage reuse` from
the primary T-Box, supporting T-Boxes, domain config (as cross-T-Box
context), and the compiled iteration plan. The compiler runs ten
independent GPT-5 trials with
`configs/meta_task/gpt5_single_tbox_binary_reusability_prompt.md` and
writes `derived_inputs/<ontology>/reuse_policy.json` only when the 10/10
gate passes. Isolated reuse regeneration: `--stage reuse`. An optional
`reuse_policy.path` copies a file instead of calling the model.

## Generated outputs (`generated/runs/<batch>/`, untracked)

`--stage all` writes one ontology slice into one batch package:

`generated/runs/<YYYYMMDD_HHMMSS>_<tag>/`

`--tag` reuses that unique run or mints a new one. `generated/current.json`
points at the active batch. `batch.json` records model, tag, and ontologies.
`--output-root` pins an exact directory. The old flat `generated/prompts/`
tree is `legacy` only; do not mix two models or two retries in one package.

| Path | Author | Notes |
| --- | --- | --- |
| `derived_inputs/<ontology>/meta_task_adapter.json` | Compiler | Internal adapter used while compiling. Do not edit. |
| `derived_inputs/<ontology>/iteration_blueprint.json` | Compiler | Ownership + runtime wiring after GPT-5 planning. |
| `derived_inputs/<ontology>/reuse_policy.json` | GPT-5 10/10 | Assembled from a representative valid trial after the unanimous gate. |
| `derived_inputs/<ontology>/reuse_judgment.json` | GPT-5 | Representative trial plus experiment summary pointer. |
| `derived_inputs/<ontology>/reuse_trials/` | GPT-5 | Independent trial artifacts, compiled plan projection, and `summary.json`. |
| `semantic_planning/<ontology>/` | GPT-5 + compiler | Planning attempts and accepted decisions. |
| `ontology_structures/<ontology>/parsed.json` | Compiler | Parsed T-Box. |
| `ontology_structures/<ontology>/parsed.md` | Compiler | Human-readable T-Box dump. |
| `ontology_structures/<ontology>/generation_contract.json` | Compiler | Full generation contract. |
| `ontology_structures/<ontology>/integrity_profile.json` | Compiler | Ordered-member profile. |
| `ontology_structures/<ontology>/config_provenance.json` | Compiler | Input hashes and boundary notes. |
| `prompts/<ontology>/*.md` | GPT-5 | Extraction and pre-extraction prompts only. |
| `prompts/<ontology>/*.materializable.inc` | Compiler | Owned classes/properties, T-Box comments, ledger rules, tool purposes. Regeneration overwrites hand edits. |
| `sparqls/<ontology>/top_entity_parsing.sparql` | Compiler | Fixed SELECT for the planned top class IRI. |
| `sparqls/<ontology>/enrichment_target.sparql` | Compiler | Extension only. Built from `runtime.enrichment_target.path`. |
| `iterations/<ontology>/iterations.json` | Compiler | Runtime iteration spec. |
| `scripts/<ontology>/_namespace.json` | Compiler | Copy of `configs/namespace.json` plus `main_ontology_name`. |
| `reports/<ontology>/generation_report.json` | Generator | Pass/fail and artifact history. |
| `reports/summary.json` | Generator | Multi-ontology summary when several reports exist. |
| `reports/llm_invocations.jsonl` | Generator | LLM call journal (no prompts or keys). |

## What is not an input

- Old `meta_task_config.json` plus a hand-written class-list blueprint
- `occurrence_surface` / inferred-atomic operation modes
- Unified-diff editing
- Prompt-enhancement / repair-only loops
- KG-building prompts and ONEPASS fragments
- SPARQL snapshots checked into `configs/sparql/`
- Official indep10 / pipeline eval configs

## Domain cheat sheet

**OntoSynthesis** — human: `configs/domains/ontosynthesis.json`, four T-Boxes,
`chemistry.json`, `run_created_mcp.json`, `extension.json`.

**Extensions** — human: `configs/domains/ontomops.json` /
`ontospecies.json`, own subgraph T-Box + OntoSyn supporting T-Box,
`extension.json`, hop list in `runtime.enrichment_target`. The inherited
root class comes from the parent domain's GPT-5 top-entity decision in the
same generation batch; generate OntoSyn `--stage context` first. Reuse is
judged from the extension primary T-Box during context generation. MCP
generation does not special-case those ontology names: `execution_profile=
simple_extension` (locked to `workflow_profile=simple_semantic`;
`EXTRACTION_ITER_1` is `SEMANTIC_HINTS_V1`, not main identity JSON), planned
`extension_focus`, and the parent's
`runtime.extensions[].bridge_class_iri` decide which class is adopted and
which children stay public.

**Medical** — human: `configs/domains/medical.json`,
`data/ontologies/medical_case_schema_de_non_flat_v4.ttl`,
`ontology_mcps.json`. No enrichment_target. Reuse is judged from the
medical T-Box during context generation.
