### Prompt + script generation (technical runbook)

This folder contains the **generation pipeline** that produces the artifacts used by the runtime extraction/KG-building pipeline:

- **Iteration specs** (`iterations.json`): declare what to extract/build per iteration, with file path templates.
- **Prompts** (`.md`): extraction prompts and KG-building prompts referenced by `iterations.json`.
- **Ontology MCP scripts** (`*_creation_*.py` + `main.py`): ontology-specific MCP servers used by agents at runtime.
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

- **LLM credentials** configured when you enable optional `--llm-agent-generation` or SPARQL helpers (typically via `.env` + `models/LLMCreator.py`). Deterministic regeneration via `agentic_generation_main` does not require API access.

---

### Output locations (what gets written where)

#### Candidate outputs (default)
- **Iterations**: `ai_generated_contents_candidate/iterations/<ontology>/iterations.json`
- **Prompts**: `ai_generated_contents_candidate/prompts/<ontology>/*.md`
- **MCP scripts**: `ai_generated_contents_candidate/scripts/<ontology>/...` (deterministic `*_creation_*.py` modules plus `main.py`)

#### Production outputs (used by pipeline runtime by default)
- **Iterations**: `ai_generated_contents/iterations/<ontology>/iterations.json`
- **Prompts**: `ai_generated_contents/prompts/<ontology>/*.md`
- **SPARQL**: `ai_generated_contents/sparqls/<ontology>/top_entity_parsing.sparql`

---

### Recommended workflow

Use **`agentic_generation_main`** to materialize iteration specs, prompts, and MCP Python servers in one pass. For the OntoSynthesis stack aligned with `configs/meta_task/meta_task_config.json`, run ontosynthesis first and then extension ontologies:

```bash
python -m src.agents.scripts_and_prompts_generation.agentic_generation_main \
  --ontology ontosynthesis \
  --stage all \
  --output-root ai_generated_contents_candidate \
  --json

python -m src.agents.scripts_and_prompts_generation.agentic_generation_main \
  --extensions \
  --stage all \
  --output-root ai_generated_contents_candidate \
  --json
```

Medical workflows typically pass `--ontology medical` plus `--meta-task-config` pointing at the medical meta-task JSON (see repository root `README.md`).

Optional LLM-driven refinement after deterministic scaffolding:

```bash
python -m src.agents.scripts_and_prompts_generation.agentic_generation_main \
  --ontology ontosynthesis \
  --stage all \
  --output-root ai_generated_contents_candidate \
  --llm-agent-generation \
  --generation-model gpt-5.2 \
  --json
```

Shell orchestration (bootstrap, SPARQL generation, promotion, MCP rewiring) lives in `scripts/rebuild_pipeline_artifacts.sh`.

Specialized JSON compaction flows (`json_patch_one_script_generation.py`, `json_patch_medical_*.py`, `json_patch_smoke.py`) remain available for experiments but are not required for the default deterministic path.

---

### Command cookbook

### 1) Validate generation environment (fast, no API calls)

```bash
python -m src.agents.scripts_and_prompts_generation.test_generation
```

### 2) Parse a T-Box TTL into structured summaries (no LLM)

```bash
python -m src.agents.scripts_and_prompts_generation.ttl_parser data/ontologies/ontosynthesis.ttl
```

### 3) Regenerate only `main.py` (requires existing deterministic creation modules)

```bash
python -m src.agents.scripts_and_prompts_generation.agentic_generation_main \
  --ontology ontosynthesis \
  --main-only \
  --output-root ai_generated_contents_candidate \
  --json
```

### 4) Generate top-entity parsing SPARQL (LLM)

```bash
python -m src.agents.scripts_and_prompts_generation.top_entity_sparql_generation_agent --ontosynthesis --model gpt-4o
```

### 5) Ensure candidate scripts are importable Python packages

```bash
python -m src.agents.scripts_and_prompts_generation.fix_package_structure
```

