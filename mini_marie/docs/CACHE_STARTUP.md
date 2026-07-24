# Cache startup guide

Build SQLite caches **incrementally** so offline workflow replay works. Pattern: **probe → warm a little → check status → continue**.

**Prerequisites** (project root = parent of `mini_marie/`):

```bash
cd /path/to/project_root
source .venv/bin/activate
export PYTHONPATH="$(pwd)"
```

Caches: `data/mini_marie_cache/` (`MINI_MARIE_DATA_DIR` to override).

**Interactive helper:**

```bash
./mini_marie/scripts/warm_cache_steps.sh city
```

---

## How caching works

| Phase | Network | Purpose |
|-------|---------|---------|
| **Probe** | Yes | Small LIMIT; saves recording JSON |
| **Warm (full tier)** | Yes | Uncapped SPARQL → SQLite |
| **Offline replay** | No | Re-run probed sequence from cache |

You do not need full comprehensive warm to start.

---

## Global status

```bash
python -c "from mini_marie.kg_catalog import catalog; print(catalog.kg_cache_status_text())"
```

---

## 1. MOF competency

**Path:** `data/mini_marie_cache/mof_competency/competency_cache.sqlite`

| Step | Command |
|------|---------|
| 0 | `python -m mini_marie.mop_mof.mof.test_competency_cache` |
| 1 Probe | `python -m mini_marie.mop_mof.mof.run_competency_probe --workflow CQ06_TOPOLOGY_ZIF8` |
| 2 Warm | `python -m mini_marie.mop_mof.mof.warm_competency_cache --workflow CQ06_TOPOLOGY_ZIF8 --missing-only` |
| 3 Replay | `python -m mini_marie.mop_mof.mof.replay_competency_offline --recording <path-from-step-1>` |
| 4 Expand | `python -m mini_marie.mop_mof.mof.warm_competency_cache --comprehensive --missing-only` |

---

## 2. TWA city (Bremen / KL)

**Path:** `data/mini_marie_cache/twa_city/city_cache.sqlite`

| Step | Command |
|------|---------|
| 0 Status | `python -m mini_marie.zaha.twa_city.warm_city_cache --status` |
| 1 Atomics | `python -m mini_marie.zaha.twa_city.warm_city_cache --city bremen --atomics-only --missing-only` |
| 2 Locations | `python -m mini_marie.zaha.twa_city.warm_city_cache --city bremen --locations-only --locations-top-n 50 --missing-only` |
| 3 Workflow | `run_workflow` then `replay_workflow` for `top10_buildings_locations_bremen` |
| 4 Full (optional) | `--comprehensive --missing-only` |

---

## 3. Chemistry (Blazegraph)

**Path:** `data/mini_marie_cache/chemistry/`

| Step | Command |
|------|---------|
| 0 Status | `python -m mini_marie.marie.chemistry.cache_status` |
| 1 Probe | `python -m mini_marie.marie.chemistry.warm_chemistry_cache --comprehensive --probe-only` |
| 2 Batch warm | `python -m mini_marie.marie.chemistry.warm_chemistry_cache --comprehensive --missing-only --batch 5` |
| 3 Coverage | `python -m mini_marie.marie.chemistry.cache_status --coverage` |
| 4 Corpus | `warm_species_corpus --status` then `--max-batches 1 --delay 3` |
| 5 E2E | `python -m mini_marie.marie.chemistry.run_competency_e2e` |

Repeat step 2 until coverage stops improving.

---

## 4. Singapore (sg-old)

**Path:** `data/mini_marie_cache/sg_old/ontop_cache.sqlite`

```bash
python -m mini_marie.zaha.sg_old.warm_ontop_cache --names-only --page-size 500
python -m mini_marie.zaha.sg_old.warm_ontop_cache --page-size 3000
```

---

## 5. MOP synthesis (local)

**Path:** `data/mini_marie_cache/twa_label_index.json`

```bash
python -c "from mini_marie.mop_mof.mops.twa_operations import ensure_twa_loaded; print(len(ensure_twa_loaded()))"
```

---

## 6. Docker

```bash
docker compose --profile cli run --rm workflow-cli \
  python -m mini_marie.zaha.twa_city.warm_city_cache --city bremen --atomics-only --missing-only
```

Volume `mini_marie_data` → `/app/data`.

---

## 7. New deployment order

1. `setup_linux.sh` + `verify_install.sh`
2. MOF probe → warm → replay
3. City atomics → top-50 locations → replay
4. Chemistry probe-only → batch 5
5. sg-old / MOPs as needed

Use `./mini_marie/scripts/warm_cache_steps.sh all` only for long unattended runs.

---

## Related

| Doc | Topic |
|-----|-------|
| [DEPLOYMENT.md](DEPLOYMENT.md) | Install, Docker, MCP |
| [WORKFLOWS_AND_CACHING.md](WORKFLOWS_AND_CACHING.md) | Cache tiers |
| `../mop_mof/mof/COMPETENCY_CACHING.md` | MOF design |
| `../zaha/twa_city/CITY_CACHING.md` | City design |
| `../marie/chemistry/CHEMISTRY_CACHING.md` | Chemistry atomics |
| `../marie/chemistry/CORPUS_CACHING.md` | Chemistry corpus |
