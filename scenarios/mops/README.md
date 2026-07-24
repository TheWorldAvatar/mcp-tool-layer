# MOP / OntoSynthesis scenario

| Dataset | Path | Contents |
|---------|------|----------|
| eval30 | `datasets/eval30/` | Up to 30 main+SI PDF pairs (see `datasets/manifest.json`) |

Runs: `runs/<yyyymmdd>_<dataset>_<tag>/` with `runtime/` and `evaluation/`.

```bash
python scripts/start_scenario_run.py --domain mops --dataset eval30 --tag gpt-4.1
```

Shared ontologies stay in `data/ontologies/`. Fill missing slots in `datasets/eval30/` using the DOI stems listed in `manifest.json`, then re-run the bootstrap helper or update the manifest statuses.
