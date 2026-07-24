# mini_marie — Technical documentation

**mini_marie** is a lightweight runtime for querying **Typed World Assets (TWAs)** over knowledge graphs via **MCP (Model Context Protocol)** tools. Code is organized into three domain folders:

| Domain | Folder | Contents | MCP config |
|--------|--------|----------|------------|
| **Zaha** (buildings & cities) | [`zaha/`](zaha/) | `twa_city` (Bremen/KL), `sg_old` (Singapore) | `configs/twa_city.json`, `configs/sg_old.json` |
| **Marie** (chemistry demo) | [`marie/`](marie/) | `chemistry` (Blazegraph MQ1–48, 7 MCP servers) | chemistry entries in `configs/mini_marie_mcps.json` |
| **MOP/MOF** (frameworks & polyhedra) | [`mop_mof/`](mop_mof/) | `mof` (OntoMOFs), `mops` (local synthesis TTL) | `configs/mof_twa.json`, `configs/marie_twa.json` |

Cross-domain tooling (`kg_catalog/`, `kgqa/`, `cross_kg_competency/`, `competency_gui/`) sits at the `mini_marie/` root alongside shared row-algebra utilities.

Agents call MCP tools with **small row limits** for fast iteration. **Workflow engines** chain tools, record runs, and **replay offline** from SQLite caches at full scale.

**MCP servers (Cursor):** `mof-twa`, `twa-city`, `twa-mops`, `sg-old`, `kg-catalog`, `chemistry-*` — see `.cursor/mcp.json` or `configs/mini_marie_mcps.json`.

> **Import paths:** New code uses `mini_marie.zaha.*`, `mini_marie.marie.*`, `mini_marie.mop_mof.*`. Legacy paths (`mini_marie.chemistry`, `mini_marie.mof_case`, etc.) redirect automatically via `mini_marie/__init__.py`.

---

## Documentation map

| Document | Contents |
|----------|----------|
| [zaha/README.md](zaha/README.md) | Buildings: Bremen/KL + Singapore |
| [marie/README.md](marie/README.md) | Chemistry demo (MQ1–48) |
| [mop_mof/README.md](mop_mof/README.md) | MOF corpus + MOP synthesis |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Components, data flow, shared libraries |
| [docs/WORKFLOWS_AND_CACHING.md](docs/WORKFLOWS_AND_CACHING.md) | Workflow JSON DSL, probe/replay, cache tiers |
| [docs/MCP_AND_AGENTS.md](docs/MCP_AND_AGENTS.md) | MCP servers, Cursor/Docker setup |
| [mop_mof/mof/COMPETENCY_CACHING.md](mop_mof/mof/COMPETENCY_CACHING.md) | MOF competency probe → cache → replay |
| [zaha/twa_city/CITY_CACHING.md](zaha/twa_city/CITY_CACHING.md) | City SQLite cache & warm |
| [marie/chemistry/CHEMISTRY_CACHING.md](marie/chemistry/CHEMISTRY_CACHING.md) | Chemistry cache tiers |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Install, Docker, MCP, portable export |
| [docs/CACHE_STARTUP.md](docs/CACHE_STARTUP.md) | **Incremental cache warm-up (small steps)** |

---

## Deployment and caches

Development and deployment share the **same project root** (parent of this `mini_marie/` package). Configs, Docker, data, and scripts live beside the package — not in a separate deploy tree.

| Step | Command (from project root) |
|------|-----------------------------|
| Setup (Linux) | `./mini_marie/scripts/setup_linux.sh` |
| Verify | `./mini_marie/scripts/verify_install.sh` |
| Cache warm | `./mini_marie/scripts/warm_cache_steps.sh` |
| Portable export | `./mini_marie/scripts/package_portable.sh` |

Details: [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md), [docs/CACHE_STARTUP.md](docs/CACHE_STARTUP.md).

---

## Quick start

### Prerequisites

- Python **3.11+**
- From project root: `pip install -r requirements-mini-marie.txt` (or run `setup_linux.sh`)
- Network access for **mop_mof/mof** and **zaha/twa_city** (remote SPARQL)
- **mop_mof/mops** requires local RDF: `evaluation/data/merged_tll`

### Run an MCP server (stdio)

```bash
python -m mini_marie.mop_mof.mof.main          # MOF
python -m mini_marie.zaha.twa_city.main        # City buildings
python -m mini_marie.mop_mof.mops.main         # MOP synthesis
python -m mini_marie.zaha.sg_old.main          # Singapore
python -m mini_marie.marie.chemistry.ontospecies.main
```

### Run a workflow from CLI

```bash
python -m mini_marie.mop_mof.mof.run_competency_probe --workflow CQ06_TOPOLOGY_ZIF8
python -m mini_marie.zaha.twa_city.run_workflow --workflow top10_buildings_locations_bremen
python -m mini_marie.marie.chemistry.warm_chemistry_cache --comprehensive
```

### Persistent cache location

| Environment | Path |
|-------------|------|
| Local (default) | `<repo>/data/mini_marie_cache/` |
| Docker | `/app/data/mini_marie_cache/` (volume `mini_marie_data`) |

Override root with `MINI_MARIE_DATA_DIR`. See `mini_marie/cache_paths.py`.

---

## Package layout

```text
mini_marie/
├── zaha/                   # Buildings & city KGs
│   ├── twa_city/           # Bremen + Kaiserslautern (twa-city MCP)
│   ├── sg_old/             # Singapore legacy stack (sg-old MCP)
│   └── probe_zaha_stack.py
├── marie/                  # Chemistry demo
│   ├── chemistry/          # Blazegraph namespaces + 7 MCP servers
│   └── probe_*.py          # Marie/chemistry infrastructure probes
├── mop_mof/                # MOF + MOP (NOT buildings)
│   ├── mof/                # OntoMOFs (mof-twa MCP)
│   ├── mops/               # Local MOP synthesis (twa-mops MCP)
│   ├── marie_agent.py      # LLM agent for MOP queries
│   └── webapp/             # Flask UI for MOP agent
├── kg_catalog/             # Cross-domain catalog MCP
├── kgqa/                   # Multi-KG Q&A agent
├── cross_kg_competency/    # Cross-domain eval (XQ01–XQ10)
├── competency_gui/         # Streamlit explorer
├── cache_paths.py          # Shared: cache root resolution
├── row_filters.py          # Shared: row-algebra transforms
└── docs/                   # Architecture & workflow docs
```

---

## Core design pattern

All TWA domains share **execute → record → replay**:

1. **Online probe** — Run workflow with `LIMIT` 10. Writes `workflow_runs/*.json` or `competency_runs/*.json` including **`probed_sequence`**.
2. **Warm cache** — Optional batch fetch at **full tier** into SQLite facet tables.
3. **Offline replay** — Re-execute **`probed_sequence`** from cache + in-memory transforms only.

---

## Testing

```bash
python -m mini_marie.test_row_filters
python -m mini_marie.mop_mof.mof.test_competency_cache
python -m mini_marie.zaha.twa_city.test_city_cache
python -m mini_marie.marie.chemistry.test_chemistry_cache
```

---

## Relationship to the extraction pipeline

In the full repository, **mini_marie** is the runtime/query layer for TWAs; the extraction pipeline lives in `src/agents` and related folders at the **same project root**. See [docs/uml_mcp_kg_workflows.md](../docs/uml_mcp_kg_workflows.md).

To ship mini_marie alone, run `./mini_marie/scripts/package_portable.sh` — do not maintain a duplicate tree in git.
