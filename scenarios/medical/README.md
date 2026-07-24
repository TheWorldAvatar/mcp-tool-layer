# Medical scenario

| Dataset | Path | Contents |
|---------|------|----------|
| eval30 | `datasets/eval30/` | 30 OPR PDFs + `Ground truth.xlsx` |
| dev5 | `datasets/dev5/` | OP Bericht 1–5 (development) |

Runs: `runs/<yyyymmdd>_<dataset>_<tag>/` with `runtime/` (pipeline `data_dir`) and `evaluation/` (scoring reports).

```bash
python scripts/start_scenario_run.py --domain medical --dataset eval30 --tag gpt-4.1
```

Resources / schema pointers: `resources/README.md`.
