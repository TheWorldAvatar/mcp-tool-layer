# MCP servers and agents

## MCP server summary

All servers use [FastMCP](https://github.com/jlowin/fastmcp) with **stdio** transport.

| Server name | Module | Config file | Primary endpoint |
|-------------|--------|-------------|------------------|
| `mops-twa` | `mini_marie.mop_mof.mops.main` | `configs/marie_twa.json` | Local RDF |
| `mof-twa` | `mini_marie.mop_mof.mof.main` | `configs/mof_twa.json` | OntoMOFs SPARQL |
| `twa-city` | `mini_marie.zaha.twa_city.main` | `configs/twa_city.json` | Bremen / KL Ontop |

### Start locally

```bash
python -m mini_marie.mop_mof.mof.main
python -m mini_marie.zaha.twa_city.main
python -m mini_marie.mop_mof.mops.main
```

### Cursor configuration

Example entry (`configs/mof_twa.json`):

```json
{
  "mof-twa": {
    "command": "python",
    "args": ["-m", "mini_marie.mop_mof.mof.main"],
    "transport": "stdio"
  }
}
```

Copy into `.cursor/mcp.json` and reload MCP. Use absolute `command` path on Windows if `python` is not on PATH.

### Docker

See [docker/README.md](../../docker/README.md). Image runs both `mof-twa` and `twa-city`; cache volume `mini_marie_data` maps to `/app/data`.

---

## `mof-twa` tool categories

Defined in `mof_case/main.py` with `@mcp.tool` and an `@mcp.prompt` instruction block.

### Corpus / Tobassco

| Tool | Description |
|------|-------------|
| `get_mof_total_count` | Total MOF count |
| `get_source_database_stats` | Counts per source DB |
| `get_tobassco_co2_coverage` | Tobassco MOFs with CO₂ data |
| `get_tobassco_co2_uptake_stats` | Avg/min/max uptake |
| `get_top_tobassco_co2_uptake` | Top uptake (seed query) |
| `get_top_tobassco_co2_valid_pore_geometry` | Top with valid PLD/LCD |
| `get_large_pore_co2_candidates` | Large pore candidates |
| `get_tobassco_topology_counts` | Counts by RCSR topology |
| `get_tobassco_mofs_by_topology` | Filter by topology |
| `get_tobassco_mofs_by_metal_node` | Filter by metal-node SMILES |
| `get_mofs_by_sourcedb` | Top MOFs from a source DB |
| `lookup_mof_by_mofid_fragment` | MOFid substring search |
| `get_mof_properties_by_mofid_fragment` | Properties for first match |

### Competency (named MOFs)

See [COMPETENCY_COVERAGE.md](../mof_case/COMPETENCY_COVERAGE.md) for question→tool mapping. Examples:

- `get_pld_stats_by_mof_name`, `get_mofs_by_metal`, `get_synthesis_by_mof_name`
- `get_mof_identity_by_name`, `get_mofs_with_same_topology_as`
- `get_linkers_by_mof_name`, `get_pore_metrics_by_mof_name`, `get_mofs_by_gsa_min`

### Workflow MCP tools

- `list_workflows` — Tab-separated catalog
- `run_workflow_online` — Probe with LIMIT 10
- `replay_workflow_offline` — Full-scale replay from `recording_path`

Competency-specific probe/replay may also be invoked via CLI (`run_competency_probe`, `replay_competency_offline`).

**SPARQL endpoint:** `http://68.183.227.15:3840/ontop/sparql/`  
**Prefix:** `mofs: <https://www.theworldavatar.com/kg/ontomofs_vkg/>`

---

## `twa-city` tools

Building-centric atomics in `twa_city_operations.py`:

- Class / predicate discovery (`list_classes`, predicate counts)
- `list_buildings_with_height`, `fetch_building_locations`
- Height stats, usage types, GeoSPARQL samples

Workflow tools mirror MOF: `run_workflow_online`, `replay_workflow_offline`, `list_workflows`.

City-specific warm: `python -m mini_marie.zaha.twa_city.warm_city_cache --city bremen`

Schema reference: [BUILDING_SCHEMA.md](../twa_city/BUILDING_SCHEMA.md)

---

## `mops-twa` tools

Local MOP synthesis graph:

- List MOPs, syntheses, steps, properties
- Workflow catalog for `list_mops_catalog`, `synthesis_profile`

Requires `evaluation/data/merged_tll` (see parent repo bootstrap).

MARIE agent config: `configs/marie_twa.json` → `mini_marie.mop_mof.mops.main`.

---

## MarieAgent (`marie_agent.py`)

**MARIE** (MOPs Analysis and Research Intelligence Engine) wraps `BaseAgent` with the **mops-twa** MCP server for natural-language Q&A over MOP synthesis.

```python
from mini_marie.mop_mof.marie_agent import MarieAgent

agent = MarieAgent(model_name="gpt-4o-mini", remote_model=True)
answer, meta = await agent.ask("What is the synthesis route for VMOP-17?")
```

Configuration path: `configs/marie_twa.json` (relative to repo root).

Requires LLM credentials (`.env` / `ModelConfig`) as documented in the parent [README.md](../../README.md).

### Demo / test

```bash
python -m mini_marie.test_marie
python mini_marie/marie_agent.py   # if __main__ demo present
```

---

## Flask webapp (`webapp/`)

Chat-style UI over **MarieAgent** (MOPs domain only).

```bash
# Linux/macOS
mini_marie/webapp/run.sh

# Windows
mini_marie\webapp\run.bat
```

Features:

- Session-based last-turn memory
- Background asyncio loop (avoids per-request loop teardown)
- JSON API for question/answer

Templates: `webapp/templates/`. Not used for MOF/city workflows — use **competency_gui** instead.

---

## Streamlit competency GUI (`competency_gui/`)

Interactive runner for **MOF competency** and **TWA city** workflows with table/chart/map views.

```bash
pip install -r requirements-gui.txt
streamlit run mini_marie/competency_gui/app.py
```

Docker: `docker compose --profile gui up competency-gui` → http://localhost:8501

Modes:

- **Online** — LIMIT 10 probe
- **Offline** — Full cap (needs warm cache for fast runs)
- **Force refresh** — Bypass cache read

---

## Agent response compaction

`workflow_mcp.format_workflow_mcp_response()` returns TSV with:

- Status, timing, `recording_path`
- Per-step `call_trace` (tool, row_count, summary)
- Truncated `sample_results` (5 rows/step)
- Hint: call `replay_workflow_offline` for full data

Long URIs and MOFids are shortened for context safety.

---

## Logging and debugging

- MCP tool calls logged at WARNING+ in `mof_case/main.py` (`mof_twa_mcp` logger)
- Recordings: inspect `call_trace[].record.sparql_executed` for SPARQL audit
- Competency eval batch: `run_competency_eval.py` → `competency_eval_results.md`

---

## Security notes

- Remote SPARQL endpoints are read-only but may leak query patterns; do not embed secrets in workflow JSON.
- Flask `secret_key` should be set via `FLASK_SECRET_KEY` in production.
- Cache SQLite under `data/` is gitignored; may contain large extracts of public KG data.
