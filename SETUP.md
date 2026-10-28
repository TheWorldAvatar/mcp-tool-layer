# Windows setup

Two scripts at the repo root:

| Script | What it does |
| --- | --- |
| `setup.cmd` | Creates `.venv`, installs runtime deps, checks `.env` / PDFs, clones scoring engines |
| `run_locked.cmd` | Frozen-MCP 1:1 run: unpack eval zips if needed, extract, Pipeline KG, OntoLogX |
| `pack_eval_inputs.cmd` | Pack or unpack `data/eval_bundles/*.zip` (PDFs + frozen MCP; not in git) |
| `run.cmd` | Default 5-step pipeline: generate → MCP → extract → KG (`generic-strict`) → score |

Locked s1–s4 needs **Python 3.11** (conda `mcp_layer` or a 3.11 `.venv`). **Git does not include** the eval PDFs or `.env` — copy those in yourself after clone. Unit tests: [docs/LOCKED_TESTS.md](docs/LOCKED_TESTS.md). Scoring engines are cloned automatically. Licensed CCDC / CSD software is **not** part of setup; it is only required to finish chemistry **CBU** (see §5).

## 1. Clone

```powershell
git clone https://github.com/TheWorldAvatar/mcp-tool-layer MCP-enhanced-MOPs-Extraction
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

### Benchmark PDFs from the shared archive

The shared folder currently holds one file, `eval30_chemistry_and_ontomed_pdfs.zip` (about 114 MiB):

https://www.dropbox.com/scl/fo/783ms8i0chfxmp96kou7y/AL-EuxEuIhHlP6nzLN_Xyi8?rlkey=xy24kxrnntfpisbjsb9u3dk84&dl=0

After downloading and extracting that archive locally, its top level is `chemistry/` and `ontomed/`. Copy the PDF files inside `chemistry/` into `scenarios\mops\datasets\eval30\`, and the PDF files inside `ontomed/` into `scenarios\medical\datasets\eval30\`. Create those destination folders if they are absent. Copy the files themselves so `eval30` contains the PDFs directly.

Checked on 28 October 2026 against `data/eval_bundles/expected_files.json`: 60 chemistry PDFs (30 papers and 30 `*_si.pdf` files) and 30 OntoMed PDFs. The filename sets match that manifest, and the archive has no further nested directories. With those files in place, `run_locked.cmd --check --cases 30` reported that the required PDF files are present.

The automated bundle `data\eval_bundles\eval30_pdfs.zip` is a separate archive. Its members already use the `scenarios/mops/datasets/eval30/` and `scenarios/medical/datasets/eval30/` paths, and `pack_eval_inputs.cmd unpack` reads that layout. Keep the Dropbox file under its existing name and follow the manual placement above.

That Dropbox folder does not contain `locked_mcp_packs.zip`. The same `--check --cases 30` run still reported `generated\runs\0908-fullpack-s1_newmcp` and `generated\runs\med-s1_newmcp` as missing. PDF placement and a complete locked run are separate steps. Articles and operative notes stay under their own copyright and clinical data-sharing terms. A public download link, permission to redistribute the files, and the ability to finish the historical locked run are three separate questions.

## 4. Python venv and install

Core Python, the CLI, and the offline tests are ordinary CPU desktop work. LLM calls go to the remote API in `.env`. No dedicated GPU is required for the local Python steps. Licensed CCDC/CSD is not part of this install; see §5.

Measured on 28 October 2026 on a Windows desktop (build 26200) on the University of Cambridge wired network. The adapter was a Realtek PCIe 2.5GbE controller with a 2.5 Gbit/s link. A `curl` download of 50 MB from Cloudflare completed in 0.37 s (about 1.0 Gbit/s). The Dropbox archive above transferred more slowly, at about 97 Mbit/s. No CCDC/CSD software was installed.

Use CPython 3.11. `setup.cmd` accepts only a 3.11 virtual environment for locked runs. Python 3.11.9 was installed with the user-scope winget package `Python.Python.3.11` (about 28 s). Install the locked stack from `requirements-runtime.txt`. That file pins `fastmcp==2.10.1` and `pydantic==2.11.7`. fastmcp 2.10.1 requires `pydantic>=2.11.7`, so 2.11.7 is the lowest pin that installs with the rest of this file. With 3.11.9 already present, a fresh `.venv`, `pip install --upgrade pip`, `pip install -r requirements-runtime.txt`, and `pip install -e .` finished in 47.6 s: the venv took 5.0 s, the pip upgrade had finished by 8.0 s, the requirements install by 42.8 s, and the editable install by 47.6 s. Most wheels were already in the local pip cache, so a first download of the same set can take longer.

From the **repo root**, in a new terminal after Python is installed (the installer updates `PATH` for new terminals):

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements-runtime.txt
.\.venv\Scripts\python.exe -m pip install -e .
```

