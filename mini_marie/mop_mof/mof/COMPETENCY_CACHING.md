# MOF competency — probe online, replay offline (cached atomics)

## Problem

Human competency questions in `mini_marie/data/CompetencyQs.md` mix fast name lookups with heavy corpus joins. Running full SPARQL for every chained step wastes time and times out in agents.

## Pattern

| Phase | Command | LIMIT | Storage |
|-------|---------|-------|---------|
| **Online probe** | `run_competency_probe.py` | **10** (probe tier) | SQLite `data/mini_marie_cache/mof_competency/competency_cache.sqlite` |
| **Pre-cache (full)** | `warm_competency_cache.py --comprehensive` | **none** (atomics + workflow arg variants) | full tier rows + facet indexes |
| **Offline replay** | `replay_competency_offline.py --recording …` | n/a (no remote) | Replays **`probed_sequence`** from online + **local_join** |

## Full E2E (timed online + offline)

Runs all 24 competency workflows, times each phase, writes reports:

```bash
python -m mini_marie.mop_mof.mof.test_competency_e2e
# Reports: mini_marie/mop_mof/mof/competency_runs/e2e_report.json | .md
# Log:     mini_marie/mop_mof/mof/competency_runs/e2e_run.log
```

1. **Online probe** records `probed_sequence` (validated tool order + resolved args).  
2. **Comprehensive warm** (`workflow_driven_warm_specs`) = registry atomics + every `(tool, args)` from `competency_suite.json`.  
3. **Offline replay** executes the same sequence from the recording (cache + local joins only).

## CLI

```bash
# List workflow ids
python -m mini_marie.mop_mof.mof.run_competency_probe --list

# Probe one question (populates cache)
python -m mini_marie.mop_mof.mof.run_competency_probe --workflow CQ06_TOPOLOGY_ZIF8 --online-limit 10

# Probe entire suite
python -m mini_marie.mop_mof.mof.run_competency_probe --suite --online-limit 10

# Comprehensive pre-cache (all atomics + workflow arg variants)
python -m mini_marie.mop_mof.mof.warm_competency_cache --comprehensive

# Incremental: only specs missing from full-tier cache
python -m mini_marie.mop_mof.mof.warm_competency_cache --comprehensive --missing-only

# Online probe (records probed_sequence)
python -m mini_marie.mop_mof.mof.run_competency_probe --workflow CQ07_SAME_TOPO_ZIF8

# Offline replay (same sequence as probe)
python -m mini_marie.mop_mof.mof.replay_competency_offline --recording mini_marie/mop_mof/mof/competency_runs/CQ07_*_online_*.json

# Cache unit tests (no network)
python -m mini_marie.mop_mof.mof.test_competency_cache
```

## Local join steps (no SPARQL)

Defined in `workflows/competency_suite.json` with `"type": "local_join"`:

| Join | Use |
|------|-----|
| `topology_from_identity` | Read cached topology for a MOF name |
| `same_topology_count_local` | Count MOFs in `facet_topology_mof` |
| `same_topology_sample_local` | Sample peers by topology |
| `synthesis_by_refcodes_local` | Filter cached synthesis rows by refcode list |
| `metal_sources_local` | Read cached `facet_metal_source` |

Generic transforms (prefer over new bespoke joins when pools already exist):

| Transform | Use |
|-----------|-----|
| `filter_rows` | Numeric/string filters on any column |
| `join_rows` | Inner/left/anti join; composite `keys`; `right_is_list` semi-join |
| `multi_join_rows` | Chained joins (e.g. NIST→CoRE→Tobassco in `CQ_MORE03_NIST_ADSORPTION_CHEM`) |
| `group_aggregate` | `GROUP BY` + count/count_distinct/sum/avg/min/max on a pool |
| `type: sparql` | Residual SPARQL (`residual_sparql` cache key); warm before offline |

```bash
python -m mini_marie.mop_mof.mof.test_mof_advanced_workflow   # CQ02C filter+group+residual
python -m mini_marie.mop_mof.mof.test_cq_more03_offline_join  # More Q3 decomposed join
python -m mini_marie.mop_mof.mof.warm_competency_cache --workflow CQ_MORE03_NIST_ADSORPTION_CHEM
python -m mini_marie.mop_mof.mof.generate_catalog            # materialized_catalog.json
```

Skipped during **online** probe when `"offline_only": true`.

## Modules

| File | Role |
|------|------|
| `competency_cache.py` | SQLite cache, facet indexes, `invoke_tool()` |
| `competency_workflow_engine.py` | Chained probe/replay |
| `workflows/competency_suite.json` | 24 human competency workflows |
| `mof_competency_operations.py` | Atomic SPARQL tools (`limit=` for offline cap) |

## Cache location

`data/mini_marie_cache/mof_competency/` (under repo `/data`, gitignored). In Docker: volume `mini_marie_data` at `/app/data` (see [docker/README.md](../../docker/README.md)).

## Docker

```bash
docker compose build
docker compose --profile bench run --rm bench-mof-complex
docker compose --profile cli run --rm workflow-cli python -m mini_marie.mop_mof.mof.run_competency_probe --suite
```

## Still use raw SPARQL batch for

Multi-UNION blocks in `CompetencyQs.md` without atomic tools:

```bash
python -m mini_marie.mop_mof.mof.run_competency_eval
```
