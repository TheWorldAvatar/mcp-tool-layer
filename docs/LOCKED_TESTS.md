# Locked 1:1 tests and how to run them

English-only notes for the frozen-MCP **Pipeline vs OntoLogX** path.
This is not the GPT-5 `run.cmd` generate-then-Pipeline path.

## What you are testing

Two layers:

1. **Unit tests** (this document). No OpenRouter calls. They lock job
   shape, frozen prompt hashes, pairing, and CSD opt-in.
2. **End-to-end locked runs** (`run_locked.cmd`). Those *do* call the
   LLM. Protocol and usage: [ONE_CLICK_RUN.md](ONE_CLICK_RUN.md).

SI tables stay on the original September 2026 folders
(`20260908_220850_s1p30`, `20260909_224749_ox_s1xh30`). A new locked run
reproduces the *protocol*, not bit-identical F1.

## Interpreter

Use the same Python as locked runs:

```powershell
cd <clone-root>

# official s1–s4 stack (preferred)
%USERPROFILE%\AppData\Local\anaconda3\envs\mcp_layer\python.exe -m pytest -q

# or a 3.11 .venv created by setup.cmd
.\.venv\Scripts\python.exe -m pytest -q
```

Do **not** use Python 3.13. `setup.cmd` refuses that venv; in-process
asyncio / MCP stdio deadlocks showed up there.

Install test extras if `pytest` is missing:

```powershell
python -m pip install pytest
python -m pip install -r requirements-runtime.txt
python -m pip install -e .
```

Working directory must be the clone root (`pyproject.toml` is here).

## Which test files to run

| File | What it locks | Needs unpacked s1 pack? |
|---|---|---|
| `tests/test_locked_one_click.py` | `run_locked` CLI, official OX letters a–o, 1 paper/process extract+KG, s1ka KG config, frozen ITER SHA-256, no prompt regeneration | One test skips if `generated/runs/0908-fullpack-s1_newmcp/prompts` is missing |
| `tests/test_kg_no_contract.py` | T-Box handbook resolution (`data/ontologies/ontosynthesis_parsed.md`) | No |
| `tests/test_ccdc_subprocess_budget.py` | Live CSD stays off unless `CSD_PYTHON_EXE` is a real file | No |
| `tests/test_extraction_runtime_paths.py` | MCP launcher filenames include the PID (no shared-file races) | No |
| `tests/test_locked_llm.py` | Seed / temperature pins | No |

Chemistry smoke (fast, no network if packs are absent):

```powershell
python -m pytest tests/test_locked_one_click.py tests/test_kg_no_contract.py tests/test_ccdc_subprocess_budget.py -q
```

Full repo tests (longer; some need local data):

```powershell
python -m pytest tests -q
```

One class:

```powershell
python -m pytest tests/test_locked_one_click.py::LockedRunnerTests -q
```

## What the locked one-click tests assert

Read `LockedRunnerTests` in `tests/test_locked_one_click.py`:

- `--protocol` paper names: `minimal` / `graph-rules` / `kg-guidance`
  (engine ids `generic-strict` / `generic-noprompt` / `with-prompt` still work).
- Extract and Pipeline KG: `EXTRACT_GROUP_SIZE = KG_GROUP_SIZE = 1`.
- OntoLogX chemistry: `OX_GROUP_SIZE = 2` and `S1_LETTER_GROUPS` equals
  official `ALL_GROUPS` (a=`0c57bac8+7ba809dd`, …). A `--cases 5` slice
  keeps those partners; missing mates become 1-paper leftover letters.
- KG child argv has `--config --resume`, **no** `--protocol` (that flag
  would rewrite steps back to PDF→extract).
- Extract argv points at `--generation-run …/0908-fullpack-s1_newmcp`
  and never calls `extraction_prompt_generation`.
- s1 ITER files match the official SHA-256 table in `scripts/eval_inputs.py`.

## End-to-end commands (LLM, costs money)

Prerequisites: [ONE_CLICK_RUN.md](ONE_CLICK_RUN.md) §§1–2 (`.env`, eval zips).

```powershell
# inputs only
run_locked.cmd --check
run_locked.cmd --list

# cheap smoke: 1 paper, both domains
run_locked.cmd

# SI-shaped chemistry Minimal (extract → Pipeline → OX)
run_locked.cmd --domain main --protocol minimal --pack s1 --cases 30 --workers 5

# Graph rules / KG guidance: reuse that extract (KG + OX + scores only)
run_locked.cmd --domain main --protocol graph-rules --pack s1 --cases 30 --workers 5 --from-extract scenarios\mops\runs\<stamp>_lkexs1
run_locked.cmd --domain main --protocol kg-guidance --pack s1 --cases 30 --workers 5 --from-extract scenarios\mops\runs\<stamp>_lkexs1

# OX only, reuse an extract that already finished
run_locked.cmd --domain main --protocol minimal --pack s1 --cases 30 --builder ox --workers 5 --from-extract scenarios\mops\runs\<stamp>_lkexs1
```

Full flag table and resume notes: [ONE_CLICK_RUN.md](ONE_CLICK_RUN.md) §3.

Outputs (gitignored under `scenarios/mops/runs/`):

| Tag | Meaning |
|---|---|
| `*_lkexs1` / `*_lkexs1a`… | Extract (merged / letter children) |
| `*_lks1min` / `*_lks1gr` / `*_lks1kg` | Pipeline KG for Minimal / Graph rules / KG guidance |
| `ox_lks1mina`…`o` (same for `gr` / `kg`) | Official two-paper OX letters |
| `ox_lks1min` / `ox_lks1gr` / `ox_lks1kg` | Merged OX + `scores/` |

Steps report: `…/scores/scoring_steps/_overall.md`.

## Things that look like tests but are not this protocol

| Action | Why it is wrong for SI |
|---|---|
| `run_locked.cmd --cases 5` as the “paper result” | First five of `papers_eval30.json`, not `OFFICIAL5`. Over-weights a few papers. |
| One OX process with five `--hash` values | Cross-document inventory and stub/full behaviour change. CLI blocks `n>2` unless `TWA_OX_ALLOW_MULTIDOC=1`. |
| `run.cmd` | Generates **new** MCP with GPT-5. Not the frozen 0908 pack. |
| Python 3.13 | Different asyncio / MCP lifetime than official `mcp_layer`. |
| Setting `CSD_PYTHON_EXE` by accident | Live CSD was off on the locked 2026-09-17 clone unless you opt in. |

## Scoring engines

`run_locked` overlays `data/scorer_assets/full_ground_truth` onto the
Reproduction (or cloned) scorer repo and runs convert + four modules.
Steps flags from `src/kg_building/ontologx/score_four.py`:

`--full --ignore --llm-synonyms --llm-synonym-model openai/gpt-5.6-sol`

`--skip-order` / `--no-vessel` are not passed; type matching and
vessel-free gold are pinned in the overlay/lock. Char / chem / CBU use
`--full` only.
