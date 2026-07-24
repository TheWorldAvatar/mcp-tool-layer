# TWA City stack — data source notes

Credentials (both cities): `postgres` / `password`

## Public endpoints (cmpg.io)

| City | Adminer (PostGIS) | Ontop UI | SPARQL |
|------|-------------------|----------|--------|
| Kaiserslautern | [Adminer](https://kaiserslautern.cmpg.io/adminer/ui/?pgsql=kaiserslautern-postgis%3A5432) | [Ontop UI](https://kaiserslautern.cmpg.io/ontop/ui/) | `https://kaiserslautern.cmpg.io/ontop/sparql/` |
| Bremen | [Adminer](https://bremen.cmpg.io/adminer/ui/?pgsql=bremen-stack-postgis%3A5432&username=postgres) | [Ontop UI](https://bremen.cmpg.io/ontop/ui/) | `https://bremen.cmpg.io/ontop/sparql/` |

## Docker-internal (stack network)

| City | PostGIS host:port |
|------|-------------------|
| Kaiserslautern | `kaiserslautern-postgis:5432` |
| Bremen | `bremen-stack-postgis:5432` |

## Schema (from Kaiserslautern probe)

- **Buildings:** `http://www.opengis.net/citygml/building/2.0/Building` (~118k), IRIs `https://theworldavatar.io/kg/Building/{uuid}`
- **Geo:** GeoSPARQL (`geo:asWKT`, `geo:hasGeometry`, `geo:hasMetricArea`)
- **Usage types:** `https://www.theworldavatar.com/kg/ontobuiltenv/*` (Domestic, Office, …)

> `http://68.183.227.15:3840/ontop/sparql/` is **OntoMOFs**, not city data.

## Probing

```bash
# Shallow schema discovery
python -m mini_marie.zaha.twa_city.probe --endpoint https://kaiserslautern.cmpg.io/ontop/sparql/ --discover

# Deep building schema (coverage, height, usage, addresses, geo)
python -m mini_marie.zaha.twa_city.probe_deep
```

Outputs: `BUILDING_SCHEMA.md`, `probe_results_deep.json`. See [PROBE.md](./PROBE.md).

## MCP server
## MCP server

```bash
python -m mini_marie.zaha.twa_city.main
python -m mini_marie.zaha.twa_city.run_queries --city bremen --query count
```

Registered in `.cursor/mcp.json` as `twa-city`.

## GIS visualization

Building footprints come from `geo:hasGeometry` / `geo:asWKT` (CityGML polygons).

```bash
python -m mini_marie.zaha.twa_city.visualize --city bremen --limit 15 --open
python -m mini_marie.zaha.twa_city.visualize --city kaiserslautern --mode bbox --limit 20
```

Maps are written to `mini_marie/zaha/twa_city/maps/*.html` (Leaflet + OpenStreetMap). MCP tool: `generate_building_map`.

## Scalable workflows (execute → record → replay)

See [WORKFLOW_SCALING.md](./WORKFLOW_SCALING.md). Online probe:

```bash
python -m mini_marie.zaha.twa_city.run_workflow --workflow top10_buildings_locations_kl
python -m mini_marie.zaha.twa_city.replay_workflow --recording mini_marie/zaha/twa_city/workflow_runs/<file>.json
```
