# Extraction runtime

This is the PDF-to-KG path. It is not prompt generation and it is not MCP-script generation.

| You want to | Read |
|---|---|
| Write thick extraction prompts with GPT-5 | [README.md](../README.md) |
| Convert PDFs, extract hints, and build the KG | **This page** |

The runtime reads **one domain config** plus **one generated batch**. It never reads `meta_task_config`. Tool names, the selected top class, iteration slots, and SPARQL all come from `configs/domains/*.json` and one `generated/runs/<id>/` package (or `TWA_GENERATED_ARTIFACT_ROOT`).

Entry:

```powershell
python -m src.extraction_runtime ontosynthesis
python -m src.extraction_runtime medical --test
```

Install runtime extras first:

```powershell
pip install -r requirements-runtime.txt
pip install -e .
```

## Generated artifacts must exist first

Point the runtime at **one** batch that already contains `prompts/`, `iterations/`, `sparqls/`, and `scripts/`:

```powershell
python -m src.extraction_runtime ontosynthesis --generation-run gpt5_r1
python -m src.extraction_runtime ontosynthesis --generated-root generated/runs/20260906_152000_gpt5_r1
$env:TWA_GENERATED_ARTIFACT_ROOT = (Resolve-Path "generated/runs/20260906_152000_gpt5_r1").Path
```

If those are unset, the runtime uses `generated/current.json`, then the latest `generated/runs/*`, then the legacy flat `generated/` tree. `--generation-run legacy` forces that old tree. A run package never falls back into another batch's prompts.

`load_prompt` splices a sibling `*.materializable.inc` onto the matching `.md` at run time. Do not hand-edit `.inc` files.

This package does **not** write `KG_BUILDING_ITER_*.md`. KG steps bind hints to the generated MCP servers with a short runtime-owned English envelope. If the generated MCP writes no TTL, that entity is skipped and the paper continues. It does not fall back to generating a thick KG prompt.

Runtime validation is fail-soft: shape checks and closed-type lists get a few retries, then the step keeps whatever is valid and moves on. Prefer a partial paper over aborting the run. After the configured steps finish, if a requested extraction marker is still missing (`top_entities.txt`, `.main_ontology_extractions_done`, `.extensions_extractions_done`), the runtime reruns the full step list once (existing artifacts are skipped). Config, discovery, and runtime-init errors still stop the job.

`--test` writes `configs/test_mcp_config_<ontology>_<run>.json` from domain `mcp_capabilities`, then starts `scripts/<ontology>/` from the artifact root. `ccdc` still launches the in-repo server.

## Two PDF-to-markdown chains

Pipeline shape comes from the domain config: `execution_profile` selects the base step list, `runtime.extra_steps` appends optional steps, `runtime.vision_required` chooses PDF conversion behavior, and `runtime.scenario_domain` chooses `scenarios/<name>/`. The runtime does not open `meta_task_config` and does not special-case ontology names.

### Chemistry: no vision

Paper PDF (main + optional SI) → PyMuPDF text + optional docling tables → `{hash}.md` / `{hash}_text.md` / `{hash}_tables.md`. SI uses the same names with `_si`.

Do **not** turn on `vision_pdf_conversion` or `--vision` for chemistry.

`tbox_slim` then shortens the conversion markdown with the domain `models.runtime_extraction` model (same name as extraction, not the KG model). It is delete-only: kept blocks are copied verbatim. T-Boxes are `tbox.primary` plus `tbox.supporting`, except unit vocabularies such as `om2.ttl`. Source is main + SI + tables (skip a file when its text is already inside an earlier file). The step writes `{hash}_slim.md` and `{hash}_slim.meta.json`.

Downstream paper load order: `_slim.md` (complete body; SI is not appended again) → `_vision.md` → `.md` → `_text.md`, then append SI (`_si_text.md` → `_si_vision.md` → `_si.md` → `_si_tables.md`) only when slim is absent.

Extraction and extension steps all use that loader. If slim is missing they fall back to conversion markdown and continue.

