# Workflows and caching

This document defines the **workflow JSON language** and the **probe / warm / replay** caching model shared across `mof_case`, `twa_city`, and `twa_mops`.

---

## Workflow file structure

Workflows live in `<domain>/workflows/*.json`. A file may define one workflow or a manifest:

```json
{
  "id": "WORKFLOW_ID",
  "question": "Natural language question",
  "description": "Short label for catalogs",
  "online_limit": 10,
  "offline_cap": 500000,
  "city": "bremen",
  "steps": [ ... ]
}
```

Manifest example: `mof_case/workflows/competency_suite.json` wraps many workflows under `"workflows": [...]`.

### Seed variables

Scalars at workflow root (`city`, `metal`, `mof_name`, `min_height_m`, …) are copied into the variable map via `seed_variables_from_workflow()` so `$placeholders` in `args` resolve consistently during warm and runtime.

---

## Step types

| `type` | Default if omitted | Behavior |
|--------|-------------------|----------|
| `tool` | yes | Invoke MCP/registry tool; optional `extract` into variables |
| `transform` | | In-memory row algebra (`filter_rows`, `join_rows`, `top_n_by_field`, …) |
| `local_join` | | Read SQLite facets (competency/city); **skipped online** if `offline_only: true` |
| `sparql` | | Residual SPARQL via `residual_sparql` tool; cached by query hash |
| `aggregate` | | Domain-specific aggregates (where implemented) |

### Tool step

```json
{
  "tool": "get_mofs_by_metal",
  "args": { "metal": "$metal", "list_sources": true },
  "extract": {
    "zn_pool": { "pick": "all_rows" }
  }
}
```

### Extract modes (`extract` block)

| `pick` | Returns |
|--------|---------|
| `row_field` | Single field from row `index` (default 0) |
| `column` | List of values for `field` across rows |
| `all_rows` | Full row list → variable for transforms |
| `top_n_by_field` | Sorted slice by numeric `field` |

### Transform: `filter_rows`

```json
{
  "type": "transform",
  "transform": "filter_rows",
  "input_variable": "zn_pool",
  "output_variable": "core_zn_rows",
  "filters": [
    { "field": "sourcedb", "op": "icontains", "value": "$db_substring" }
  ],
  "logic": "and"
}
```

