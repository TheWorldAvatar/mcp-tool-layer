# Windows setup

Two scripts at the repo root:

| Script | What it does |
| --- | --- |
| `setup.cmd` | Creates `.venv`, installs runtime deps, checks `.env` / PDFs / scorer |
| `run.cmd` | Default 5-step pipeline: generate → MCP → extract → KG (`generic-strict`) → score |

You need Python 3.11+ on PATH. Everything else is copied in next to the clone (none of it is in git).

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

## 3. Eval PDFs

Copy the 30 chemistry papers and 30 medical papers:

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

## 5. Install

From the repo root:

```powershell
setup.cmd
```

Fix any `[WARN]` for empty keys, missing PDFs, or a missing scorer before running.

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
| `[FAIL] .venv is missing` | Run `setup.cmd` |
| empty `REMOTE_API_KEY` | Fill `.env` |
| missing PDFs | Names must match the table in §3 |
| scorer not found | Set `SCORER_REPO` |
| chemistry extract cannot start PubChem | Update to a commit that launches `src.mcp_servers.pubchem.main` (in-repo; no extra clone) |