Official chemistry extract starts from the eval30 PDFs every time: `pdf_conversion` (0827 `simple_conversion`, no vision, no chrome trim, no stitch) → `tbox_slim` (concatenates `{hash}.md` + `{hash}_si.md` + tables) → extract. Do not seed `eval30_md`, `gpt41_tbox_slim`, or any other prebuilt markdown.

### OntoMed: vision required

Scanned or oddly laid-out operative notes. Vision is on by default and failure fails the step. It does not silently fall back to plain text.

| Item | Value |
|---|---|
| Switch | `vision_pdf_conversion: true` (forced when the domain is medical) |
| Model | `vision_model`, default `gpt-4o` |
| DPI | `vision_dpi`, default `150` |
| Credentials | `.env` `REMOTE_API_KEY` + `REMOTE_BASE_URL` (or `OPENAI_API_KEY`) |

Writes `{hash}_vision.md` and copies it to `{hash}.md` and `{hash}_text.md`. Extraction reads `_vision.md` directly. OntoMed does **not** run chemistry `tbox_slim` (that step is for long OntoSyn papers).

`--no-vision` can turn vision off. Do not use that for the current OntoMed path.

## Default steps

`complex_main` plus `runtime.extra_steps` (OntoSynthesis currently appends `mop_derivation`):

`pdf_conversion` → `tbox_slim` → `top_entity_extraction` → `top_entity_kg_building` → `main_ontology_extractions` → `main_kg_building` → `extensions_extractions` → `extensions_kg_building` → `mop_derivation`

`simple_main` (medical.json sets `vision_required`):

`pdf_conversion` (vision) → `top_entity_extraction` → `top_entity_kg_building` → `main_ontology_extractions` → `main_kg_building`

The top class is read from `ontology_structures/<ontology>/generation_contract.json` in the selected batch (or the parent contract for extensions). The runtime does not hardcode `ChemicalSynthesis` or `MedicalCase`.

Chemistry main extraction: ITER 2/3 may open PubChem / CCDC / websearch (`use_agent`); ITER 3 runs a closed-ledger PRE extraction. PubChem returns a compact identity-name list (deterministic junk filter plus the extraction `advanced_model` collapse; `PUBCHEM_NAME_DEDUP_LLM=0` disables the LLM pass). Prompts also inject a lookup-alias rule so agents do not paste synonym dumps. Medical ITER 2 is JSON and does not open those tools.

Extension queues run generated `sparqls/<ext>/top_entity_parsing.sparql` against published main TTL. KG then locks bridge individuals with `sparqls/<ext>/enrichment_target.sparql`, writes those IRIs into `{ontology}_global_state.json`, pins the entity scope, and requires `init_memory` / `export_memory`. Publish copies `memory_<ontology>/{entity}.ttl` only — never the latest file in the main `memory/` directory. Generated MCP processes receive `TWA_MAIN_ONTOLOGY_NAME` as the pipeline-owned main ontology (the parent name, or `runtime.binding.upstream_ontology` when the entry domain is an extension). Setting that env to the extension package name collapses those scoped files onto `memory/`.

## How to run

From the repository root. You need `.env` for extraction models and medical vision.

A fresh run **deletes the whole scenario runtime** unless you pass `--resume`.

```powershell
python -m src.extraction_runtime ontosynthesis `
  --generation-run current `
  --input-dir scenarios/mops/datasets/eval30 `
  --test `
  --hash 0c57bac8
```

Official eval30 chemistry path: PDF → `pdf_conversion` → `tbox_slim` → extract. A fresh run wipes the runtime first, so conversion is not skipped.

The CLI mints `scenarios/<domain>/runs/<timestamp>_<tag>/pipeline.resolved.json` and `runtime/`. Run JSON holds topology only: `domain`, `input_dir`, `data_dir`, `steps`, `vision_*`, `generated_artifact_root`.

Reuse an existing resolved file:

```powershell
python -m src.extraction_runtime ontosynthesis --config scenarios/mops/runs/<run>/pipeline.resolved.json --test --resume
```

Templates without `meta_task_config`:

- `configs/scenarios/pipeline_mops.json`
- `configs/scenarios/pipeline_medical.json`

Extraction model names live in `configs/extraction_models.json` (mostly `gpt-4.1`). Medical vision uses `gpt-4o`.
