# TWA City — cached workflows (probe online, replay offline)

SQLite atomics with **probe** vs **full** tiers. Pre-cache with `warm_city_cache.py`; offline replay is cache + local joins only (no remote SPARQL).

## Cache location

`data/mini_marie_cache/twa_city/city_cache.sqlite` (gitignored under `/data`). Docker: [docker/README.md](../../docker/README.md).

```bash
docker compose build
docker compose --profile bench run --rm bench-city-complex
```

## Tables

| Store | Purpose |
|-------|---------|
| `atomic_calls` / `atomic_rows` | SPARQL plan results keyed by tool + args + tier (`probe` / `full`) |
| `facet_building_height` | Buildings from list/rank/usage plans |
| `facet_building_location` | WKT footprints from `fetch_building_locations` |

## Local joins (`type: "local_join"`)

| Join | Use |
|------|-----|
| `building_pool_from_cache` | Reload full height pool from facets (offline) |
| `top_n_by_height_local` | Top-N by height without SPARQL |
| `locations_for_buildings_local` | WKT for building IRIs from cache |
| `buildings_with_locations_sql` | SQL join height + location facets for building IRIs |
| `top_n_with_locations_from_cache` | SQL top-N by height joined with WKT (indexed) |

Set `"offline_only": true` to skip during online probe.

## CLI / E2E

```bash
python -m mini_marie.zaha.twa_city.test_city_cache
python -m mini_marie.zaha.twa_city.test_city_e2e
# Reports: mini_marie/zaha/twa_city/workflow_runs/city_e2e_report.json | .md

python -m mini_marie.zaha.twa_city.city_cache_status
python -m mini_marie.zaha.twa_city.city_cache_status --city bremen

# Chunked / resumable warm (Bremen locations ~10k batches @ 100 buildings — hours)
python -m mini_marie.zaha.twa_city.warm_city_cache --city bremen --atomics-only
python -m mini_marie.zaha.twa_city.warm_city_cache --city bremen --locations-only --missing-only
python -m mini_marie.zaha.twa_city.warm_city_cache --city kaiserslautern --comprehensive

# Recommended for offline top10/top50 (minutes, not hours):
python -m mini_marie.zaha.twa_city.warm_city_cache --city kaiserslautern --locations-top-n 50 --missing-only

python -m mini_marie.zaha.twa_city.warm_city_cache --city bremen --city kaiserslautern
python -m mini_marie.zaha.twa_city.warm_city_cache --comprehensive   # all cities; very long
python -m mini_marie.zaha.twa_city.run_workflow --workflow top10_buildings_locations_bremen
python -m mini_marie.zaha.twa_city.replay_workflow --recording mini_marie/zaha/twa_city/workflow_runs/<file>.json
```

## Partial location cache (workflows)

Offline `fetch_building_locations` resolves in order:

1. `facet_building_location` (any warmed batch, including `--locations-top-n`)
2. Full-tier **atomic** row for the exact `building_iris` list
3. Per-IRI atomic batches

Workflow status is `partial` when some IRIs have no WKT but others do. Warm targeted pools:

```bash
python -m mini_marie.zaha.twa_city.warm_city_cache --city kaiserslautern --locations-top-n 50 --missing-only
```

## MCP (`twa-city`)

- `run_workflow_online` — LIMIT 10, writes cache
- `replay_workflow_offline` — cache-only (`force_refresh=False`), hybrid location lookup, local joins

Reload MCP after code changes.

## Modules

| File | Role |
|------|------|
| `city_cache.py` | SQLite + `invoke_plan()` |
| `workflow_engine.py` | Cached plan steps, local joins, `answer_digest` |
| `sparql_plans.py` | SPARQL builders (unchanged) |
| `test_city_e2e.py` | Timed online + offline for all `workflows/*.json` |
