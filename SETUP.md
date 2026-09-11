# Windows setup

Two scripts at the repo root:

| Script | What it does |
| --- | --- |
| `setup.cmd` | Creates `.venv`, installs runtime deps, checks `.env` / PDFs / scorer |
| `run.cmd` | Default 5-step pipeline: generate → MCP → extract → KG (`generic-strict`) → score |

You need Python 3.11+ on PATH. **Git does not include** the eval PDFs, `.env`, or the scorer checkout — copy those in yourself after clone.

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

## 3. Eval PDFs (not in this repo)

The papers are **not committed**. A fresh clone has no PDFs; `setup.cmd` only creates empty folders. Copy the files yourself into the paths below (from a USB drive, another machine, or an existing checkout).

You need the 30 chemistry papers and 30 medical papers:

| Domain | Folder | Filename rule |
| --- | --- | --- |
| Chemistry | `scenarios\mops\datasets\eval30` | DOI with `/` replaced by `_`, e.g. `10.1002_anie.201811027.pdf` |
| OntoMed | `scenarios\medical\datasets\eval30` | Stem from the paper list, e.g. `10062026 OPR10a.pdf` |

Optional SI files are `*_si.pdf`. `run.cmd` defaults to **10** cases, so you need at least that many PDFs in each folder (up to 30).

Paper order is `src/kg_building/ontologx/papers_eval30.json` and `papers_medical.json`.

## 4. Scorer checkout (read-only)

Step 5 of `run.cmd` scores against gold in **`MCP-enhanced-MOPs-Extraction_Reproduction`**. Clone that repo somewhere and either:

- put it next to this clone (`..\MCP-enhanced-MOPs-Extraction_Reproduction`), or
- set `SCORER_REPO` in `.env` to the full path.

Do not edit that checkout. Gold CSV, merge/conversion, and `evaluation.scoring_*` live there; this repo writes scores under its own `scenarios\...\runs\`.

## 5. Python venv and install

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

`run.cmd` always calls `.\.venv\Scripts\python.exe`. Or skip the block and run `setup.cmd`, which probes `py -3.13` / `3.12` / `-3.11` then `python`, then installs and checks `.env` / PDFs / scorer.

## 6. Run

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

## Resume and dry-run

```powershell
run.cmd --dry-run 1
run.cmd --from-step kg --domain ontomed
```

Extraction **mints a new** run. To resume an unfinished extract you already started, use the extraction CLI with `--config` and `--resume` rather than `run.cmd` extract.

## If something fails

| Symptom | Check |
| --- | --- |
| `[FAIL] .venv is missing` | Run the venv block in §5, or `setup.cmd` |
| empty `REMOTE_API_KEY` | Fill `.env` |
| missing PDFs | PDFs are not in git. Copy them into the folders in §3, with those filenames. |
| scorer not found | Set `SCORER_REPO` |
| chemistry extract cannot start PubChem | Update to a commit that launches `src.mcp_servers.pubchem.main` (in-repo; no extra clone) |
