### Prompt + script generation (technical runbook)

This folder contains the **generation pipeline** that produces the artifacts used by the runtime extraction/KG-building pipeline:

- **Iteration specs** (`iterations.json`): declare what to extract/build per iteration, with file path templates.
- **Prompts** (`.md`): extraction prompts and KG-building prompts referenced by `iterations.json`.
- **Ontology MCP scripts** (`*_creation.py` + `main.py`): ontology-specific MCP servers used by agents at runtime.
- **Top-entity parsing SPARQL** (`top_entity_parsing.sparql`): used to extract “top entities” from iteration-1 TTL.

Most outputs are written under `ai_generated_contents_candidate/` (development “candidate” tree). The runtime pipeline typically reads from `ai_generated_contents/` (production tree), so promoting candidate → production is a deliberate step.

---

### Prerequisites (before generating)

- **Bootstrapped repo folders** (creates gitignored dirs required at import-time):

```bash
python scripts/bootstrap_repo.py
```

- **Ontology T-Boxes exist**:
  - `data/ontologies/ontosynthesis.ttl`
  - `data/ontologies/ontomops-subgraph.ttl`
  - `data/ontologies/ontospecies-subgraph.ttl`

- **Meta task config** (drives “which ontologies exist” and their roles):
  - Runtime meta config: `configs/meta_task/meta_task_config.json`
  - Some generators also read: `ape_generated_contents/meta_task_config.json`

- **LLM credentials** configured (typically via `.env` + `models/LLMCreator.py`).

---

### Output locations (what gets written where)

#### Candidate outputs (default)
- **Iterations**: `ai_generated_contents_candidate/iterations/<ontology>/iterations.json`
- **Prompts**: `ai_generated_contents_candidate/prompts/<ontology>/*.md`
- **MCP scripts**: `ai_generated_contents_candidate/scripts/<ontology>/...`
- **Generated MCP config** (orchestration): `configs/generated_ontology_mcps.json`

#### Production outputs (used by pipeline runtime by default)
- **Iterations**: `ai_generated_contents/iterations/<ontology>/iterations.json`
- **Prompts**: `ai_generated_contents/prompts/<ontology>/*.md`
- **SPARQL**: `ai_generated_contents/sparqls/<ontology>/top_entity_parsing.sparql`

---

### Recommended workflow (most users)

Use the one-shot orchestration entrypoint:

```bash
python -m src.agents.scripts_and_prompts_generation.generation_main --all
```

This orchestrator performs:
- Step 0: parse ontology TTLs → `data/ontologies/*_parsed.{md,json}`
- Step 1: generate `iterations.json` → `ai_generated_contents_candidate/iterations/...`
- Step 2: generate MCP underlying scripts → `ai_generated_contents_candidate/scripts/.../*_creation.py`
- Step 3: generate MCP main scripts → `ai_generated_contents_candidate/scripts/.../main.py`
- Step 4: generate extraction prompts → `ai_generated_contents_candidate/prompts/.../EXTRACTION_*.md`
- Step 5: generate KG building prompts → `ai_generated_contents_candidate/prompts/.../KG_BUILDING_*.md` (+ extensions)
- Step 6: generate MCP config JSON → `configs/generated_ontology_mcps.json`

---

### Command cookbook (granular generators)

Use these when you want tighter control (e.g., iterate on just prompts, or just scripts).

---

### 1) Validate generation environment (fast, no API calls)

```bash
python -m src.agents.scripts_and_prompts_generation.test_generation
```

- **Purpose**: checks imports, required config files, and output directories.
- **Outputs**: none (prints status).

---

### 2) Parse a T-Box TTL into structured summaries (no LLM)

```bash
python -m src.agents.scripts_and_prompts_generation.ttl_parser data/ontologies/ontosynthesis.ttl
```

- **Purpose**: create machine-friendly parsed ontology representations.
- **Outputs (default)**:
  - `data/ontologies/ontosynthesis_parsed.json`
  - `data/ontologies/ontosynthesis_parsed.md`

You can also pass explicit output paths:

```bash
python -m src.agents.scripts_and_prompts_generation.ttl_parser data/ontologies/ontosynthesis.ttl out.json out.md
```

---

### 3) Generate a task division plan (OntoSynthesis A-Box plan; LLM)

