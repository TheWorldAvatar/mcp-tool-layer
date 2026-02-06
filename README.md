# MCP-enhanced MOPs Extraction

A multi-stage pipeline for extracting Metal-Organic Polyhedra (MOPs) information from scientific papers using MCP-enhanced LLM agents, producing structured knowledge graphs (TTL).

## Setup (detailed)

### Prerequisites

- Python **3.11+**
- (Recommended) **WSL** on Windows for a smoother Linux-like environment
- Docker (only if you use MCP tools that require it; some tools are stdio-only)

### 1) Create a Python environment

```bash
# venv
python -m venv .venv
source .venv/bin/activate  # Windows PowerShell: .venv\Scripts\Activate.ps1

# or conda
conda create -n mcp_layer python=3.11
conda activate mcp_layer
```

### 2) Install dependencies

```bash
pip install -r requirements.txt
```

### 3) Bootstrap required runtime folders (important)

This repo **git-ignores** many runtime folders (caches, logs, generated prompts/scripts).  
Some modules (notably `models/locations.py`) **require directories to exist at import time**.

Run:

```bash
python scripts/bootstrap_repo.py
```

If you plan to run grounding/lookup agents, also create grounding-cache folders:

```bash
python scripts/bootstrap_repo.py --with-grounding-cache ontospecies
```

### 4) Configure MCP settings

```bash
cp configs/mcp_configs.json.example configs/mcp_configs.json
```

Then edit `configs/mcp_configs.json` to reflect your local environment (paths, server commands).

### 5) Configure LLM credentials (if you run LLM agents)

This repo does **not** ship a committed `.env.example`. Create `.env` in the repo root with what your environment expects.
At minimum, many agents expect something like:

```bash
API_KEY=...
BASE_URL=...
```

Exact keys depend on your `models/ModelConfig.py` / `models/LLMCreator.py` configuration.

## Common folder layout (fresh clone)

After `python scripts/bootstrap_repo.py`, you should have (among others):

- `data/` (runtime data, cached results; **gitignored**)
  - `data/log/` (required; some modules error if missing)
  - `data/ontologies/` (place ontology T-Box TTLs here)
  - `data/grounding_cache/<ontology>/labels` (optional; for Script C fuzzy lookup)
- `raw_data/` (PDF inputs; **gitignored**)
- `sandbox/` (scratch scripts; **gitignored**)
- `ai_generated_contents*/` (LLM-generated artifacts; **gitignored**)

## Grounding (overview)

There are two “layers”:

1) **Ontology-specific MCP lookup server** (generated for a given ontology)
2) **Grounding consumer agent** that applies mappings to TTLs

### OntoSpecies lookup MCP server

This repo includes `configs/grounding.json` to run the OntoSpecies lookup server via stdio.

### Ground TTLs (single or batch)

The grounding agent lives at `src/agents/grounding/grounding_agent.py`.

- Single file:

```bash
python -m src.agents.grounding.grounding_agent --ttl path/to/file.ttl --write-grounded-ttl
```

- Batch folder (recursively processes `*.ttl`, skipping `*_grounded.ttl` and `*link.ttl`):

```bash
python -m src.agents.grounding.grounding_agent --batch-dir evaluation/data/merged_tll --write-grounded-ttl
```

Notes:
- Internal merge (deduplicating identical nodes across TTLs) runs by default in batch mode; disable with `--no-internal-merge`.
- Default grounding materialization mode is `replace` (replaces `source_iri` with `grounded_iri`). You can switch to `sameas` with `--grounding-mode sameas`.

## Main extraction entrypoint

The main pipeline entrypoint is `mop_main.py` (see its CLI help):

```bash
python mop_main.py --help
```

## Prompt + MCP script generation (no `.sh` wrappers)

Use the following canonical Python entrypoints to generate plans, prompts, and MCP scripts.

### 1) Generate a task division plan (writes `configs/task_division_plan.json`)

```bash
python -m src.agents.scripts_and_prompts_generation.task_division_agent \
  --tbox data/ontologies/ontosynthesis.ttl \
  --output configs/task_division_plan.json \
  --model gpt-5
```

### 2) Generate KG-building iteration prompts (writes into `ai_generated_contents_candidate/prompts/…`)

```bash
python -m src.agents.scripts_and_prompts_generation.task_prompt_creation_agent \
  --version 1 \
  --plan configs/task_division_plan.json \
  --tbox data/ontologies/ontosynthesis.ttl \
  --model gpt-4.1 \
  --parallel 3
```

### 3) Generate extraction-scope prompts (writes into `ai_generated_contents_candidate/prompts/…`)

