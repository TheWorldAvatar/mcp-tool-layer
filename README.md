# Extraction prompt generation

Generate extraction prompts and SPARQL from **T-Box + one human domain
config**. The default model is `gpt-5`; domain config `models.*` can name
other models. The configured model writes the full (thick) prompt text.
Companion `*.materializable.inc` files and SPARQL are compiled, not
authored by the model. MCP scripts are produced by a separate generation
chain.

This repository keeps only that path for:

- OntoSynthesis (`complex_main`)
- OntoMOPs / OntoSpecies (`simple_extension`)
- OntoMed / medical (`simple_main`)

Do not commit anything under `generated/`. That directory is created by the
generator and must stay untracked.

Paths are resolved from the repository root (the directory that contains
`pyproject.toml` and `configs/domains/`), not from the current working
directory. You can run the CLI from any folder.

## Windows quick start

See [SETUP.md](SETUP.md) for `.env`, PDFs, `setup.cmd`, and `run.cmd`.
For the frozen-MCP Pipeline vs OntoLogX path (eval zips, no extraction in git),
see [docs/ONE_CLICK_RUN.md](docs/ONE_CLICK_RUN.md) and `run_locked.cmd`.
Licensed CCDC / CSD software is optional: you need it only to finish chemistry CBU; the rest of the pipeline runs without it.

## Prerequisites

1. Python 3.11+
2. Install generation dependencies from this repository:

```powershell
pip install -r requirements-generation.txt
pip install -e .
```

After `pip install -e .` you can also run `extraction-prompt-generation ontosynthesis`
from any directory. Without the editable install, use
`python -m src.extraction_prompt_generation` with this repository on `PYTHONPATH`
(the usual way is to start the command from the clone root).

3. Copy `.env.example` to `.env` and set `REMOTE_BASE_URL` plus `REMOTE_API_KEY`.
   Do not set `ROOT_DIR` unless you need to override the automatic clone
   discovery.

## Generate one domain

The only required argument is the ontology name. Domain config, T-Boxes,
and `generated/` are inferred from the repository:

```powershell
python -m src.extraction_prompt_generation ontosynthesis
python -m src.extraction_prompt_generation ontomops
python -m src.extraction_prompt_generation ontospecies
python -m src.extraction_prompt_generation medical
```

Each campaign writes a separate package under
`generated/runs/<YYYYMMDD_HHMMSS>_<tag>/`. Do not write two models or two
retries into the same tree. Extensions inherit the parent top entity from
**that same batch**; generate OntoSyn first, then ontomops / ontospecies.

```powershell
python -m src.extraction_prompt_generation --all-domains --tag gpt5_r1 --model gpt-5
python -m src.extraction_prompt_generation ontomops --tag gpt5_r1
```

`--tag` reuses the unique existing run with that suffix, or mints a new one.
`generated/current.json` points at the active batch. Parallel campaigns should
pass `--no-current`. The extraction runtime reads `current.json`, or
`--generation-run` / `--generated-root`.

Optional overrides:

| Flag | Default | When to set |
| --- | --- | --- |
| `--domain-config` | `configs/domains/<ontology>.json` | The config is not in the conventional place |
| `--tag` | mint `*_gen` or reuse `current.json` | Separate one model / retry from another |
| `--model` | domain `models.*` (usually `gpt-5`) | Override planning + authoring + repair |
| `--output-root` | `generated/runs/<stamp>_<tag>/` | Continue a known package path |
| `--generation-run` | unset | Existing run id, tag, `current`, or `legacy` |
| `--all-domains` | off | OntoSyn, then extensions, then medical |
| `--stage` | `all` | `prompts` = extraction prompts; `context` = planning |
| `--workers` | `5` | Change concurrent model authoring calls |
| `--json` | off | Print a machine-readable summary |

```powershell
python -m src.extraction_prompt_generation ontosynthesis --stage context
python -m src.extraction_prompt_generation --domain-config configs/domains/medical.json
```

Each run:

1. Plans the top class or extension focus (default `gpt-5`) and assigns iteration ownership.
2. The configured model judges which primary-T-Box classes are reusable and at what scope.
3. Creates empty `.md` slots, then the configured model writes the extraction prompts.
4. Compiles `*.materializable.inc`, `sparqls/<ontology>/`, and `iterations/<ontology>/iterations.json`.

Inspect `generated/runs/<id>/reports/<ontology>/generation_report.json`.

### Stage shortcuts

| `--stage` | Writes |
| --- | --- |
| `all` | Context, then extraction prompts + SPARQL |
| `prompts` | Extraction prompts + SPARQL + iterations |
| `context` | Planning / contracts only (model calls for top-class / focus planning and class-reuse judgment) |

See `src/extraction_prompt_generation/generate/extraction_prompts/README.md`.
KG-building prompts and ONEPASS fragments are not generated.

## What each domain writes

| Domain | Profile | Prompts | SPARQL |
| --- | --- | --- | --- |
| ontosynthesis | `complex_main` | `EXTRACTION_ITER_{1,2,3,4}`, `PRE_EXTRACTION_ITER_3` | `top_entity_parsing.sparql` for `ChemicalSynthesis` |
| ontomops | `simple_extension` | `EXTRACTION_ITER_1` | inherited-root list SPARQL + `enrichment_target.sparql` |
| ontospecies | `simple_extension` | `EXTRACTION_ITER_2` | same + `enrichment_target.sparql` |
| medical | `simple_main` | `EXTRACTION_ITER_1`, `EXTRACTION_ITER_2` | `top_entity_parsing.sparql` for `MedicalCase` |

`*.materializable.inc` is compiled next to extraction / pre-extraction prompts
(except main-ontology ITER 1). Do not edit `.inc` files by hand.

## Class reuse policy

Default generation starts from domain config + T-Box only. After
deterministic iteration ownership, the compiler asks GPT-5 for a class-reuse
policy and uses the first valid trial. `reuse_policy.path` remains an
optional override, not a required input. Do not check generated policy
files in.

## Config field reference

See [docs/CONFIGS.md](docs/CONFIGS.md) for every human field and which
files the generator writes.

## Layout

```
configs/domains/     human domain configs
configs/mcp/         human MCP set JSON (tool purposes + launch stubs)
data/ontologies/     human T-Boxes
src/extraction_prompt_generation/   generator (see that folder's README.md)
generated/           untracked home: current.json + runs/<batch>/ packages
```

Package layout and per-folder roles:
[`src/extraction_prompt_generation/README.md`](src/extraction_prompt_generation/README.md).
