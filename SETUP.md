# Windows setup

Two scripts at the repo root:

| Script | What it does |
| --- | --- |
| `setup.cmd` | Creates `.venv`, installs runtime deps, checks `.env` / PDFs, clones scoring engines |
| `run_locked.cmd` | Frozen-MCP 1:1 run: unpack eval zips if needed, extract, Pipeline KG, OntoLogX |
| `pack_eval_inputs.cmd` | Pack or unpack `data/eval_bundles/*.zip` (PDFs + frozen MCP; not in git) |
| `run.cmd` | Default 5-step pipeline: generate → MCP → extract → KG (`generic-strict`) → score |

You need Python 3.11+ on PATH. **Git does not include** the eval PDFs or `.env` — copy those in yourself after clone. Scoring engines are cloned automatically. Licensed CCDC / CSD software is **not** part of setup; it is only required to finish chemistry **CBU** (see §5).

## 1. Clone

```powershell
git clone <this-repo-url> MCP-enhanced-MOPs-Extraction
cd MCP-enhanced-MOPs-Extraction
```

## 2. API keys (`.env`)

```powershell
copy .env.example .env
```

Edit `.env` and set:

```
REMOTE_BASE_URL=https://openrouter.ai/api/v1
REMOTE_API_KEY=...
```

Any OpenAI-compatible endpoint works. Leave `ROOT_DIR` unset.

Do not commit `.env`.

## 3. Eval PDFs and frozen MCP (not in this repo)

Git does **not** include PDFs, generated MCP packs, or extraction runtimes.
The supported way to move them between machines is two zip files. Full
layout, filenames, and unpack rules: [docs/ONE_CLICK_RUN.md](docs/ONE_CLICK_RUN.md).

Put the zips here, then unpack (or let `run_locked.cmd` unpack them):

```text
data\eval_bundles\eval30_pdfs.zip
data\eval_bundles\locked_mcp_packs.zip
```

```powershell
pack_eval_inputs.cmd unpack
```

On a machine that already has the files:

```powershell
pack_eval_inputs.cmd pack
```

If you copy files by hand instead of using the zips:

| What | Folder | Filename rule |
| --- | --- | --- |
| Chemistry PDFs | `scenarios\mops\datasets\eval30` | DOI with `/` replaced by `_`, e.g. `10.1002_anie.201811027.pdf` |
| OntoMed PDFs | `scenarios\medical\datasets\eval30` | Stem from the paper list, e.g. `10062026 OPR10a.pdf` |
| Chemistry MCP | `generated\runs\0908-fullpack-s1_newmcp` | frozen pack (s2 / s3 / kimi names in the doc) |
| OntoMed MCP | `generated\runs\med-s1_newmcp` | frozen pack |

Optional chemistry SI files are `*_si.pdf`. Extraction ledgers are **not**
shipped; `run_locked.cmd` extracts from the PDFs.

Paper order is `src/kg_building/ontologx/papers_eval30.json` and `papers_medical.json`.
`run.cmd` defaults to **10** cases; `run_locked.cmd` defaults to **1**.

## 4. Python venv and install