### 6) Legacy LLM MCP scaffolding (optional)

`mcp_main_script_creation_agent.py` still exists for older Docker/agent-driven flows, but new work should prefer `agentic_generation_main`.

### 7) Semantic MCP loop (medical)

Closed loop that **regenerates** the medical MCP each outer iteration, runs Level-1 **ruff/contract** repair, builds an A-Box from a mock OP note, then **requires HermiT** (plus OWL-RL checks) via [`scripts/validate_abox_with_reasoner.py`](../../../scripts/validate_abox_with_reasoner.py). Failures become sticky feedback for the next regenerate.

**Default A-Box path (`--abox-mode react`):** write `document_md` as `*_stitched.md`, then run the real pipeline steps `top_entity_extraction` → `top_entity_kg_building` (ReAct) → `main_ontology_extractions` → `main_kg_building` with `force_react_kg` (skips `materialize_hints` short-circuit). Merge `medical_output/*.ttl` into `abox.ttl`.

**Offline harness path (`--abox-mode harness`):** in-process `materialize_hints` only (no pipeline LLM).

```bash
# Full ReAct extract + KG on mock doc, HermiT gate (needs LLM credentials + owlready2/HermiT)
python -m src.agents.scripts_and_prompts_generation.semantic_mcp_loop_medical \
  --fixture tests/fixtures/medical_semantic_mock.json \
  --abox-mode react \
  --max-outer 2 \
  --json

# Offline harness dry path (still requires HermiT for pass)
python -m src.agents.scripts_and_prompts_generation.semantic_mcp_loop_medical \
  --abox-mode harness \
  --no-llm \
  --fixture tests/fixtures/medical_semantic_mock.json \
  --max-ruff-repairs 0 \
  --json
```

Artifacts land under `tmp/semantic_mcp_loop_medical/<run_id>/` (`iter_N/abox.ttl`, `runtime/`, `reasoner_report.json`, `semantic_feedback.md`, `summary.json`). Unittest: `python -m unittest tests.test_semantic_mcp_loop_medical_harness`.

### 8) Semantic MCP loop (OntoSynthesis main-only) — LLM repair proof

Same two-level idea as medical, scoped to **OntoSynthesis main only** (no OntoMOPs/OntoSpecies extension MCP). Default A-Box path is the **harness** (`materialize_hints`) so Level-1 / Level-2 repair signals stay clean. HermiT is required; unknown properties fail the gate (cross-ontology `Species` type noise is soft).

**Non-trivial defects are healed only by LLM unified-diff patches** — not ruff autofix, not restore/undo, not regenerate-as-proof. `--prove-repairs` / `--exercise-*` require LLM credentials (`--no-llm` is rejected).

```bash
# Real OntoSyn run: LLM (gpt-5) regenerates MCP scripts + full T-Box mock fixture, then HermiT
python -m src.agents.scripts_and_prompts_generation.semantic_mcp_loop_ontosynthesis \
  --llm-agent-generation \
  --generation-model gpt-5 \
  --model gpt-5 \
  --max-outer 2 \
  --json

# Preferred repair proof: inject syntax (L1) + BogusSemanticFailProp (L2); heal only via LLM
python -m src.agents.scripts_and_prompts_generation.semantic_mcp_loop_ontosynthesis \
  --prove-repairs \
  --fixture tests/fixtures/ontosynthesis_semantic_mock.json \
  --scripts-source ai_generated_contents_candidate/scripts/ontosynthesis \
  --max-ruff-repairs 2 \
  --json
```

Artifacts: `tmp/semantic_mcp_loop_ontosynthesis/prove_<run_id>/` (`level1_proof.json`, `semantic_proof.json`, `reasoner_*.json`, `summary.json`). Offline unittest covers inject/detect + `--no-llm` rejection: `python -m unittest tests.test_semantic_mcp_loop_ontosynthesis_harness`.

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

