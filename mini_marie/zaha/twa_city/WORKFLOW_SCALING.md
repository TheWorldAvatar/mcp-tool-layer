# TWA City — execute → record → replay at scale

## Problem

MCP tools use hardcoded `LIMIT` values so agents stay fast and context stays small. That gives **correct workflow shape** but sometimes **incomplete answers** when the true result needs a larger scan.

## Pattern: execute → record → replace limit → replay offline

| Phase | Where | LIMIT | Purpose |
|-------|--------|-------|---------|
| **Online probe** | Agent / MCP `run_workflow_online` | **10** | Discover valid call sequence; save `probed_sequence` |
| **Record** | `workflow_runs/*.json` | `probed_sequence` + call trace + seed variables |
| **Pre-cache (full)** | `warm_city_cache.py` | None (uncapped atomics) | Full city/building pools in SQLite |
| **Offline replay** | `replay_workflow.py` | No remote SPARQL | Full-tier cache + local joins |

## Example (Berlin → Bremen)

**Question:** Find locations of the 10 highest buildings.

| Call | Tool / step | Input | Output |
|------|-------------|-------|--------|
| 1 | `list_buildings_with_height` | `city=bremen` | building IRIs + heights (online LIMIT 500) |
| 2 | `transform: top_n_by_field` | pool from call 1 | top 10 building IRIs |
| 3 | `fetch_building_locations` | 10 IRIs | WKT footprints |

Pre-cache: `warm_city_cache --city bremen` fills full-tier height + location facets. Offline: call 1 reads cache, call 2 top-10 in memory, call 3 `local_locations_for_buildings` (no remote).

## Workflow JSON

Definitions live in `workflows/`:

- `top10_buildings_locations_bremen.json` — 3-step list → rank → locate
- `top10_buildings_locations_kl.json` — 2-step rank → locate (+ transform)

Steps support:

- `$variables` between calls
- `extract`: `column`, `all_rows`, `top_n_by_field`
- `transform: filter_rows` — generic field filters (numeric/string, any column)
- `record.sparql_executed` per SPARQL step for audit/replay

### `filter_rows` transform

After an atomic step stores a row pool (`extract.pick: all_rows`), filter in memory — no new SPARQL per threshold:

```json
{
  "type": "transform",
  "transform": "filter_rows",
  "input_variable": "building_pool",
  "output_variable": "tall_buildings",
  "filters": [
    { "field": "height", "op": "gte", "value": "$min_height_m" }
  ]
}
```

- **field**: any column name (supports dotted paths, e.g. `props.height`)
- **op**: `eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `contains`, `icontains`, `startswith`, `endswith`, `in`, `not_in`, `regex`, `empty`, `not_empty` (aliases like `>=` work)
- **logic**: `and` (default) or `or` across multiple clauses
- Workflow scalars (`min_height_m`, etc.) are copied into variables for `$` resolution

Example workflow: `workflows/bremen_buildings_height_filter.json` — change `min_height_m` from 80 to 86 offline against the same warmed height pool.

### `join_rows` transform

Join two row pools (or semi-join with a list of keys on the right):

```json
{
  "type": "transform",
  "transform": "join_rows",
  "left_variable": "top_building_rows",
  "right_variable": "location_rows",
  "left_key": "building",
  "right_key": "building",
  "how": "inner",
  "output_variable": "buildings_with_wkt"
}
```

- **right_is_list**: `true` when `right_variable` is a list of key values (e.g. refcodes)
- **filters**: optional post-join clauses (same ops as `filter_rows`)
- `--comprehensive` warm uses `workflow_driven_warm_specs()` (registry + all `workflows/*.json` atomics)

## MCP tools (Cursor agent)

| Tool | Purpose |
|------|---------|
| `list_workflows` | Available workflow names |
| `run_workflow_online(workflow_name)` | LIMIT **10**, saves recording, returns compact TSV + `recording_path` |
| `replay_workflow_offline(recording_path)` | Full-scale replay, compact summary (WKT not inlined) |

Reload `twa-city` in Cursor MCP after code changes.

## CLI (same backend as MCP)

```bash
python -m mini_marie.zaha.twa_city.run_workflow --workflow top10_buildings_locations_kl --online-limit 10
python -m mini_marie.zaha.twa_city.replay_workflow --recording mini_marie/zaha/twa_city/workflow_runs/<file>.json
python -m mini_marie.zaha.twa_city.test_workflow_scaling
```

## Caching (SQLite + facets)

See [CITY_CACHING.md](CITY_CACHING.md). Plan results are cached under `data/mini_marie_cache/twa_city/`; offline replay refreshes at raised limits and can use local joins over cached buildings.

```bash
python -m mini_marie.zaha.twa_city.test_city_cache
python -m mini_marie.zaha.twa_city.test_city_e2e
```

## Key modules

| Module | Role |
|--------|------|
| `city_cache.py` | SQLite cache, facets, `invoke_plan()` |
| `sparql_plans.py` | SPARQL builders + `strip_limit` / `apply_limit` |
| `workflow_engine.py` | Chained execution, cache, local joins, recording |
| `run_workflow.py` | Online runner |
| `replay_workflow.py` | Offline runner |

## Pre-cache and offline

```bash
python -m mini_marie.zaha.twa_city.warm_city_cache --comprehensive
python -m mini_marie.zaha.twa_city.replay_workflow --recording mini_marie/zaha/twa_city/workflow_runs/<online>.json
```

- Online answers may **differ** from offline when the true top-N lies outside the online probe window.
- Offline requires full-tier cache; run `warm_city_cache` first.
