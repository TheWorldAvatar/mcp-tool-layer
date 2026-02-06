## Pre-existing artifact inventory (generation pipeline)

This document inventories **the code + meta-prompts used to generate MCP scripts** under `ai_generated_contents_candidate/scripts/<ontology>/...` (and related generation-time utilities).

### What this inventory covers

- **MCP script generation (direct mode)**: produces multi-file MCP servers (`*_creation_base.py`, `*_creation_checks.py`, `*_creation_entities_*.py`, `*_creation_relationships.py`, and `main.py`).
- **MCP script generation (agent mode / legacy)**: produces monolithic `*_creation.py` and a `main.py` wrapper (kept for backward compatibility / experimentation).
- **Shared template utility**: `sandbox/code/universal_utils.py`, copied into the candidate tree as `ai_generated_contents_candidate/scripts/universal_utils.py`.
- **Meta-prompts** used by the script generators (stored under `ape_generated_contents/meta_prompts/mcp_scripts/`).

### What this inventory intentionally does NOT cover

- **Ontology coverage** (“every class/property has a tool”) — tracked elsewhere.
- **Prompt text quality checks** — tracked elsewhere.
- **Extraction/KG-building prompt generation meta-prompts** (those live under `src/agents/scripts_and_prompts_generation/*` but are not MCP *script* generation).

---

## 1) Orchestrator (entrypoint)

- **Entrypoint**: `src/agents/scripts_and_prompts_generation/generation_main.py`
  - **Role**: orchestrates generation steps (parse TTL → iterations → scripts → prompts → MCP config).
  - **Writes MCP scripts to**: `ai_generated_contents_candidate/scripts/<ontology>/...`
  - **Ensures utility copied**:
    - **Source**: `sandbox/code/universal_utils.py`
    - **Target**: `ai_generated_contents_candidate/scripts/universal_utils.py`
    - **Function**: `ensure_universal_utils()` in `generation_main.py`

---

## 2) Direct MCP script generator (multi-file, divide-and-conquer)

- **Generator code**: `src/agents/scripts_and_prompts_generation/direct_script_generation.py`
  - **Role**: direct (non-agent) LLM calls that generate smaller pieces first and then stitch/wrap them.
  - **Outputs** (per ontology, under `ai_generated_contents_candidate/scripts/<ontology>/`):
    - `{ontology}_creation_base.py`
    - `{ontology}_creation_checks.py`
    - `{ontology}_creation_relationships.py`
    - `{ontology}_creation_entities_<N>.py` (split into multiple parts)
    - `main.py` (FastMCP server wrapper exporting tools)
    - Optional split-main intermediates (when enabled):
      - `main_part_core.py`, `main_part_relationships.py`, and stitch artifacts

### 2.1 Meta-prompts used by `direct_script_generation.py` (MCP scripts)

All stored in `ape_generated_contents/meta_prompts/mcp_scripts/`:

- **`direct_underlying_script_prompt.md`**
  - **Used by**: direct underlying-script generation (legacy/monolithic path).
- **`direct_main_script_prompt.md`**
  - **Used by**: main MCP wrapper generation (`main.py`).
- **`direct_main_stitch_prompt.md`**
  - **Used by**: stitching split-main parts into final `main.py`.
- **`direct_main_part_core_fragment_prompt.md`**
  - **Used by**: generating `main_part_core.py` in split-main mode.
- **`direct_main_part_relationships_fragment_prompt.md`**
  - **Used by**: generating `main_part_relationships.py` in split-main mode.
- **`direct_base_script_prompt.md`**
  - **Used by**: generating `{ontology}_creation_base.py`.
- **`direct_entities_script_prompt.md`**
  - **Used by**: generating entity create-tools (`{ontology}_creation_entities_<N>.py`) and/or grouped entity scripts.
- **`direct_relationships_script_prompt.md`**
  - **Used by**: generating `{ontology}_creation_relationships.py`.