```bash
python -m src.agents.scripts_and_prompts_generation.task_division_agent \
  --tbox data/ontologies/ontosynthesis.ttl \
  --output configs/task_division_plan.json \
  --model gpt-5
```

- **Purpose**: produce a plan JSON (steps) that can be used by legacy prompt generators (plan-driven mode).
- **Primary input**: OntoSynthesis T-Box.
- **Output**: `configs/task_division_plan.json`
- **Hardcoded defaults** (overrideable via CLI/env):
  - default T-Box: `data/ontologies/ontosynthesis.ttl`
  - default output: `configs/task_division_plan.json`
  - default model: env `TASK_DIVISION_MODEL` or `gpt-5`

---

### 4) Generate `iterations.json` (ontology iteration specification; LLM)

This is the main config file consumed by pipeline steps like `main_ontology_extractions`, `main_kg_building`, and extension steps.

```bash
python -m src.agents.scripts_and_prompts_generation.iteration_creation_agent --ontosynthesis --ontomops --ontospecies
```

- **Outputs**:
  - `ai_generated_contents_candidate/iterations/ontosynthesis/iterations.json`
  - `ai_generated_contents_candidate/iterations/ontomops/iterations.json`
  - `ai_generated_contents_candidate/iterations/ontospecies/iterations.json`
- **Notes**:
  - The agent uses domain-generic prompting but then **post-processes** results using `configs/meta_task/meta_task_config.json` (role/tool defaults).
  - Extension output paths (e.g., `mcp_run_ontomops/extraction_{entity_safe}.txt`) are partially **hardcoded** in post-processing to match pipeline behavior.

---

### 5) Generate extraction prompts (LLM)

There are two modes: **iterations-driven** (recommended) and **plan-driven legacy**.

#### 5.1 Iterations-driven mode (recommended)
Generates all extraction prompts referenced by `ai_generated_contents_candidate/iterations/<ontology>/iterations.json`.

```bash
python -m src.agents.scripts_and_prompts_generation.task_extraction_prompt_creation_agent \
  --ontosynthesis \
  --version 1 \
  --model gpt-5 \
  --parallel 3
```

Also supports:
- `--ontomops`
- `--ontospecies`

**Outputs** (typical):
- `ai_generated_contents_candidate/prompts/<ontology>/EXTRACTION_ITER_1.md`
- `ai_generated_contents_candidate/prompts/<ontology>/EXTRACTION_ITER_2.md`
- `ai_generated_contents_candidate/prompts/<ontology>/PRE_EXTRACTION_ITER_3.md`
- plus sub-iteration prompts if present (e.g., `EXTRACTION_ITER_3_1.md`)

**Important behavior**:
- ITER1 extraction prompt is **generated specially** by analyzing the T-Box directly (entity identification focus).
- Pre-extraction prompts use a different template and can be generated alone with orchestration flags (see below).

#### 5.2 Legacy plan-driven mode (uses `configs/task_division_plan.json`)

```bash
python -m src.agents.scripts_and_prompts_generation.task_extraction_prompt_creation_agent \
  --plan configs/task_division_plan.json \
  --tbox data/ontologies/ontosynthesis.ttl \
  --version 1 \
  --model gpt-5 \
  --parallel 3
```

Outputs go under `sandbox/extraction_scopes/<version>/...` (legacy).

---

### 6) Generate KG-building prompts (LLM)

Two modes: **plan-driven** and **iterations-driven**.

#### 6.1 Plan-driven KG prompts (from `configs/task_division_plan.json`)

```bash
python -m src.agents.scripts_and_prompts_generation.task_prompt_creation_agent \
  --plan configs/task_division_plan.json \
  --tbox data/ontologies/ontosynthesis.ttl \
  --version 1 \
  --model gpt-4.1 \
  --parallel 3
```

- **Outputs**:
  - `sandbox/prompts/<version>/MCP_PROMPT_ITER_*.txt` (legacy)
  - plus a python bundle `prompts_v<version>.py` (legacy)

#### 6.2 Iterations-driven KG prompts (recommended for pipeline)

For iteration-driven mode, pass the ontology short name as `--tbox`:

```bash
python -m src.agents.scripts_and_prompts_generation.task_prompt_creation_agent --tbox ontosynthesis --version 1
```

