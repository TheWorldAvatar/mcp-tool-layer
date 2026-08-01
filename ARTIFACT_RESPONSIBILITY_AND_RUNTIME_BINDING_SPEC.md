# Artifact Responsibilities and Runtime Binding Specification

Status: normative design contract  
Scope: script generation, prompt generation, runtime injection, validation, repair, and semantic closed-loop evaluation



## 0. Core mechanisms

For prompt generation, I expect the following:

 - A immediate repair loop, where the validator checks whether the slots are correctly in place, whether the prompt generated is semantically about what it is expected to do, as well as 
 - After mock A-box generation and evaluation, according to the feedback, we improve the prompt content to increase the 

## 1. Purpose

This document defines what each generated prompt and script is responsible for, which information may be fixed generically, which information must be derived from the active T-Box or iteration blueprint, and which runtime content must be injected by pipeline code.

The central separation is:

1. **Generic infrastructure policy** defines domain-independent lifecycle, safety, data-channel, validation, and repair behavior.
2. **Active T-Box projection** supplies ontology classes, properties, comments, domains, ranges, cardinalities, creator/checker names, and ordered-member rules.
3. **Iteration blueprint projection** supplies the responsibility and input/output dependencies of the current iteration.
4. **Pipeline scripts** load concrete runtime data and bind it to named prompt slots.
5. **Generated prompts** tell the runtime model how to use those already-bound inputs; prompts do not load files, reinterpret mislabeled channels, or invent pipeline policy.
6. **Generated ontology scripts** expose only bounded, T-Box-derived capabilities.
7. **Fixed runtimes** implement only reusable domain-independent mechanics.



## 2. Non-negotiable red lines



### 2.1 No domain leakage in generic sources

Generic meta-prompts, generic generation contracts, fixed runtimes, and pipeline infrastructure must not contain:

- ontology-specific class or property names;
- ontology-specific IRIs or namespace assumptions;
- chemistry-, medical-, city-, or other domain examples;
- fixture entities, expected answers, source quotations, quantities, or benchmark values;
- hard-coded semantic triggers, exclusions, disambiguation rules, or cardinalities;
- ontology-name routing such as `if ontology == ...` to inject semantic knowledge;
- local-name fallbacks for properties such as a hard-coded ordering property;
- heuristic A-Box repair based on domain words.

A domain symbol is permitted in a generated runtime artifact only when its provenance is explicit:

- `active_tbox`;
- `tbox_derived_contract`;
- `iteration_blueprint`;
- or a separately configured external-enrichment profile whose field targets are validated against the active T-Box.



### 2.2 Fixed scripts remain domain-independent

Fixed runtimes may implement generic behavior such as:

- retained graph lifecycle;
- scoped persistence paths;
- A-Box-only export;
- deterministic IRI minting;
- normalized-label identity reuse;
- atomic ordered creation when given a T-Box-derived class and ordering predicate;
- bounded quantity parsing and unit aliases;
- structured success/rejection envelopes;
- graph snapshot and rollback.

They must not decide which domain class, property, relationship, quantity type, or extraction rule applies.

### 2.3 Prompt templates are not completed extractions

A generated Markdown prompt is a runtime template. It must preserve declared runtime slots and must not contain pre-populated A-Box facts. Semantic review must evaluate its instructions, scope, provenance, and bindings—not demand concrete runtime instances.

### 2.4 No hidden channel aliasing

One semantic input has one explicitly named slot. In particular:

- raw source text is not silently inserted into a hints slot;
- extraction hints are not inserted into `{paper_content}`;
- a prompt must not explain away a misleading slot name;
- unknown or unbound runtime slots are hard failures.

The historical pattern below is forbidden:

```text
hints_content -> script wraps hints -> replaces {paper_content}
```

The required pattern is:

```text
hints_content -> pipeline wraps hints -> replaces {iteration_hints}
```



## 3. Contract layers



### 3.1 Generic generation contract

This layer may be predefined. It contains only domain-independent rules:

- artifact role;
- lifecycle sequence;
- required runtime channel names;
- closed-world tool exposure;
- identity and retry semantics;
- final export/commit boundary;
- validation and repair transaction rules;
- format-independent semantic evaluation.

