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


