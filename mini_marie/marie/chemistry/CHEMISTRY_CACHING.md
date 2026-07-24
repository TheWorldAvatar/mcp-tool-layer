# Chemistry competency caching

SQLite cache for Marie chemistry competency tools. Designed for **fragile** Blazegraph endpoints (`theworldavatar.io/chemistry`).

## Three tiers

| Mode | Tier | Remote SPARQL | Row limit |
|------|------|---------------|-----------|
| MCP / online probe | `probe` | Yes (GET + `curl/8.0`) | **5** (`ONLINE_PROBE_LIMIT`) |
| Warm pre-cache | `full` | Yes | Namespace **warm cap** (10k–500k) |
| Offline replay | `full` | **No** | Read cache only |

DB path: `data/mini_marie_cache/chemistry/chemistry_cache.sqlite`

## Safety (fragile endpoints)

- **Inter-call delay** default 3s between warm requests (`WARM_DELAY_SECONDS`)
- **Retries** up to 5 with exponential backoff on warm failures
- **Namespace warm caps** — never truly uncapped on 50M+ triple stores:
  - `ontospecies`, `ontozeolite`: 10,000 rows
  - `ontokin`: 50,000
  - `ontocompchem`, `ontoprovenance`: 500,000
- **ASK health check** before warming each namespace (skip with `--skip-health-check`)
- **Filtered warm specs only** — no unbounded `lookup_individuals(ReactionMechanism)` without filters
- **WAL journal** on SQLite for concurrent warm/status reads

## Full-space caching (option catalog)

Each tool can have many argument variants — e.g. `filter_by_literal(framework=AEN)` vs `(BEA)` vs `(FAU)`.
The catalog enumerates every variant; **`--missing-only`** skips variants already in the full tier.

**Catalog:** [`warm_option_catalog.json`](warm_option_catalog.json)  
**Discover live enumerations** (one SPARQL query per target, then sleep):

```bash
python -m mini_marie.marie.chemistry.discover_warm_options --target framework_codes
# writes warm_option_catalog.discovered.json (256 framework codes)
```

**Incremental warm (recommended on fragile endpoints):**

```bash
# See dimensions and option counts
python -m mini_marie.marie.chemistry.warm_chemistry_cache --list-dimensions

# Coverage: which options are cached vs still missing
python -m mini_marie.marie.chemistry.cache_status --coverage
python -m mini_marie.marie.chemistry.cache_status --coverage --dimension ontozeolite_framework_materials

# Warm next 10 missing variants only (3s sleep between calls; tqdm ETA in terminal)
python -m mini_marie.marie.chemistry.warm_chemistry_cache --full-space --missing-only --batch 10 --delay 3

# Warm one dimension, resume with offset
python -m mini_marie.marie.chemistry.warm_chemistry_cache \
  --full-space --dimension ontozeolite_framework_materials \
  --missing-only --batch 20 --offset 0 --delay 3

# Warm all missing zeolite framework codes (256 variants after discover)
python -m mini_marie.marie.chemistry.warm_chemistry_cache \
  --full-space --namespace ontozeolite --missing-only --delay 3
```

| Flag | Purpose |
|------|---------|
| `--full-space` | Expand catalog (not just ~23 MQ seeds) |
| `--missing-only` | Skip cached `function_x(A)`; warm only B,C,D,… |
| `--batch N` | Warm at most N specs this run |
| `--offset N` | Skip first N missing specs (resume batches) |
| `--dimension ID` | One catalog dimension only |
| `--delay SEC` | Sleep between remote calls |

## Commands (MQ seed warm)

```bash
# Smoke probes (live LIMIT 5, no cache required)
python -m mini_marie.marie.chemistry.probe_competency

# Online probe tier → populate cache (LIMIT 5)
python -m mini_marie.marie.chemistry.warm_chemistry_cache --comprehensive --probe-only

# Full-tier warm (recommended: incremental)
python -m mini_marie.marie.chemistry.warm_chemistry_cache --comprehensive --missing-only

# Warm one namespace only
python -m mini_marie.marie.chemistry.warm_chemistry_cache --comprehensive --namespace ontokin --missing-only

# Cache status
python -m mini_marie.marie.chemistry.cache_status

# Workflow online probe (records call sequence)
python -m mini_marie.marie.chemistry.run_competency_probe --workflow mq03_formula_c6h8o6

# Offline replay from recording (cache-only)
python -m mini_marie.marie.chemistry.replay_competency_offline mini_marie/marie/chemistry/competency_runs/<recording>.json
```

## Architecture

```
warm_chemistry_cache.py  →  chemistry_cache.invoke_tool(mode=warm)
run_competency_probe.py  →  chemistry_workflow_engine  →  invoke_tool(mode=online)
replay_competency_offline →  invoke_tool(mode=offline)  →  CacheMissError if not warmed
```

MCP atomic tools remain **live probe only** (LIMIT 5). Cache is used by warm CLI and workflow probe/replay.

## Cached tools

All tools require `namespace` in args:

- `lookup_individuals`
- `get_linked_values`
- `filter_by_literal`
- `count_instances`
- `traverse_mechanism_reactions` (ontokin)
- `query_calculation_results` (ontocompchem)
- `query_zeolite_property` (ontozeolite)

Warm manifest: [`atomic_warm_manifest.py`](atomic_warm_manifest.py)  
Workflows: [`workflows/competency_suite.json`](workflows/competency_suite.json)

## Endpoint health

Before a full warm batch, run:

```bash
python -m mini_marie.probe_chemistry_retry
```

If ASK fails for a namespace, warm aborts unless `--skip-health-check` is set.
