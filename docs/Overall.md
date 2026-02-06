
### Project overview: MCP-enhanced MOPs Extraction + Grounding

This repo implements an end-to-end system for:

1. **LLM-driven generation of prompts + MCP servers** to support ontology-guided extraction and KG building from scientific papers (MOPs focus).
2. **Pipeline execution** over PDFs to produce per-paper RDF/Turtle outputs (main ontology + extension ontologies).
3. **Grounding generation + KG grounding** to canonicalize entities by mapping labels/identifiers to a reference KG (currently OntoSpecies), and to apply those mappings to TTL outputs.

This document is the “map of the repo”: what each major part does, and where to find its inputs/outputs/configs.

---

### 1) Key concepts and artifacts

#### 1.1 DOI hash (data partition key)
- Most runtime data is stored under `data/<doi_hash>/`.
- A DOI hash is an 8-char stable hash (SHA-256 truncated).
- Mappings are stored in `data/doi_to_hash.json`.

#### 1.2 The three layers
- **Ontology tooling generation (LLM)**: produces iteration specs, prompts, and MCP scripts from ontology T-Boxes.
- **Extraction + KG building runtime**: runs a pipeline over PDFs to create TTL outputs.
- **Grounding (generation + runtime)**: creates a lookup layer for a target KG, then uses it to canonicalize IRIs in produced TTLs.

---

### 2) Repository layout (what lives where)

#### 2.1 Inputs you provide (typically gitignored)
- **PDF papers**: `raw_data/<doi>.pdf` and optionally `raw_data/<doi>_si.pdf`
- **Ontology T-Boxes**: `data/ontologies/*.ttl`
- **MCP runtime config** (local paths/commands): `configs/mcp_configs.json` (copied from `configs/mcp_configs.json.example`)
- **LLM credentials**: `.env` (not committed)

#### 2.2 Generated content (mostly gitignored build artifacts)
- **Generation outputs (candidate)**:
  - `ai_generated_contents_candidate/iterations/<ontology>/iterations.json`
  - `ai_generated_contents_candidate/prompts/<ontology>/...`
  - `ai_generated_contents_candidate/scripts/<ontology>/...`
- **Production outputs (used by pipeline by default)**:
  - `ai_generated_contents/iterations/<ontology>/iterations.json`
  - `ai_generated_contents/prompts/<ontology>/...`
  - `ai_generated_contents/sparqls/<ontology>/...` (e.g., top-entity parsing SPARQL)
- **Reference snapshot**: `ai_generated_contents_reference/` (when used)

#### 2.3 Runtime outputs (per DOI hash; gitignored)
Under `data/<hash>/` you’ll typically see:
- **PDF copies**: `<hash>.pdf`, `<hash>_si.pdf`
- **Converted markdown**: `<hash>.md`, `<hash>_si.md`
- **Sectioning/classification**: `sections.json`, `<hash>_stitched.md`
- **Top entity extraction**: `top_entities.txt`
- **Top entity KG**:
  - `iteration_1.ttl`
  - `mcp_run/iter1_top_entities.json`
- **Main ontology (OntoSynthesis) extraction hints**: `mcp_run/iter2_hints_*.txt`, `iter3_hints_*.txt`, `iter4_hints_*.txt`
- **Main ontology KG building artifacts**:
  - `intermediate_ttl_files/iteration_2_*.ttl`, `iteration_3_*.ttl`, `iteration_4_*.ttl` (per entity)
  - per-entity prompts/responses under `prompts/` and `responses/`
- **Extensions extraction**:
  - `mcp_run_ontomops/extraction_*.txt`
  - `mcp_run_ontospecies/extraction_*.txt`
- **Extensions KG outputs**:
  - `ontomops_output/*.ttl`
  - `ontospecies_output/*.ttl`
- **MOP derivation outputs**:
  - `cbu_derivation/...` (metal/organic/integrated/full)

#### 2.4 Evaluation outputs
- Merged TTLs and JSON conversions:
  - `evaluation/data/merged_tll/<hash>/<hash>.ttl`
  - plus `link.ttl`, `cbu.json`, `chemicals.json`, `steps.json`, `characterisation.json`
- Ablation plans and results:
  - `evaluation/ablation/*.md`

---

### 3) Major runtime entrypoints (how you run things)

