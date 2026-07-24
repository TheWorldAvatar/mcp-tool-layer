# Chemistry cache — maintenance backlog

> Snapshot: **2026-06-10**  
> For step-by-step warm commands see [docs/CACHE_STARTUP.md](../../docs/CACHE_STARTUP.md).  
> Architecture: [CORPUS_CACHING.md](CORPUS_CACHING.md) · [CHEMISTRY_CACHING.md](CHEMISTRY_CACHING.md)

## Completed baseline (no re-warm needed)

| Layer | Item | Scale | Verify |
|-------|------|-------|--------|
| Atomic | workflow-driven specs | MQ/probe set | `cache_status --coverage` |
| Corpus | `ontospecies_*` facets | species / formula / uses / pKa | `warm_species_corpus --status` |
| Corpus | `ontozeolite_*` | 2,208 materials | `warm_zeolite_corpus --status` |
| Registry | 15/15 facets implemented | — | `list_corpus_facets --summary` |

## Open / optional items

| ID | Task | Notes |
|----|------|-------|
| D3 | `test_species_physprops_corpus.py` | Low priority (no TPSA in endpoint) |
| D7 | CI job | Unit tests + `--status` only; no live warm |
| E1–E4 | Atomic catalog extras | Optional Marie demo parameters |
| F1–F4 | Other domains | MOF, sg-old, twa-city, cross-kg |

## Quick status commands

```bash
python -m mini_marie.marie.chemistry.cache_status
python -m mini_marie.marie.chemistry.cache_status --coverage
python -m mini_marie.marie.chemistry.cache_offline_smoke
python -m mini_marie.marie.chemistry.run_competency_e2e
```

Use `--missing-only`, `--batch N`, and per-corpus `--max-batches 1` for incremental warm-up.
