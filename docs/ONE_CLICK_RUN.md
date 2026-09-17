# One-click locked 1:1 run

This is the short path for the paper's **Pipeline vs OntoLogX** conditions.
Everything in this document is in English so a fresh clone can follow it.

`run.cmd` is a different entry: it **generates new MCP** with GPT-5 and runs
Pipeline `generic-strict` only. Use **`run_locked.cmd`** when you want the
frozen MCP packs and an OntoLogX comparison.

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
run_locked.cmd --domain main --protocol generic-strict --cases 30
run_locked.cmd --domain main --protocol generic-noprompt --cases 30
run_locked.cmd --domain main --protocol with-prompt --cases 30
run_locked.cmd --domain ontomed --protocol generic-strict --cases 30
run_locked.cmd --pack s4 --extract-model kimi --kg-model kimi --domain main --cases 30
run_locked.cmd --builder pipeline --domain main
run_locked.cmd --from-extract scenarios\mops\runs\<existing_pipeline_run>
```

| Flag | Default | Meaning |
|---|---|---|
| `--protocol` | `generic-strict` | Paper **Minimal**. `generic-noprompt` = Graph rules. `with-prompt` = KG guidance. |
| `--pack` | `s1` | Frozen chemistry MCP. `s2` / `s3` are the other gpt-4.1 packs. `s4` is Kimi MCP. |
| `--kg-model` | `gpt-4o` | Also `kimi`. |
| `--extract-model` | domain default | Chemistry `gpt-4.1`, OntoMed `gpt-5`, or `kimi`. |
| `--builder` | `both` | `pipeline`, `ox`, or both. OX still runs Pipeline KG first (token budget). |
| `--domain` | `both` | `main` (OntoSynthesis) and/or `ontomed`. |
| `--cases` | `1` | 1–30, official eval order from `papers_eval30.json`. |
| `--from-extract` | unset | Skip chemistry extract; resume KG / OX from that run. |

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
3. Pipeline KG under `--protocol` (writes the traces OX needs).
4. OntoLogX `--hint-runs` that same Pipeline run, same protocol, seed 42.

That reproduces the **protocol**, not bit-identical F1 from a previous date.
Locked paper numbers stay on the original run folders (`ox_s1xha`,
`20260908_220850_s1p30`, …). If you already have those folders, pass
`--from-extract` instead of extracting again.

## 5. Costs and extras

- Default `run_locked.cmd` is **1 paper** so a smoke test is cheap.
- Chemistry CBU still needs a licensed local CSD. Steps / chemicals /
  characterisation / OntoMed do not.
- OntoMed SHACL on a **new** OX run is the current full oracle, not the
  label-only stub used in the September 2026 locked medical OX packs.

## If something fails

| Message | Fix |
|---|---|
| `.venv is missing` | `setup.cmd` |
| empty `REMOTE_API_KEY` | fill `.env` |
| Eval PDFs missing | copy `eval30_pdfs.zip` to `data/eval_bundles/` |
| Frozen chemistry MCP pack missing | copy `locked_mcp_packs.zip` to `data/eval_bundles/` |
| No pipeline KG token budget | do not run OX against an extract-only folder; let this script run Pipeline KG first |
| CBU F1 empty | expected without CSD; see SETUP.md §5 |
