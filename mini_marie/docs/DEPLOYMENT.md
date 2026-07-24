# mini_marie deployment guide

This document covers **development and deployment** for mini_marie in one place. There is no separate deploy tree: you work from the **project root** (the directory that contains the `mini_marie/` Python package, plus `configs/`, `docker/`, etc.).

> Step-by-step cache warm-up: **[CACHE_STARTUP.md](CACHE_STARTUP.md)**

---

## 1. Project layout

**Project root** = parent of the `mini_marie/` package (`mini_marie.cache_paths.repo_root()`).

```text
<project_root>/
├── mini_marie/              # Python package + docs + scripts
│   ├── docs/
│   │   ├── DEPLOYMENT.md    # this file
│   │   └── CACHE_STARTUP.md
│   └── scripts/             # setup, verify, cache warm helpers
├── configs/                 # MCP JSON (mini_marie subset)
├── models/                  # Agent base classes (KGQA / MARIE)
├── src/utils/               # Shared logger
├── evaluation/data/merged_tll/   # Local MOP RDF
├── data/                    # SQLite caches (runtime)
├── docker/
├── Dockerfile
├── docker-compose.yml
└── requirements-mini-marie.txt
```

In the full MCP-enhanced repository, `<project_root>` is the repository root. The extraction pipeline (`src/agents`, etc.) lives alongside mini_marie in the same root — they share configs and data paths but are separate concerns.

---

## 2. What mini_marie provides

| Domain | MCP name | Module | Data source |
|--------|----------|--------|-------------|
| MOF | `mof-twa` | `mini_marie.mop_mof.mof.main` | Remote OntoMOFs SPARQL |
| City buildings | `twa-city` | `mini_marie.zaha.twa_city.main` | Bremen / KL Ontop |
| MOP synthesis | `twa-mops` | `mini_marie.mop_mof.mops.main` | Local `evaluation/data/merged_tll` |
| Singapore | `sg-old` | `mini_marie.zaha.sg_old.main` | sg-old SPARQL |
| Chemistry demo | `chemistry-*` (7) | `mini_marie.marie.chemistry.*.main` | Blazegraph |
| Catalog | `kg-catalog` | `mini_marie.kg_catalog.main` | Metadata |

Optional GUIs: Competency GUI (:8501), KGQA GUI (:8502, needs LLM).

---

## 3. Requirements

- Python **3.11+** (Docker image: 3.12)
- HTTPS to remote SPARQL endpoints (MOF, city, chemistry, sg-old)
- ≥ 2 GB disk for caches
- Docker Engine + Compose v2 (optional)
- OpenAI-compatible API (optional, for KGQA / MARIE agent)

---

## 4. Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `PYTHONPATH` | *(set to project root)* | Import `mini_marie`, `models`, `src` |
| `MINI_MARIE_DATA_DIR` | `./data` | Cache root |
| `MINI_MARIE_MERGED_TLL_DIR` | `./evaluation/data/merged_tll` | MOP RDF override |
| `REMOTE_BASE_URL` / `REMOTE_API_KEY` | — | LLM for KGQA / MARIE |

Always run commands with **current directory = project root** and `export PYTHONPATH="$(pwd)"`.

---

## 5. Local setup

### Linux

```bash
cd /path/to/project_root
chmod +x mini_marie/scripts/*.sh
./mini_marie/scripts/setup_linux.sh
source .venv/bin/activate
export PYTHONPATH="$(pwd)"
./mini_marie/scripts/verify_install.sh
```

Optional: `INSTALL_GUI=1` or `INSTALL_KGQA=1` before `setup_linux.sh`.

### Windows

```powershell
cd C:\path\to\project_root
.\mini_marie\scripts\setup_windows.ps1
$env:PYTHONPATH = (Get-Location).Path
```

### Layout check (no Python)

```bash
./mini_marie/scripts/check_layout.sh
```

---

## 6. Cache warm-up

Offline replay needs warmed SQLite caches. Work in **small steps** — see **[CACHE_STARTUP.md](CACHE_STARTUP.md)**.

```bash
./mini_marie/scripts/warm_cache_steps.sh city
```

Caches: `data/mini_marie_cache/`

---

## 7. Docker

```bash
cd /path/to/project_root
./mini_marie/scripts/docker_build.sh
docker compose run --rm -i mof-twa
docker compose --profile gui up competency-gui
```

Persistent volume: `mini_marie_data` → `/app/data`.

Services `twa-mops` and `sg-old` are defined in `docker-compose.yml`. MOP RDF is mounted from `./evaluation/data/merged_tll`.

See [../../docker/README.md](../../docker/README.md).

---

## 8. Cursor MCP

Local Python example (paths = your project root):

```json
{
  "mcpServers": {
    "mof-twa": {
      "command": "/path/to/project_root/.venv/bin/python",
      "args": ["-m", "mini_marie.mop_mof.mof.main"],
      "cwd": "/path/to/project_root",
      "env": { "PYTHONPATH": "/path/to/project_root" }
    }
  }
}
```

Full list: `configs/mini_marie_mcps.json`. Docker mode: `docker/mcp.json.example`.

---

## 9. Portable export (copy to another machine)

Do **not** maintain a second copy inside the repo. Export on demand:

```bash
./mini_marie/scripts/package_portable.sh
# → dist/mini_marie_portable/
```

Copy `dist/mini_marie_portable/` to the target host and run `./mini_marie/scripts/setup_linux.sh` there. Same docs and scripts apply.

---

## 10. Troubleshooting

| Symptom | Fix |
|---------|-----|
| `ModuleNotFoundError: mini_marie` | `export PYTHONPATH=$(pwd)` from project root |
| `merged_tll` not found | Ensure `evaluation/data/merged_tll` exists |
| Empty offline replay | [CACHE_STARTUP.md](CACHE_STARTUP.md) |
| MCP exits immediately | Use stdio (`docker compose run -i` or Cursor) |

Logs: `data/log/agent.log`

---

## 11. Related docs

| Document | Topic |
|----------|-------|
| [../README.md](../README.md) | Package overview |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Components |
| [MCP_AND_AGENTS.md](MCP_AND_AGENTS.md) | MCP setup |
| [WORKFLOWS_AND_CACHING.md](WORKFLOWS_AND_CACHING.md) | Workflow DSL |
| [CACHE_STARTUP.md](CACHE_STARTUP.md) | Incremental cache warm |

---

## Recommended sequence

1. `./mini_marie/scripts/setup_linux.sh`
2. `./mini_marie/scripts/verify_install.sh`
3. Configure Cursor MCP
4. `./mini_marie/scripts/warm_cache_steps.sh` (or [CACHE_STARTUP.md](CACHE_STARTUP.md))
5. Run workflows / GUIs