The same generic contract must be reusable byte-for-byte with unrelated ontologies.

### 3.2 Active T-Box projection

This layer is generated at run time from the active ontology. It may contain:

- exact class/property IRIs and local names;
- class comments and property comments;
- domains, ranges, datatypes, and cardinalities;
- top-entity role;
- ordered-member classes and ordering property;
- exact `create_*`, `add_*`, and `check_existing_*` names derived from validated manifests;
- T-Box-authorized quantity ranges;
- required links and typing rules.

Every domain-specific instruction in a generated prompt must be traceable to this projection.

### 3.3 Iteration blueprint projection

This layer defines the current iteration only:

- iteration number and name;
- semantic responsibility;
- per-entity or global scope;
- upstream data sources;
- output artifact paths;
- whether pre-extraction or enrichment is involved;
- configured external tools;
- sub-iteration dependencies.

An iteration prompt must not broaden into an ontology-wide task.

### 3.4 Pipeline-only runtime policy

This layer is controlled by scripts/configuration and is not serialized wholesale into LLM generation prompts:

- file paths;
- data directories;
- DOI/hash resolution;
- runtime scope names;
- MCP configuration;
- retry counts and timeout settings;
- persistence artifact discovery;
- environment variables;
- baseline/champion locations.

Only a minimal, artifact-specific projection may enter a prompt-generation context.

## 4. Runtime data channels



### 4.1 Common slots

- `{doi}`: document/run identifier supplied by the orchestrator.
- `{entity_label}`: current top-entity human-readable identity.
- `{entity_uri}`: current persisted top-entity IRI.

Prompts must not invent any of these values.

### 4.2 Extraction channels

- `{paper_content}`: source text selected by the extraction pipeline.
- `{iteration_input}`: content loaded from the current iteration's configured `inputs.file_path`, when present.

The extraction pipeline owns file loading and binding. An extraction prompt only declares how the injected source channels should be interpreted.

### 4.3 KG-building channels

- KG Iteration 1:
  - `{doi}`;
  - `{paper_content}` for contextual provenance only;
  - `{top_entities}` for upstream top-entity labels.
- KG Iteration 2 and later:
  - `{doi}`;
  - `{entity_label}`;
  - `{entity_uri}`;
  - `{iteration_hints}` as the authoritative extracted facts for this iteration.

KG Iteration 2+ must not require `{paper_content}`. Raw paper content is not an implicit KG-building input. If a future KG stage genuinely requires source evidence, it must declare a separate slot such as `{source_text}` and the pipeline must bind it explicitly.

### 4.4 Pipeline injection behavior

For KG Iteration 2+, the pipeline:

1. reads the iteration hints file;
2. merges explicitly configured enrichment patches;
3. wraps the resulting content with a generic `ExtractedHints` boundary;
4. replaces `{iteration_hints}`;
5. replaces `{doi}`, `{entity_label}`, and `{entity_uri}`;
6. appends generic runtime execution rules;
7. runs the agent;
8. applies the script-level final `export_memory` fallback when required.

The prompt must not load files, reconstruct paths, or reinterpret another slot as hints.

## 5. Prompt responsibilities



### 5.1 Requirements common to all extraction prompts

Extraction prompts must:

- consume pipeline-bound source channels;
- follow only the current iteration blueprint;
- apply relevant active-T-Box comments as binding semantic rules;
- remain source-grounded;
- preserve ambiguity rather than invent facts;
- permit any unambiguous output representation;
- avoid fixture-specific examples and expected answers.

Extraction prompts must not:

- impose canonical JSON as a semantic hard gate;
- materialize RDF or call KG mutation tools;
- perform cross-iteration responsibilities;
- insert A-Box facts before runtime inputs are bound.



### 5.2 Requirements common to all KG-building prompts

KG-building prompts must:

