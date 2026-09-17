# One-click locked 1:1 run

This is the short path for the paper's **Pipeline vs OntoLogX** conditions.
Everything in this document is in English so a fresh clone can follow it.

`run.cmd` is a different entry: it **generates new MCP** with GPT-5 and runs
Pipeline **Minimal** only. Use **`run_locked.cmd`** when you want the
frozen MCP packs and an OntoLogX comparison.

Guidance names on `run_locked.cmd --protocol` are the paper labels:

| `--protocol` | Paper name | What the KG agent gets |
|---|---|---|
| `minimal` | **Minimal** | Occurrence and ownership instructions only |
| `graph-rules` | **Graph rules** | Those plus explicit construction recipes and the T-Box handbook |
| `kg-guidance` | **KG guidance** | Those plus the frozen human-engineered graph-construction guidance and the same T-Box handbook |

The engine still uses `generic-strict` / `generic-noprompt` / `with-prompt` internally. Those old ids remain accepted aliases.

## What git does not contain

| Artifact | In git? | How a new machine gets it |
|---|---|---|
| Eval PDFs (30 chemistry + SI, 30 OntoMed) | No | `data/eval_bundles/eval30_pdfs.zip` → unpack |
| Frozen MCP packs (`0908-fullpack-s*_newmcp`, `med-s1_newmcp`) | No (`generated/` is gitignored) | `data/eval_bundles/locked_mcp_packs.zip` → unpack |
| Extraction runtimes / hint ledgers | No (`scenarios/` is gitignored) | **Re-run extract from PDFs** (this is expected) |
| OntoLogX and Pipeline KG graphs | No | Re-run `run_locked.cmd` |
| Scoring gold JSON / medical CSV | Yes, `data/scorer_assets/` | — |
| Paper lists / filenames | Yes, `papers_eval30.json`, `papers_medical.json`, `data/eval_bundles/expected_files.json` | — |

Do **not** look for extraction git history. Ledgers are produced locally from
PDFs + a frozen MCP pack. That is the supported way to run.

The zip files themselves are also **not committed** (publisher PDFs, and
operative notes). Build them on a machine that already has the files, then
copy the zips.

## 1. One-time setup

```powershell
setup.cmd
copy .env.example .env
# edit .env: REMOTE_BASE_URL and REMOTE_API_KEY
```

## 2. Put the zip files here

Copy both zips into the clone, **this exact folder**:

```text
<data/eval_bundles/eval30_pdfs.zip>
<data/eval_bundles/locked_mcp_packs.zip>
```

Create the folder if it is missing. Names must match.

Then either:

```powershell
pack_eval_inputs.cmd unpack
```

or just start `run_locked.cmd` — it unpacks automatically when the zips are
present and the PDFs / MCP packs are missing.

### What unpack writes

| Zip member prefix | Lands at |
|---|---|
| `scenarios/mops/datasets/eval30/*.pdf` | chemistry papers + `*_si.pdf` |
| `scenarios/medical/datasets/eval30/*.pdf` | OntoMed operative-note PDFs |
| `generated/runs/0908-fullpack-s1_newmcp/` | chemistry MCP pack s1 |
| `generated/runs/0908-fullpack-s2_newmcp/` | pack s2 |
| `generated/runs/0908-fullpack-s3_newmcp/` | pack s3 |
| `generated/runs/0908-fullpack-kimi_newmcp/` | pack s4 (Kimi MCP) |
| `generated/runs/med-s1_newmcp/` | OntoMed MCP pack |

Chemistry PDF names are the DOI with `/` replaced by `_`, for example
`10.1002_anie.201811027.pdf`. OntoMed names are the paper-list stems, for
example `10062026 OPR10a.pdf`. The full filename list is
`data/eval_bundles/expected_files.json`.

### If you do not have the zips

On a machine that already has PDFs and `generated/runs/0908-fullpack-*`:

```powershell
pack_eval_inputs.cmd pack
```

That writes the two zips into `data/eval_bundles/`. Copy them to the new
machine. `--force` rebuilds zips that already exist:

```powershell
pack_eval_inputs.cmd pack --force
```

`pack_eval_inputs.cmd check` prints what is present.

Manual copy without zips is still valid: drop PDFs into the two `eval30`
folders and copy the five `generated/runs/...` pack directories.

## 3. Run one locked condition

```powershell
run_locked.cmd              # 1 paper, both domains, Minimal, Pipeline + OX
run_locked.cmd --check      # inputs only
run_locked.cmd --list       # protocols / packs / examples
run_locked.cmd 30           # full eval30
```

Useful combinations:

```powershell
run_locked.cmd --domain main --protocol minimal --cases 30
run_locked.cmd --domain main --protocol graph-rules --cases 30
run_locked.cmd --domain main --protocol kg-guidance --cases 30
run_locked.cmd --domain ontomed --protocol minimal --cases 30
run_locked.cmd --pack s4 --extract-model kimi --kg-model kimi --domain main --cases 30
run_locked.cmd --builder pipeline --domain main
run_locked.cmd --from-extract scenarios\mops\runs\<existing_extract_run>
```

`--cases 5` is a **smoke slice** of `papers_eval30.json` (first five hashes). It is **not** the official s1–s4 first-five set (`OFFICIAL5`), and it is **not** a substitute for the 30-paper SI table. OntoLogX still splits those five into official letter partners (often one paper per leftover pair). Do not treat a 5-paper micro-F1 as evidence that Pipeline or OX “moved”.

Extract runs once (`lkexs1`). Graph rules and KG guidance copy that ledger and only rebuild KG + OX.