`py -3.11` is the selector `setup.cmd` tries first. When more than one Python is installed, `py` with no version can select another one; this command still builds a 3.11 `.venv`. `setup.cmd` exits unless that `.venv` is 3.11. The same four commands are what `setup.cmd` runs for the virtual environment. The Windows Store `python` alias is a different program.

That is **not** universal:

| Assumption | If it fails |
| --- | --- |
| `py -3.11` runs | Open a new terminal after the user-scope install. The interpreter is `%LOCALAPPDATA%\Programs\Python\Python311\python.exe`. |
| Windows layout `.venv\Scripts\` | macOS/Linux use `.venv/bin/python` — this guide is Windows-only. |
| PowerShell | `.\.venv\...` is required. In `cmd.exe`, `.venv\Scripts\python.exe` also works. |
| Working directory | Must be the clone root (`pyproject.toml` is here). |

`setup.cmd` probes `py -3.11` first (official s1–s4 is conda `mcp_layer` / CPython 3.11), then exits unless `.venv` is 3.11.

## 5. CCDC MCP (needed only for CBU)

The CCDC MCP (`src.mcp_servers.ccdc.main`) can answer a small hardcoded MOP table without licensed software. **Live** Cambridge Structural Database (CSD) access — the `ccdc` Python package, usually a conda env named `csd311` — is **opt-in**. `setup.cmd` does **not** install that software. A fresh clone does **not** probe `%USERPROFILE%\...\envs\csd311\python.exe` even if that env exists on the machine.

You need that install to **complete chemistry CBU** (chemical building units). The `mop_derivation` step fetches `.res` / `.cif` crystal files through `get_res_cif_file_by_ccdc`. Without `CSD_PYTHON_EXE`, CBU derivation cannot finish, and the CBU score stays empty or near zero.

**Everything else can run without it:** prompt generation, MCP compile, PDF conversion, extraction, KG, PubChem, websearch, OntoMed, and scoring of chemicals / steps / characterisation. Unset `CSD_PYTHON_EXE` prints `[WARN] Live CSD disabled (CSD_PYTHON_EXE unset); CCDC MCP is hardcoded-only` and continues.

If you have CSD installed and want live search / CIF fetch, point the MCP at that env (not `.venv`):

```
CSD_PYTHON_EXE=C:\Users\<you>\AppData\Local\anaconda3\envs\csd311\python.exe
CSD_CONDA_ENV=csd311
```

Put those in `.env` or the process environment. Leave them commented out on a new clone. Do not use `mcp_layer` or the repo venv for CSD.

## 6. Run

Locked paper-style 1:1 (frozen MCP, Pipeline + OntoLogX). One-click entry is
`run_locked.cmd`. Chemistry guidances: `--protocol minimal` / `graph-rules` /
`kg-guidance`. Extract once, then reuse it for the other two. Full commands:
[docs/ONE_CLICK_RUN.md](docs/ONE_CLICK_RUN.md).

```powershell
run_locked.cmd --check
run_locked.cmd          # 1 case, both domains, Minimal
run_locked.cmd --list
run_locked.cmd --domain main --protocol minimal --pack s1 --cases 30 --workers 5
run_locked.cmd --domain main --protocol graph-rules --pack s1 --cases 30 --workers 5 --from-extract scenarios\mops\runs\<stamp>_lkexs1
run_locked.cmd --domain main --protocol kg-guidance --pack s1 --cases 30 --workers 5 --from-extract scenarios\mops\runs\<stamp>_lkexs1
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

### What successful execution produces

`run.cmd` and `run_locked.cmd` write score reports under `generated\ship_reports\`. Locked runs also write extraction and KG outputs under `scenarios\mops\runs\` and `scenarios\medical\runs\`. Both `generated/` and `scenarios/` are gitignored. `run_locked.cmd --check` prints input status and does not call an LLM. These are output locations. A new LLM run follows the protocol; it does not guarantee the same F1 numbers as the historical paper runs. No end-to-end LLM demo time was measured here.

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
| missing PDFs | PDFs are not in git. Copy `eval30_pdfs.zip` into `data\eval_bundles\` or see [docs/ONE_CLICK_RUN.md](docs/ONE_CLICK_RUN.md). |
| scorer not found | `setup.cmd` / `run.cmd` clone scoring engines into `data\third_party_repos\`. Needs git. |
| chemistry extract cannot start PubChem | Update to a commit that launches `src.mcp_servers.pubchem.main` (in-repo; no extra clone) |
| `[WARN] Live CSD disabled` / CBU score empty | Expected on a fresh clone. Other steps still run. To finish CBU, install CSD (`csd311` + `ccdc` package) and set `CSD_PYTHON_EXE` as in §5 |
