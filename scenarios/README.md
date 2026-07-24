# Scenario workspace

Separated **medical** and **mops** evaluation setups. Datasets are immutable inputs; each cross-model experiment gets its own run folder with runtime outputs and evaluation side-by-side.

## Layout

```text
scenarios/
  medical/
    datasets/eval30/     # 30 operative-report cases (+ Ground truth.xlsx)
    datasets/dev5/       # 5 development OP Bericht PDFs
    datasets/manifest.json
    runs/<yyyymmdd>_<dataset>_<tag>/{runtime,evaluation,...}
  mops/
    datasets/eval30/     # up to 30 main+SI PDF pairs (staged; some may be missing)
    datasets/manifest.json
    runs/<yyyymmdd>_<dataset>_<tag>/{runtime,evaluation,...}
```

## Start a run

```bash
# Prefers mcp_layer python when available
python scripts/start_scenario_run.py --domain medical --dataset eval30 --tag gpt-4.1
python scripts/start_scenario_run.py --domain medical --dataset dev5 --tag gpt-4.1
python scripts/start_scenario_run.py --domain mops --dataset eval30 --tag gpt-4.1
```

The script creates `scenarios/<domain>/runs/<yyyymmdd>_<dataset>_<tag>/`, writes `pipeline.resolved.json` + `run_meta.json`, and prints the `generic_main.py` command.

**Tag naming:** use the model or profile label for the experiment, e.g. `gpt-4.1`, `gpt-5.2`, `mixed_default` (when using the shared [`configs/extraction_models.json`](../configs/extraction_models.json) mix).

## Shared (not per-run)

| Asset | Location |
|-------|----------|
| OntoSynthesis / MOP T-Boxes & CCDC CSVs | `data/ontologies/` |
| Medical schemas | `medical_case/` (see also `scenarios/medical/resources/`) |
| Generated MCP + prompts | `ai_generated_contents*` ontology subtrees |
| Default model map | `configs/extraction_models.json` |

## Legacy paths (deprecated for new runs)

`data/`, `data_medical_new_cases/`, `raw_data/`, `raw_data_mop/`, `raw_data_new_medical/` remain for older experiments. Prefer `scenarios/` going forward.
