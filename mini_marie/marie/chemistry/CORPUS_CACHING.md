# Chemistry corpus caching (full KG materialization)

Competency questions **guide** MCP design. The goal is **whole-KG offline coverage** from **many MCP query angles** — not one index (names only) and not 314 atomic variants.

## Three layers

| Layer | What | Example |
|-------|------|---------|
| **Online probe** | Live SPARQL, LIMIT 5 | MCP atomic tools |
| **Atomic cache** | `(tool, args)` results | 314 filtered warm specs (~18 MB) |
| **Corpus cache** | Materialized indexes per **query pattern** | names, pKa rows, reaction graph, … |

Same DB: `data/mini_marie_cache/chemistry/chemistry_cache.sqlite`

```bash
python -m mini_marie.marie.chemistry.list_corpus_facets --summary
python -m mini_marie.marie.chemistry.cache_status --coverage
```

Registry: [`corpus_registry.py`](corpus_registry.py)

---

## Facet status (14/15 implemented)

| Facet ID | Warm CLI | MCP offline tools |
|----------|----------|-------------------|
| `ontospecies_names` | `warm_species_corpus` | `search_species_names` |
| `ontospecies_pka` | `warm_species_pka_corpus` | `query_species_pka` |
| `ontospecies_uses` | `warm_species_uses_corpus` | `search_species_uses`, `filter_by_literal(hasUse)` |
| `ontospecies_physprops` | `warm_species_physprops_corpus` | `query_species_physprops` |
| `ontospecies_formula_index` | `build_species_formula_index --build` | `list_species_by_formula`, `filter_by_literal(formula)` |
| `ontokin_mechanisms` | `warm_ontokin_corpus --facet mechanisms` | `search_mechanisms` |
| `ontokin_reaction_graph` | `warm_ontokin_corpus --facet graph` | `traverse_mechanism_reactions` |
| `ontokin_rate_models` | — | **planned** |
| `ontocompchem_results` | `warm_compchem_corpus` | `query_calculation_results` |
| `ontozeolite_*` (4 facets) | `warm_zeolite_corpus` | `search_zeolite_materials`, `query_zeolite_property`, `filter_by_literal` |
| `ontoprovenance_persons` | `warm_provenance_corpus` | `search_authors`, `lookup_individuals(Person)` |
| `ontoprovenance_publications` | `warm_provenance_corpus` | reference rows in `corpus_provenance_refs` |

---

## Warm all corpora (resumable)

```bash
# OntoSpecies (fragile — use --delay 3, health check before each batch)
python -m mini_marie.marie.chemistry.warm_species_corpus --max-batches 0 --delay 3
python -m mini_marie.marie.chemistry.warm_species_pka_corpus --max-batches 0 --delay 3
python -m mini_marie.marie.chemistry.warm_species_uses_corpus --max-batches 0 --delay 3
python -m mini_marie.marie.chemistry.warm_species_physprops_corpus --max-batches 0 --delay 3
python -m mini_marie.marie.chemistry.build_species_formula_index --build

# Smaller namespaces
python -m mini_marie.marie.chemistry.warm_ontokin_corpus --facet mechanisms --max-batches 0
python -m mini_marie.marie.chemistry.warm_ontokin_corpus --facet graph --max-batches 0
python -m mini_marie.marie.chemistry.warm_compchem_corpus
python -m mini_marie.marie.chemistry.warm_provenance_corpus

# OntoZeolite (fragile)
python -m mini_marie.marie.chemistry.warm_zeolite_corpus --max-batches 0 --delay 3
```

Status per facet: `python -m mini_marie.marie.chemistry.warm_<facet>_corpus --status` (or `--health-only`).

---

## Warm strategy

1. **Cursor pagination** (`FILTER(STR(?entity) > cursor)`) — avoid OFFSET on 50M+ namespaces.
2. **Two-phase fetch**: page entity IRIs → `VALUES { … }` property lookup.
3. **`corpus_warm_state`**: `corpus_id`, `offset_next`, `cursor_subject`, `status` (`running|paused|complete`).
4. **Health + retry**: [`corpus_health.py`](corpus_health.py), [`corpus_fetch.py`](corpus_fetch.py); pauses without advancing cursor on failure.
5. **Corpus-first MCP**: [`competency_operations.py`](competency_operations.py) reads SQLite before live SPARQL.

---

## DB recovery

If `database disk image is malformed`:

```bash
python -m mini_marie.marie.chemistry.recover_cache_db
python -m mini_marie.marie.chemistry.rebuild_species_table
python -m mini_marie.marie.chemistry.build_species_formula_index --build
```

---

## Related

- [CACHE_BACKLOG.md](CACHE_BACKLOG.md) — maintenance backlog (optional enhancements)
- [CHEMISTRY_CACHING.md](CHEMISTRY_CACHING.md) — atomic tier (314/314 variants)
- [gaps_marie_zaha.md](../docs/gaps_marie_zaha.md) — MQ coverage map
- [corpus_registry.py](corpus_registry.py)