Extract uses ``--until main_ontology_extractions``. Pipeline KG matches locked ``s1ka–o``: the run config has the guidance protocol, **steps are only** ``main_kg_building``, and each paper is one process. Do not pass ``--protocol`` on the KG child CLI — that flag rewrites the step list back to PDF→extract.

| Flag | Default | Meaning |
|---|---|---|
| `--protocol` | `minimal` | **Minimal** / **Graph rules** / **KG guidance** (see table above). |
| `--pack` | `s1` | Frozen chemistry MCP. `s2` / `s3` are the other gpt-4.1 packs. `s4` is Kimi MCP. |
| `--kg-model` | `gpt-4o` | Also `kimi`. |
| `--extract-model` | domain default | Chemistry `gpt-4.1`, OntoMed `gpt-5`, or `kimi`. |
| `--builder` | `both` | `pipeline`, `ox`, or both. OX still runs Pipeline KG first (token budget). |
| `--domain` | `both` | `main` (OntoSynthesis) and/or `ontomed`. |
| `--cases` | `1` | 1–30, official eval order from `papers_eval30.json`. |
| `--workers` | `5` | Max concurrent extract/KG **processes**. Each process: 1 paper, sequential. |
| `--from-extract` | unset | Reuse this extract run (copy into a new KG run for the chosen guidance). |

Replay OntoLogX only, after Pipeline KG is already complete:

```powershell
run_locked.cmd --domain main --protocol minimal --pack s1 --cases 30 --builder ox --workers 5 --from-extract scenarios\mops\runs\<lkexs1_run>
```

`--from-extract` must be the **extract** folder (`*_lkexs1`). The runner still finds the matching Pipeline KG traces (`*_lks1min`) for the OX token budget.

There is **no** matrix script that fires all 18 chemistry rows plus 4 OntoMed
rows in one process. Run one protocol × pack × model at a time.

Re-running the same protocol / pack / hashes reuses the last run tagged
`lk{pack}{s|n|w}` when extract and KG markers are already complete.

## 4. Why extraction is re-run

`scenarios/` is gitignored. A clone has no `iter3_hints_*.txt` and no
`responses/main_kg_building/*.trace.json`.

OntoLogX refuses to start without that Pipeline token budget. So the one-click
path is:

1. Unpack PDFs + frozen MCP (no LLM).
2. Extract from PDFs with the frozen pack (LLM; this **replaces** git-missing ledgers).
3. Pipeline KG under the chosen guidance (writes the traces OX needs).
4. OntoLogX `--hint-runs` that same Pipeline run, same protocol, seed 42.

That reproduces the **protocol**, not bit-identical F1 from a previous date.
Slim / top-entity / main ledgers are drawn live (same frozen ITER prompts).
Locked paper numbers stay on the original run folders (`ox_s1xha`,
`20260908_220850_s1p30`, …). If you already have those folders, pass
`--from-extract` instead of extracting again.

## 5. How chemistry jobs are split

Official September 2026 s1–s4:

| Stage | Papers per OS process | Pairing |
|---|---|---|
| Extract | 2 (then changed here to 1) | letter jobs |
| Pipeline KG | 1 (locked `s1ka` shape) | one hash per child |
| OntoLogX | **2** | official `ALL_GROUPS` **a–o** (not `papers_eval30.json` list order) |

This runner:

- Extract / Pipeline KG: **1 paper per process**, up to `--workers` processes.
- OntoLogX: **2 papers per process**, official partners (`0c57bac8+7ba809dd`, …). The CLI refuses `n>2` unless `TWA_OX_ALLOW_MULTIDOC=1`.
- Scoring: Reproduction engines via `score_four.py` (`--full --ignore --llm-synonyms` on steps; gold from `data/scorer_assets/full_ground_truth`).

A 5-paper OX job in **one** process is not this protocol. That is what
`TWA_OX_ALLOW_MULTIDOC` / a raw `--hash` list of five would do. Do not do that
for SI comparisons.

## 6. Interpreter

Official s1–s4 used conda env `mcp_layer` (CPython 3.11) and pinned
mcp / langchain / openai wheels. `run_locked.cmd` prefers

`%USERPROFILE%\AppData\Local\anaconda3\envs\mcp_layer\python.exe`

then `.venv` (must be 3.11). Override with `TWA_LOCKED_PYTHON`.
`setup.cmd` refuses a 3.13 venv. `requirements-runtime.txt` pins the stack.

## 7. Costs and extras

- Default `run_locked.cmd` is **1 paper** so a smoke test is cheap.
- Chemistry CBU still needs a licensed local CSD. Steps / chemicals /
  characterisation / OntoMed do not.
- OntoMed SHACL on a **new** OX run is the current full oracle, not the
  label-only stub used in the September 2026 locked medical OX packs.

## 8. Automated tests (no LLM)

How to run the unit tests, and what each file covers:
[docs/LOCKED_TESTS.md](LOCKED_TESTS.md).

```powershell
# from the clone root, same interpreter as locked runs
%USERPROFILE%\AppData\Local\anaconda3\envs\mcp_layer\python.exe -m pytest tests/test_locked_one_click.py tests/test_kg_no_contract.py tests/test_ccdc_subprocess_budget.py -q
```

## If something fails

| Message | Fix |
|---|---|
| `.venv is missing` | `setup.cmd` |
| empty `REMOTE_API_KEY` | fill `.env` |
| Eval PDFs missing | copy `eval30_pdfs.zip` to `data/eval_bundles/` |
| Frozen chemistry MCP pack missing | copy `locked_mcp_packs.zip` to `data/eval_bundles/` |
| No pipeline KG token budget | do not run OX against an extract-only folder; let this script run Pipeline KG first |
| CBU F1 empty | expected without CSD; see SETUP.md §5 |