From the **repo root**, on Windows, with CPython 3.11+:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements-runtime.txt
.\.venv\Scripts\python.exe -m pip install -e .
```

That is **not** universal:

| Assumption | If it fails |
| --- | --- |
| `python` is 3.11+ on PATH | Official installer: `py -3.11 -m venv .venv`. Avoid the Microsoft Store `python` stub. |
| Windows layout `.venv\Scripts\` | macOS/Linux use `.venv/bin/python` — this guide is Windows-only. |
| PowerShell | `.\.venv\...` is required. In `cmd.exe`, `.venv\Scripts\python.exe` also works. |
| Working directory | Must be the clone root (`pyproject.toml` is here). |

`run.cmd` always calls `.\.venv\Scripts\python.exe`. Or skip the block and run `setup.cmd`, which probes `py -3.13` / `3.12` / `-3.11` then `python`, then installs, checks `.env` / PDFs, and clones scoring engines if needed.

## 5. CCDC MCP (needed only for CBU)

The CCDC MCP (`src.mcp_servers.ccdc.main`) talks to a **local, licensed** Cambridge Structural Database (CSD) install — the `ccdc` Python package, usually a conda env named `csd311`. `setup.cmd` does **not** install that software. It is not in this repo.

You need that install to **complete chemistry CBU** (chemical building units). The `mop_derivation` step fetches `.res` / `.cif` crystal files through `get_res_cif_file_by_ccdc`. Without CSD, CBU derivation cannot finish, and the CBU score stays empty or near zero.

**Everything else can run without it:** prompt generation, MCP compile, PDF conversion, extraction, KG, PubChem, websearch, OntoMed, and scoring of chemicals / steps / characterisation. A missing CSD env prints `[WARN] CSD python resolve failed` and the pipeline continues. Live CCDC name/DOI search also needs CSD; a small hardcoded MOP table can still resolve some deposition numbers.

If you have CSD installed, point the MCP at that env (not `.venv`):

```
CSD_PYTHON_EXE=C:\Users\<you>\AppData\Local\anaconda3\envs\csd311\python.exe
CSD_CONDA_ENV=csd311
```

Put those in `.env` or the process environment. Default lookup is `%USERPROFILE%\AppData\Local\anaconda3\envs\csd311\python.exe` when `CSD_PYTHON_EXE` is unset. Do not use `mcp_layer` or the repo venv for CSD.

## 6. Run

Locked paper-style 1:1 (frozen MCP, Pipeline + OntoLogX):

```powershell
run_locked.cmd --check
run_locked.cmd          # 1 case, both domains, generic-strict
run_locked.cmd 30
run_locked.cmd --list
```

Shipped generate-then-Pipeline path (new MCP, no OntoLogX):

```powershell
run.cmd          # 10 cases, both domains
run.cmd 1        # smoke test (1 case)
run.cmd 20       # 1–30
run.cmd --from-step extract
run.cmd --domain main
run.cmd --domain ontomed
```

`--from-step` starts at that step and continues through scoring (`generate` / `mcp` / `extract` / `kg` / `score`, or `1`–`5`).

Default parallelism is **5 workers** on every step: prompt authoring, MCP compile waves, paper extract/KG, TTL convert, and the four chemistry score modules. Override with `run.cmd --workers 3`. One paper still uses one worker.

Scores print at the end and are also written to `generated\ship_reports\`.

Chemistry steps scoring is locked: match steps by type, not list order.
Vessel is not a scoring switch — the committed gold under
`data/scorer_assets/full_ground_truth` has no vessel keys. OntoMed gold is
`data/scorer_assets/medical_cases_new_20260710_all30_corrected.csv`.

## Resume and dry-run

```powershell
run.cmd --dry-run 1
run.cmd --from-step kg --domain ontomed
```

Extraction **mints a new** run. To resume an unfinished extract you already started, use the extraction CLI with `--config` and `--resume` rather than `run.cmd` extract.

## If something fails

| Symptom | Check |
| --- | --- |
| `[FAIL] .venv is missing` | Run the venv block in §4, or `setup.cmd` |
| empty `REMOTE_API_KEY` | Fill `.env` |
| missing PDFs | PDFs are not in git. Copy them into the folders in §3, with those filenames. |
| scorer not found | `setup.cmd` / `run.cmd` clone scoring engines into `data\third_party_repos\`. Needs git. |
| chemistry extract cannot start PubChem | Update to a commit that launches `src.mcp_servers.pubchem.main` (in-repo; no extra clone) |
| `[WARN] CSD python resolve failed` / CBU score empty | Expected without licensed CSD. Other steps still run. To finish CBU, install CSD (`csd311` + `ccdc` package) and set `CSD_PYTHON_EXE` as in §5 |