#### 3.1 `mop_main.py` (legacy orchestrator; multiple run modes)
- **Purpose**: “classic” pipeline orchestration with run modes like `--iter1`, `--iter2`, `--extraction-only`, etc.
- **Implements**: calls into `src/utils/pipeline.py` which drives conversion → division/classification → dynamic MCP extraction, plus later stages.
- **Primary use**: quick iterative runs and older workflows.

#### 3.2 `generic_main.py` (clean config-driven runner)
- **Purpose**: run a pipeline defined strictly by `configs/pipeline.json` (step list), per DOI hash.
- **Key features**:
  - deterministic DOI discovery from `raw_data/`
  - step modules are loaded from `src/pipelines/<step_name>/`
  - passes a `step_config` dict to each step
  - supports a “test mode” for using generated MCP tools via `--test` (writes `configs/test_mcp_config.json`)
- **Primary use**: controlled runs, reproducibility, and ablation testing.

#### 3.3 Grounding runtime
- **Entry point**: `python -m src.agents.grounding.grounding_agent`
- **Purpose**: canonicalize entities in TTLs (single-file or batch) using an MCP lookup server (typically OntoSpecies).

---

### 4) Part A — Generation: prompts + MCP servers for extraction and KG building

This is the “build step” that prepares the ontology-specific artifacts used by the runtime pipeline.

#### 4.1 Inputs
- **Ontology schema (T-Box)**: `data/ontologies/<ontology>.ttl`
- **Meta task config**:
  - `configs/meta_task/meta_task_config.json` (used by pipeline runtime to know which ontologies are “main” vs “extensions” and which MCP sets/tools to use)
  - `ape_generated_contents/meta_task_config.json` (used by some generation agents as a source-of-truth ontology list)

#### 4.2 Outputs
- **Iterations specs**: `ai_generated_contents_candidate/iterations/<ontology>/iterations.json`
  - Describes extraction/KG-building iterations, per-entity behavior, and where hints/prompts/TTLs should go.
- **Prompts**: `ai_generated_contents_candidate/prompts/<ontology>/...`
  - KG-building prompts, extraction prompts, extension prompts.
- **Generated MCP scripts**: `ai_generated_contents_candidate/scripts/<ontology>/...`
  - Ontology-specific MCP server(s) and helper scripts (LLM-created tooling).

#### 4.3 Where generation happens (code)
- Generation orchestration and agents live in:
  - `src/agents/scripts_and_prompts_generation/`
- Common entrypoints are documented in `README.md` (repo root), including:
  - `task_division_agent.py`
  - `iteration_creation_agent.py`
  - `task_prompt_creation_agent.py`
  - `task_extraction_prompt_creation_agent.py`
  - `mcp_underlying_script_creation_agent.py`
  - `mcp_main_script_creation_agent.py`

---

### 5) Part B — Runtime pipeline: extraction + KG building from PDFs

At runtime, the project processes PDFs into markdown, identifies top entities, performs iterative extraction (hint files), and builds TTL knowledge graphs.

#### 5.1 Pipeline step list (config)
- Default step ordering is defined in:
  - `configs/pipeline.json`
- Step implementations are under:
  - `src/pipelines/<step_name>/`

Default steps:
- `pdf_conversion`
- `section_classification`
- `stitching`
- `top_entity_extraction`
- `top_entity_kg_building`
- `main_ontology_extractions`
- `main_kg_building`
- `extensions_extractions`
- `extensions_kg_building`
- `mop_derivation`

#### 5.2 What each step does (inputs → outputs)

##### `pdf_conversion`
- **Code**: `src/pipelines/pdf_conversion/convert.py`
- **Purpose**: PDF → markdown (text + tables + merged).
- **Inputs**: `data/<hash>/<hash>.pdf` and optional `data/<hash>/<hash>_si.pdf`
- **Outputs**: `data/<hash>/<hash>.md`, `data/<hash>/<hash>_si.md` (and intermediate `*_text.md`, `*_tables.md`)

##### `section_classification`
- **Code**: `src/pipelines/section_classification/`
- **Purpose**: split markdown into sections and classify “keep vs discard” using an LLM agent.
- **Inputs**: `data/<hash>/<hash>.md`, `data/<hash>/<hash>_si.md`
- **Outputs**: `data/<hash>/sections.json`