1. always call `init_memory` first; it is idempotent open-or-resume;
2. use current persisted identity and current iteration hints;
3. call exact T-Box-derived checks/creators/relationship tools;
4. use `check_existing_<Class>()` before creation when cross-iteration reuse is possible;
5. interpret existing-entity checks as zero-argument inventories containing `{iri, labels}`;
6. match hinted labels against returned labels after generic normalization;
7. create only when no valid existing identity is found;
8. assert only T-Box-compatible links supported by hints;
9. tolerate corrected intermediate rejections;
10. call `export_memory` as the final tool action.

The successful final export is the attempt's commit boundary. An earlier rejected call does not invalidate the attempt if the agent corrects it and exports successfully.

KG-building prompts must not:

- re-extract facts from raw source text;
- invent entities to satisfy cardinality or required-link constraints;
- create placeholders for missing targets;
- guess class/property/tool names;
- use generic triple writers;
- hard-code a runtime scope value;
- continue mutating after `export_memory`.



### 5.3 KG Iteration 1

Generic objective:

- materialize or reuse top-entity A-Box roots from upstream labels;
- create no downstream targets or relationships;
- take labels from `{top_entities}`;
- take the root class and exact creator from the active T-Box projection;
- accept JSON labels and generic wrapper lines `<type-prefix>-<index> [<label>]`;
- use only the bracketed payload as the label;
- normalize and deduplicate labels before creator calls;
- use orchestrator-provided scope;
- export last.

The generic contract must not contain the concrete top class. The generated runtime prompt must render the exact T-Box-derived creator tool as a concrete callable name; symbolic references such as `tbox_scope.top_entity.creator_tool` are forbidden at runtime.

### 5.4 KG Iteration 2 and later

Generic objective:

- materialize only `{iteration_hints}` for the current entity and iteration;
- do not re-run extraction;
- resume existing A-Box state;
- discover/reuse persisted entities;
- apply iteration-specific T-Box creators, scalar fields, and links;
- export last.

Iteration-specific classes, cardinalities, exclusions, scalar fields, and links must come from the current T-Box/iteration projection.

For the current Iteration 2, the blueprint says the extraction responsibility is inputs/outputs for one top entity. The generated KG prompt may therefore mention the concrete T-Box-derived classes, properties, cardinalities, and tools for that scope, but the generic meta-contract must not contain those symbols.

### 5.5 KG Iteration 3

The iteration-specific projection owns ordered members and their enrichment:

- use the exact T-Box-derived specific ordered-member subclass;
- pass the positive integer order atomically to the creator;
- preserve one member per source operation;
- link each member individually to its parent;
- use exact checks and relationship tools from the generated surface;
- never guess or repair missing order values downstream.



### 5.6 KG Iteration 4

The iteration-specific projection owns the final scoped fact category declared by the blueprint. It must:

- materialize only current hints;
- enforce relevant T-Box cardinality and non-derivation rules;
- avoid inheriting or calculating absent values;
- preserve exact source-supported labels where required;
- export last.



## 6. Generated script responsibilities



### 6.1 `*_creation_base.py`

- Minimal package-relative adapter to fixed RDF runtime.
- No domain IRIs, graph logic, wrappers, creators, or relationship tools.



### 6.2 `*_creation_entities.py`

- One exact `create_<class_local>` per owned T-Box class.
- Unordered creator signature: `(label: str)`.
- Ordered creator signature: `(label: str, order: int)`.
- Ordered identity and order are written atomically.
- No caller-selected class except a bounded quantity class explicitly authorized by T-Box ranges.



### 6.3 `*_creation_relationships.py`

- One explicit `add_<predicate_local>` per owned object property.
- Exact T-Box predicate binding.
- Explicit range-aware parameter schema.
- No generic predicate/triple writer.
- No separate ordering setter when atomic ordered creators own order.



### 6.4 `*_creation_checks.py`

- Read-only `check_ordered_members`.
- Zero-argument `check_existing_<Class>` inventory for each authorized class.
- Returns existing IRIs and labels.
- No mutation, repair, reordering, or caller-selected graph query.
- Literal closed-world `__all__`.



### 6.5 `main.py`

- Real FastMCP server.
- Registry equals the manifest-derived allowlist exactly.
- Public lifecycle:
  - `init_memory(doi, top_level_entity_name)`;
  - `export_memory(doi, top_level_entity_name)`;
  - `materialize_hints(doi, top_level_entity_name, entity_label, hints_json)`.
