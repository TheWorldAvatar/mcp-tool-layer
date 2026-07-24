# TWA City — data source probing

Unlike `mof_case`, city stack endpoints are **not fully documented** in `seed.md` (only Docker-internal service names).

## Known from seed.md

| City | PostGIS (Adminer) | Ontop |
|------|-------------------|-------|
| Kaiserslautern | `kaiserslautern-postgis:5432` | Ontop v5.3.0 (URL TBD) |
| Bremen | `bremen-stack-postgis:5432` | Ontop v5.3.0 (URL TBD) |

Credentials: `postgres` / `password`

## MOF workflow (target)

1. **Probe** — find reachable SPARQL URLs and sample schema (`probe.py`, `queries/00–04`).
2. **Seed queries** — competency-style SPARQL files once classes/prefixes are known.
3. **Operations** — `twa_city_operations.py` with hardcoded limits (mirror `mof_operations.py`).
4. **MCP** — `main.py` FastMCP tools + `.cursor/mcp.json` entry.

## Run probing

```bash
# From repo root (PYTHONPATH = repo root)
# Fast: find open SPARQL URLs only
python -m mini_marie.zaha.twa_city.probe --json-out mini_marie/zaha/twa_city/probe_results.json

# Slow: schema introspection (class/predicate counts, geo, city name filter)
python -m mini_marie.zaha.twa_city.probe --endpoint http://localhost:PORT/ontop/sparql/ --discover
```

## Endpoints (cmpg.io)

| City | SPARQL |
|------|--------|
| Bremen | `https://bremen.cmpg.io/ontop/sparql/` |
| Kaiserslautern | `https://kaiserslautern.cmpg.io/ontop/sparql/` |

Heavy `GROUP BY` queries may **504** on large graphs; use `01b_building_count.sparql` or narrow filters.

## Deep probe (building details)

```bash
python -m mini_marie.zaha.twa_city.probe_deep
python -m mini_marie.zaha.twa_city.probe_deep --city bremen
```

Writes:

- `BUILDING_SCHEMA.md` — human-readable summary per city
- `probe_results_deep.json` — full JSON for MCP design

Queries `10_*`–`18_*`: one-building property list, predicate sample (500 buildings), height stats, usage counts, coverage, addresses, footprint area, WKT sample.

## Next steps after probe

- Record actual endpoint URLs per city in `configs/twa_city.json`.
- Split or parameterise tools if Bremen and Kaiserslautern use **different** Ontop instances.
- Add city-specific prefixes and competency questions once `01_class_counts` / `02_predicate_counts` identify the ontology.