Other meta-prompts in the same folder exist (historical/experimental) but are not required for the current direct pipeline:
- `AUTO_CREATE_PATTERN.md`
- `direct_main_core_part_prompt.md`
- `direct_main_relationships_part_prompt.md`
- `full_script_prompt.md`
- `main_script_prompt.md`
- `step_by_step_prompt.md`

### 2.2 “Blurred mock” examples injected into direct prompts (pre-written, domain-agnostic)

These are **prepared in advance** and optionally appended to LLM prompts to stabilize code structure:

- **Entity creation example**: `src/agents/scripts_and_prompts_generation/mock_examples/entity_creation_blurred_example.py`
  - **Injected into**:
    - entity-part generation (`generate_entity_part_script(...)`)
    - entity-group prompt builder (`build_entity_group_prompt(...)`)
- **Relationship example**: `src/agents/scripts_and_prompts_generation/mock_examples/relationships_blurred_example.py`
  - **Injected into**:
    - relationships generation (`generate_relationships_script_direct(...)`)

These examples intentionally use **placeholder names** and must not introduce domain terms.

---

## 3) Shared generation-time utility template (copied into candidate outputs)

- **Template source**: `sandbox/code/universal_utils.py`
  - **Role**: domain-agnostic utilities for:
    - TTL-backed storage + file locking (`locked_graph`)
    - global state reading (`_read_global_state`)
    - IRI minting (`_mint_hash_iri`)
    - common graph helpers (`_find_by_type_and_label`, `_set_single_label`, etc.)
- **Copied output**: `ai_generated_contents_candidate/scripts/universal_utils.py`
  - **Copied by**: `ensure_universal_utils()` in `generation_main.py`

---

## 4) Agent-based MCP generators (legacy / alternate path)

These modules generate MCP scripts using the project’s agent framework (they still rely on the same meta-prompt folder).

- **Underlying script agent**: `src/agents/scripts_and_prompts_generation/mcp_underlying_script_creation_agent.py`
  - **Loads design principles**:
    - `src/agents/mops/prompts/universal_mcp_underlying_script_design_principles.md`
  - **Loads meta-prompts from**: `ape_generated_contents/meta_prompts/mcp_scripts/`
  - **Typical output**:
    - `ai_generated_contents_candidate/scripts/<ontology>/<ontology>_creation.py` (monolithic legacy)

- **Main script agent**: `src/agents/scripts_and_prompts_generation/mcp_main_script_creation_agent.py`
  - **Loads meta-prompts from**: `ape_generated_contents/meta_prompts/mcp_scripts/`
  - **Typical output**:
    - `ai_generated_contents_candidate/scripts/<ontology>/main.py`

---

## 5) Candidate MCP entry used by runtime config (example)

- **Config**: `configs/run_created_mcp.json`
  - **Uses**: `python -m ai_generated_contents_candidate.scripts.ontosynthesis.main`
  - This points the runtime “created MCP” to the candidate MCP `main.py`.

---

## 6) Notes / invariants (contracts the generators must respect)

These are **implementation contracts** that generated scripts must follow because they are relied upon by the runtime + shared utilities:

- **Persistent storage**:
  - All graph mutations must occur inside `with locked_graph() as g:`
  - Do not pass a Graph object into `locked_graph(...)` (it accepts DOI/entity or reads global state).
- **IRI minting**:
  - `_mint_hash_iri(class_local)` is the stable contract in `sandbox/code/universal_utils.py`.
  - Generated scripts must not treat `_mint_hash_iri` as `(namespace, label)` unless the utility contract is changed.
- **Guard decorators**:
  - `_guard_noncheck` is a decorator; generated `create_*` functions should use `@_guard_noncheck` and must not call `_guard_noncheck()` at runtime.
- **Unit handling (OM-2)**:
  - No per-file unit maps; unit validation should flow through the base module’s OM-2 inventory + helpers.