Supported ops include: `eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `contains`, `icontains`, `startswith`, `endswith`, `in`, `not_in`, `regex`, `empty`, `not_empty`.

### Transform: `join_rows`

Join two row pools on `left_key` / `right_key` (or composite `on` / `keys`). Options: `how` (`inner`, `left`, `anti`), `right_is_list` for semi-join patterns. See `row_joins.py` and tests in `test_row_joins_v2.py`.

### Transform: `group_aggregate`

```json
{
  "type": "transform",
  "transform": "group_aggregate",
  "input_variable": "core_zn_rows",
  "output_variable": "core_zn_by_source",
  "group_by": ["sourcedb"],
  "aggregates": [
    { "field": "mof", "op": "count_distinct", "as": "mof_count" }
  ]
}
```

### Local join (MOF competency)

```json
{
  "type": "local_join",
  "join": "topology_from_identity",
  "reference_name": "ZIF-8",
  "output_variable": "topology",
  "offline_only": true
}
```

| `join` id | Reads |
|-----------|--------|
| `topology_from_identity` | `facet_identity` |
| `same_topology_count_local` | `facet_topology_mof` |
| `same_topology_sample_local` | `facet_topology_mof` |
| `synthesis_by_refcodes_local` | `facet_synthesis` |
| `metal_sources_local` | `facet_metal_source` |

### Residual SPARQL

```json
{
  "type": "sparql",
  "query": "SELECT (COUNT(?mof) AS ?count) WHERE { ... }",
  "offline_only": true,
  "extract": { "total_mofs": { "pick": "row_field", "field": "count" } }
}
```

Warm full-tier cache with `warm_competency_cache.py` before offline replay. Queries containing unresolved `$variables` are not auto-collected for warm.

---

## Variable resolution

Strings starting with `$` substitute from the variables dict. Nested structures (lists, dicts) are resolved recursively (`resolve_value()` in domain engines).

Example: `"args": { "metal": "$metal" }` with workflow root `"metal": "Zn"`.

---

## Execution modes

| Mode | Remote SPARQL | Row limits | Writes recording |
|------|---------------|------------|------------------|
| `online` | Yes (atomics) | probe tier / `online_limit` | Yes + `probed_sequence` |
| `offline` | Only uncached residual | full tier / `offline_cap` | Optional |

### Phase diagram

```text
  ┌─────────────┐     ┌──────────────┐     ┌─────────────────┐
  │ Online probe│────▶│ Warm (full)  │────▶│ Offline replay  │
  │ LIMIT 10    │     │ optional     │     │ cache + local   │
  └─────────────┘     └──────────────┘     └─────────────────┘
        │                     │                      │
        ▼                     ▼                      ▼
  competency_runs/      SQLite facets          Same probed_sequence
  workflow_runs/*.json  mini_marie_cache/      No remote for hits
```

---

## Cache tiers (implementation)

Defined in `cache_tiers.py`:

```python
TIER_PROBE = "probe"   # row_limit = 10
TIER_FULL = "full"     # row_limit = None
```

`CompetencyCache` / `CityCache` key: `make_cache_key(tool, args, tier)`.

**Online probe** populates probe tier and records which tools/args succeeded.

**Warm scripts** (`warm_competency_cache.py`, `warm_city_cache.py`):

```bash
python -m mini_marie.mop_mof.mof.warm_competency_cache --comprehensive
python -m mini_marie.mop_mof.mof.warm_competency_cache --comprehensive --missing-only

python -m mini_marie.zaha.twa_city.warm_city_cache --city bremen
```

`--comprehensive` uses registry atomics + every `(tool, args)` from workflow manifests (`workflow_driven_warm_specs`).

**Offline replay**:

```bash
python -m mini_marie.mop_mof.mof.replay_competency_offline \
  --recording mini_marie/mop_mof/mof/competency_runs/CQ06_*_online_*.json

python -m mini_marie.zaha.twa_city.replay_workflow --recording mini_marie/zaha/twa_city/workflow_runs/<file>.json
```

Raises `CacheMissError` if a full-tier entry is missing.

---

## MCP workflow tools

Each domain exposes (via `workflow_mcp.py`):

| Tool | Effect |
|------|--------|
| `run_workflow_online` | `mode=online`, default `online_limit=10`, returns TSV + `recording_path` |
| `replay_workflow_offline` | Loads recording, runs `probed_sequence` at `offline_cap` |
| `list_workflows` | Catalog TSV |

MOF competency uses parallel CLI: `run_competency_probe.py`, `replay_competency_offline.py`.

---

## CLI reference

| Command | Domain |
|---------|--------|
| `python -m mini_marie.mop_mof.mof.run_workflow --workflow <id>` | MOF analytics |
| `python -m mini_marie.mop_mof.mof.replay_workflow --recording <path>` | MOF analytics |
| `python -m mini_marie.mop_mof.mof.run_competency_probe --workflow <id>` | Competency |
| `python -m mini_marie.mop_mof.mof.replay_competency_offline --recording <path>` | Competency |
| `python -m mini_marie.zaha.twa_city.run_workflow --workflow <id>` | City |
| `python -m mini_marie.mop_mof.mops.run_workflow --workflow <id>` | MOPs local |

Add `--list` where supported to print workflow ids.

---

## Example workflows by domain

### MOF analytics (`mof_case/workflows/`)

- `top10_tobassco_co2_properties` — Rank CO₂ uptake → top-N → property sheet
- `dominant_topology_leader` — Topology counts → branch → properties
- `tobassco_co2_corpus_share` — Corpus + Tobassco stats

### MOF competency (`competency_suite.json`)

24 workflows mapping [CompetencyQs.md](../data/CompetencyQs.md), e.g. `CQ01_PLD_UIO66`, `CQ07_SAME_TOPO_ZIF8`, `CQ02C_ZN_CORE_GROUP_RESIDUAL` (filter + group + residual SPARQL).

### City (`twa_city/workflows/`)

- `top10_buildings_locations_bremen` — list heights → top 10 → WKT
- `top10_buildings_locations_kl` — Kaiserslautern variant
- `bremen_buildings_height_filter` — `filter_rows` on height pool

### MOPs (`twa_mops/workflows/`)

- `list_mops_catalog` — Sample online, full catalog offline
- `synthesis_profile` — VMOP-17 recipe chain

---

## Performance expectations

| Operation | Typical time | Notes |
|-----------|--------------|-------|
| Online probe (1–3 atomics) | 1–30 s | Network to OntoMOFs / Ontop |
| Warm one city | Minutes | Full building pools |
| Comprehensive MOF warm | Long | All competency arg variants |
| Offline replay (warm hit) | Sub-second per step | SQLite + pandas-like joins in Python |

Use `test_*_cache` and `test_workflow_scaling` for regression timing without agents.

---

## Related domain docs

- MOF competency detail: [mof_case/COMPETENCY_CACHING.md](../mof_case/COMPETENCY_CACHING.md)
- City cache: [twa_city/CITY_CACHING.md](../twa_city/CITY_CACHING.md)
- Scaling notes: [mof_case/WORKFLOW_SCALING.md](../mof_case/WORKFLOW_SCALING.md), [twa_city/WORKFLOW_SCALING.md](../twa_city/WORKFLOW_SCALING.md), [twa_mops/WORKFLOW_SCALING.md](../twa_mops/WORKFLOW_SCALING.md)