- **Purpose**: read `ai_generated_contents_candidate/iterations/<ontology>/iterations.json` and write any referenced `kg_building_prompt` templates into `ai_generated_contents_candidate/prompts/...`.
- **Outputs**: `ai_generated_contents_candidate/prompts/<ontology>/KG_BUILDING_*.md` (+ extension prompt templates).

---

### 7) Generate MCP underlying scripts (LLM)

```bash
python -m src.agents.scripts_and_prompts_generation.mcp_underlying_script_creation_agent --all
```

Or a single ontology:

```bash
python -m src.agents.scripts_and_prompts_generation.mcp_underlying_script_creation_agent \
  --ontology ontosynthesis \
  --model gpt-5
```

Optional: step-by-step generation aligned with `configs/task_division_plan.json`:

```bash
python -m src.agents.scripts_and_prompts_generation.mcp_underlying_script_creation_agent \
  --ontology ontosynthesis \
  --model gpt-5 \
  --split
```

- **Outputs**:
  - `ai_generated_contents_candidate/scripts/<ontology>/<ontology>_creation.py`
  - (split mode may create `step*_*.py` components)

---

### 8) Generate MCP main scripts (`main.py`) (LLM)

```bash
python -m src.agents.scripts_and_prompts_generation.mcp_main_script_creation_agent --all
```

Or a single ontology:

```bash
python -m src.agents.scripts_and_prompts_generation.mcp_main_script_creation_agent --ontology ontosynthesis --model gpt-4.1
```

- **Inputs**:
  - Underlying script: auto-detected from:
    - `ai_generated_contents_candidate/scripts/<ontology>/<ontology>_creation.py` (preferred)
    - else `scripts/<ontology>/<ontology>_creation.py`
- **Outputs**:
  - `ai_generated_contents_candidate/scripts/<ontology>/main.py` (same folder as underlying script)

---

### 9) Generate top-entity parsing SPARQL (LLM)

This produces the SPARQL query used by the pipeline to parse iteration-1 TTL into `iter1_top_entities.json`.

```bash
python -m src.agents.scripts_and_prompts_generation.top_entity_sparql_generation_agent --ontosynthesis --model gpt-4o
```

- **Inputs**: expects `data/ontologies/<ontology>.ttl` to exist.
- **Output**:
  - `ai_generated_contents/sparqls/<ontology>/top_entity_parsing.sparql`

---

### 10) Ensure candidate scripts are importable as python packages

If you want to run generated MCP servers via `python -m ai_generated_contents_candidate.scripts.<ontology>.main`,
ensure `__init__.py` files exist:

```bash
python -m src.agents.scripts_and_prompts_generation.fix_package_structure
```

---

### One-shot orchestration CLI reference

The orchestrator has useful flags for partial generation:

```bash
python -m src.agents.scripts_and_prompts_generation.generation_main --all --model gpt-4o
```

Skip steps:
- `--skip-iterations`
- `--skip-underlying`
- `--skip-main`
- `--skip-extraction-prompts`
- `--skip-kg-prompts`
- `--skip-mcp-config`

Generate only certain extraction prompts:
- `--iter1-only`, `--iter2-only`, `--iter3-only`, `--iter4-only`
- `--pre-extraction-only` (auto-skips non-extraction steps)

Direct generation mode (faster; no MCP agent wrappers):
- `--direct`

---

### Promotion: candidate → production (recommended practice)

Runtime pipeline steps typically read from `ai_generated_contents/...` (production). Generation writes to `ai_generated_contents_candidate/...`.

Recommended workflow:
- Generate into candidate.
- Review diffs / run a small pipeline run on a test DOI hash.
- Copy the stable artifacts into `ai_generated_contents/`:
  - `ai_generated_contents_candidate/iterations/<ontology>/iterations.json` → `ai_generated_contents/iterations/<ontology>/iterations.json`
  - `ai_generated_contents_candidate/prompts/<ontology>/*.md` → `ai_generated_contents/prompts/<ontology>/*.md`
  - (optional) scripts if you want production scripts committed/packaged

---

### Troubleshooting notes

- **Missing folders / import-time crashes**: run `python scripts/bootstrap_repo.py`.
- **Missing ontology TTLs**: verify `data/ontologies/*.ttl` exist and match what scripts expect.
- **Package import errors for generated scripts**: run `fix_package_structure.py`.
- **Model output format issues**: many generators strip markdown fences; retries exist, but if the model consistently fails, use a more capable model (e.g., `gpt-4o`/`gpt-5` depending on the task).