##### `stitching`
- **Code**: `src/pipelines/stitching/stitch.py`
- **Purpose**: stitch “kept” sections into a single model-friendly document.
- **Inputs**: `data/<hash>/sections.json`
- **Outputs**: `data/<hash>/<hash>_stitched.md`

##### `top_entity_extraction`
- **Code**: `src/pipelines/top_entity_extraction/extract.py`
- **Purpose**: extract top-level entities (e.g., synthesis instances) from the stitched paper.
- **Inputs**: `data/<hash>/<hash>_stitched.md`
- **Outputs**: `data/<hash>/top_entities.txt`

##### `top_entity_kg_building` (iteration 1 KG)
- **Code**: `src/pipelines/top_entity_kg_building/build.py`
- **Purpose**: build iteration-1 TTL and parse top entities into JSON for later steps.
- **Inputs**:
  - `data/<hash>/top_entities.txt`
  - KG prompt: `ai_generated_contents/prompts/<ontology>/...` (main ontology)
  - Parsing SPARQL: `ai_generated_contents/sparqls/<ontology>/top_entity_parsing.sparql`
- **Outputs**:
  - `data/<hash>/iteration_1.ttl`
  - `data/<hash>/mcp_run/iter1_top_entities.json`

##### `main_ontology_extractions` (hints only; iterations 2+)
- **Code**: `src/pipelines/main_ontology_extractions/extract.py`
- **Purpose**: generate extraction hint files for later KG building.
- **Inputs**:
  - `data/<hash>/<hash>_stitched.md`
  - `data/<hash>/mcp_run/iter1_top_entities.json`
  - Iteration spec: `ai_generated_contents/iterations/ontosynthesis/iterations.json`
  - Prompts: `ai_generated_contents/prompts/ontosynthesis/*`
- **Outputs** (examples; depend on iteration spec):
  - `data/<hash>/mcp_run/iter2_hints_<entity>.txt`
  - `data/<hash>/mcp_run/iter3_hints_<entity>.txt` (+ enrichment markers/files)
  - `data/<hash>/mcp_run/iter4_hints_<entity>.txt`

##### `main_kg_building` (KG for iterations 2/3/4)
- **Code**: `src/pipelines/main_kg_building/build.py`
- **Purpose**: read the hint files and produce per-entity TTL outputs for later merging/evaluation.
- **Inputs**:
  - `data/<hash>/mcp_run/iter*_hints_<entity>.txt`
  - `data/<hash>/mcp_run/iter1_top_entities.json`
  - KG prompts from `ai_generated_contents/prompts/ontosynthesis/KG_BUILDING_ITER_*.md`
- **Outputs**:
  - `data/<hash>/intermediate_ttl_files/iteration_<n>_<entity>.ttl`
  - per-entity prompts/responses under `data/<hash>/prompts/` and `data/<hash>/responses/`

##### `extensions_extractions`
- **Code**: `src/pipelines/extensions_extractions/extract.py`
- **Purpose**: extract extension-ontology content per top entity (text outputs used by extension KG builders).
- **Inputs**:
  - stitched paper
  - `iter1_top_entities.json`
  - extension prompts and iteration specs:
    - `ai_generated_contents/iterations/<ext>/iterations.json`
    - `ai_generated_contents/prompts/<ext>/...`
- **Outputs**:
  - `data/<hash>/mcp_run_ontomops/extraction_<entity>.txt`
  - `data/<hash>/mcp_run_ontospecies/extraction_<entity>.txt`

##### `extensions_kg_building`
- **Code**: `src/pipelines/extensions_kg_building/build.py`
- **Purpose**: build extension TTLs (OntoMOPs + OntoSpecies) per top entity.
- **Inputs**:
  - `data/<hash>/mcp_run_<ext>/extraction_<entity>.txt`
  - main ontology TTL (per entity) as context
  - iteration specs under `ai_generated_contents_candidate/iterations/<ext>/iterations.json` (preferred if present) else `ai_generated_contents/...`
- **Outputs**:
  - `data/<hash>/ontomops_output/*.ttl`
  - `data/<hash>/ontospecies_output/*.ttl`