Legacy plan-driven mode (matches the old `run_extraction_prompt_creation.sh` intent):

```bash
python -m src.agents.scripts_and_prompts_generation.task_extraction_prompt_creation_agent \
  --version 1 \
  --plan configs/task_division_plan.json \
  --tbox data/ontologies/ontosynthesis.ttl \
  --model gpt-5 \
  --parallel 3
```

Iterations-driven mode (uses ontology flags + `ai_generated_contents_candidate/iterations/**/iterations.json`):

```bash
python -m src.agents.scripts_and_prompts_generation.task_extraction_prompt_creation_agent \
  --ontosynthesis \
  --version 1 \
  --model gpt-5 \
  --parallel 3
```

### 4) Generate MCP underlying scripts from T-Box (writes into `ai_generated_contents_candidate/scripts/…`)

All ontologies from `ape_generated_contents/meta_task_config.json`:

```bash
python -m src.agents.scripts_and_prompts_generation.mcp_underlying_script_creation_agent --all
```

Single ontology (by short name or by TTL path):

```bash
python -m src.agents.scripts_and_prompts_generation.mcp_underlying_script_creation_agent \
  --ontology ontosynthesis \
  --model gpt-5 \
  --split
```

## Repo maintenance scripts (`scripts/`)

These convenience wrappers help you (a) regenerate the full “pipeline artefacts” and (b) reset the workspace back to a clean state.

### 1) Regenerate *all* pipeline artefacts + promote to production

- Generates **candidate** artefacts via `generation_main` (iterations, prompts, MCP scripts, generated MCP config)
- Generates **top-entity parsing SPARQL** (writes into `ai_generated_contents/`)
- Promotes candidate prompts + iterations into `ai_generated_contents/` (what the runtime pipeline reads by default)
- Rewires runtime MCP configs to use the newly generated MCP servers

```bash
bash scripts/rebuild_pipeline_artifacts.sh
```

Optional flags:

```bash
bash scripts/rebuild_pipeline_artifacts.sh --model gpt-5
bash scripts/rebuild_pipeline_artifacts.sh --direct --model gpt-4o
bash scripts/rebuild_pipeline_artifacts.sh --model gpt-5.2 --test
bash scripts/rebuild_pipeline_artifacts.sh --no-promote
bash scripts/rebuild_pipeline_artifacts.sh --no-rewire-mcp
```

Main-only (reuse existing candidate scripts, regenerate only `main.py`):

```bash
bash scripts/rebuild_pipeline_artifacts.sh --test --ontology ontosynthesis --model gpt-4.1 --main-only
```

Notes:
- Script generation is **direct-by-default** (no MCP/Docker required for code output). To force agent/MCP script generation (requires Docker), run the Python entrypoint with `--agent-scripts`.

### 1.5) Rewire which MCP the pipeline uses (NO regeneration; cheap)

If you already have a generated MCP server and want the KG construction pipeline to use it **without rerunning any LLM generation**, use:

```bash
# Use the already-generated *candidate* MCP server for ontosynthesis
python scripts/rewire_pipeline_mcp.py \
  --ontology ontosynthesis \
  --tree candidate \
  --mcp-set run_created_mcp.json \
  --update-meta-task
```

To switch back to the **production** tree (`ai_generated_contents/`):

```bash
python scripts/rewire_pipeline_mcp.py \
  --ontology ontosynthesis \
  --tree production \
  --mcp-set run_created_mcp.json \
  --update-meta-task
```

Notes:
- This updates `configs/run_created_mcp.json` (and optionally `configs/meta_task/meta_task_config.json`) and writes timestamped `.bak.*` backups.
- This does **not** generate or modify any MCP code; it only changes which module is launched for `llm_created_mcp`.

### 2) Clean run outputs + prune `raw_data/` (and clean evaluation artefacts)

- **Dry-run first** (prints what would be deleted):

```bash
bash scripts/cleanup_results_and_raw_data.sh
```

- Actually delete (**irreversible**):

```bash
bash scripts/cleanup_results_and_raw_data.sh --real
```

By default it keeps only the DOI mapped to hash `0c57bac8` in `raw_data/`. You can override:

```bash
bash scripts/cleanup_results_and_raw_data.sh --keep-hash 0c57bac8 --real
```

### 3) Cheap “unit tests” for generation pipeline setup (no LLM calls)

This is the recommended **pre-flight check** before running any expensive generation.

```bash
bash scripts/test_generation_pipeline.sh
```

### 4) Real LLM smoke test (1 cheap call)

This makes **one** small LLM call to generate a tiny Python file and verifies it compiles.

```bash
bash scripts/test_llm_smoke.sh gpt-5.2
```