- `init_memory` is idempotent open-or-resume and has no reset/mode surface.
- `export_memory` persists A-Box only.
- `materialize_hints` adapts hints through bounded public tools; it must not bypass class/property capabilities.



## 7. Pipeline script responsibilities

Pipeline scripts own:

- selecting source and hint files;
- reading and merging runtime data;
- replacing declared prompt slots;
- resolving DOI, entity identity, and runtime scope;
- starting the MCP session;
- injecting generic execution rules;
- retaining full tool traces;
- applying final export fallback;
- deciding retry from the final commit boundary;
- writing intermediate/final artifacts;
- invoking reasoner and scorers.

Pipeline scripts must not:

- add domain facts through lexical heuristics;
- create placeholder A-Box nodes;
- infer missing T-Box symbols by namespace/local-name conventions;
- silently repair semantic content after publish;
- serialize secrets into MCP configuration.



## 8. Validation and real-time repair



### 8.1 Pre-freeze transaction

Every artifact stage follows:

1. generate candidate;
2. run artifact-specific mechanical hard gates;
3. run runtime binding and generation-residue gates;
4. run behavior probes where applicable;
5. run independent semantic review;
6. if any gate fails, feed focused evidence into repair on the same candidate;
7. re-run all gates after each accepted edit;
8. freeze only when all hard gates pass and semantic decision is `pass`.

Manual post-freeze discovery of a missing gate indicates a validator defect. The gate must be moved into the pre-freeze transaction.

### 8.2 Repair behavior

Repair must:

- preserve the current candidate;
- target only the active artifact;
- receive bounded failure evidence;
- use artifact-type-specific skills/context;
- exclude Python runtime/import guidance when repairing Markdown;
- reject edits that introduce protected regressions;
- re-run semantic review after mechanical success.

Repair falls back to regeneration only when:

- attempts are exhausted;
- the candidate is structurally unrecoverable;
- or repair would require changing frozen upstream contracts.



### 8.3 Required prompt hard gates

- required runtime slots present;
- unknown runtime slots absent;
- no generation-time residue such as `tbox_scope...` or symbolic tool placeholders;
- exact T-Box-derived tool names rendered when runtime needs concrete callables;
- iteration scope matches blueprint;
- no canonical-format semantic gate;
- no fixture facts;
- every domain rule has T-Box provenance;
- lifecycle/check/create/link/export sequence is executable.



## 9. Evaluation and prompt enhancement loop

The closed loop is:

1. freeze generated scripts/prompts;
2. run mock A-Box baseline;
3. apply script/runtime/HermiT hard gates;
4. collect extraction and A-Box LLM soft-judge feedback;
5. route each issue to the responsible extraction prompt, KG prompt, or script layer;
6. generate an enhanced prompt candidate;
7. run the full extraction-to-KG pipeline again;
8. accept only when hard gates remain green and semantic quality improves;
9. otherwise retain/restore the previous champion.

Deterministic F1 is diagnostic, not a format hard gate. Detailed judge feedback, not only scalar scores, must enter diagnosis. Optimization and acceptance fixtures should be separated when possible to reduce overfitting.

## 10. Acceptance checklist

Before freezing any artifact:

- [ ] Generic contract contains no domain symbol or rule.
- [ ] Domain instructions trace to active T-Box or iteration blueprint.
- [ ] Runtime slots match actual pipeline replacements.
- [ ] Pipeline—not prompt—loads and injects data.
- [ ] Prompt scope equals current iteration scope.
- [ ] No source/hints channel aliasing.
- [ ] No fixture facts or canonical serialization requirement.
- [ ] Exact runtime tool names are available where required.
- [ ] Lifecycle begins with idempotent init/resume.
- [ ] Existing identities are checked and reused where applicable.
- [ ] Intermediate rejections may recover.
- [ ] Final action is successful A-Box export.
- [ ] Mechanical, runtime, semantic, and provenance gates all pass.
- [ ] Failed validation triggers repair on the same candidate before regeneration.