##### `mop_derivation`
- **Code**: `src/pipelines/mop_derivation/derive.py`
- **Purpose**: derive CBUs / MOP formulas and integrate with extracted structures.
- **Inputs**:
  - extension TTLs under `data/<hash>/ontomops_output/`
  - intermediate derivation outputs (may be skipped if already present)
- **Outputs**:
  - `data/<hash>/cbu_derivation/...` (structured + integrated outputs)

#### 5.3 MCP tools and configs (runtime)
- MCP server configs live under `configs/*.json`:
  - `configs/mcp_configs.json` (general MCP tool set; local paths)
  - `configs/chemistry.json` (pubchem / enhanced_websearch / ccdc / chemistry servers)
  - `configs/extension.json` (extension MCP servers)
  - `configs/run_created_mcp.json` (LLM-created MCP server for KG building iterations)
  - `configs/grounding.json` (OntoSpecies lookup server for grounding)
- The agent base class `models/BaseAgent.py` loads MCP server definitions via `models/MCPConfig.py` and opens one MCP session per configured tool name.

---

### 6) Part C — Grounding generation + KG grounding

Grounding is documented in detail in:
- `src/agents/grounding/README.md`

#### 6.1 Grounding generation (build lookup layer for a target KG)
- **Agents**: `src/agents/grounding/agent1_*` to `agent4_*`
- **Typical outputs**:
  - `data/grounding_cache/<ontology>/labels/**.jsonl`
  - Script C module (example): `sandbox/script_c_query_client.py`

#### 6.2 Grounding runtime (apply mappings to produced TTLs)
- **Runtime**: `src/agents/grounding/grounding_agent.py`
- **Inputs**:
  - one TTL (`--ttl`) or a folder of TTLs (`--batch-dir`)
  - MCP lookup server config: `configs/grounding.json`
- **Outputs**:
  - a JSON mapping + per-entity details (printed)
  - optionally `*_grounded.ttl` files (replace IRIs or add `owl:sameAs`)

---

### 7) Part D — Merging and evaluation outputs

The repo includes a post-processing step that merges per-step TTLs, builds link graphs, prunes orphans, and exports JSON summaries.

- **Script**: `scripts/merge_and_conversion_main.py`
- **Inputs**: `data/<hash>/...` (main TTLs, intermediate TTLs, extension TTLs)
- **Outputs**: `evaluation/data/merged_tll/<hash>/`
  - `<hash>.ttl` (merged/pruned)
  - `link.ttl` (link-only subgraph)
  - `cbu.json`, `chemicals.json`, `steps.json`, `characterisation.json` (debug/evaluation-friendly views)

---

### 8) Where to modify the system (development guide)

#### 8.1 Changing ontology behavior
- Update ontology T-Box under `data/ontologies/`
- Regenerate:
  - iteration specs: `src/agents/scripts_and_prompts_generation/iteration_creation_agent.py`
  - prompts: `task_prompt_creation_agent.py`, `task_extraction_prompt_creation_agent.py`
  - MCP scripts: `mcp_underlying_script_creation_agent.py`, `mcp_main_script_creation_agent.py`
- Promote candidate outputs from `ai_generated_contents_candidate/` into `ai_generated_contents/` when stable.

#### 8.2 Changing runtime pipeline behavior
- Step implementations: `src/pipelines/<step_name>/`
- Step ordering: `configs/pipeline.json`
- Per-step MCP tools / iteration specs:
  - main ontology: `ai_generated_contents/iterations/ontosynthesis/iterations.json`
  - extensions: `ai_generated_contents/iterations/<ext>/iterations.json`

#### 8.3 Changing grounding behavior
- Grounding build/runtime: `src/agents/grounding/` (see its README)
- MCP lookup server wiring: `configs/grounding.json` and `src/mcp_servers/ontospecies/main.py`

---

### 9) Suggested “happy path” workflow (end-to-end)

1. **Bootstrap folders**:
   - `python scripts/bootstrap_repo.py`
2. **Put PDFs** into `raw_data/` and run pipeline:
   - with `generic_main.py` (config-driven) or `mop_main.py` (legacy run modes)
3. **Merge outputs for evaluation**:
   - `python scripts/merge_and_conversion_main.py`
4. **Ground merged TTLs**:
   - `python -m src.agents.grounding.grounding_agent --batch-dir evaluation/data/merged_tll --write-grounded-ttl`


