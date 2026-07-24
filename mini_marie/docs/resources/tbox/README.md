# Chemistry T-Box files

Local copies of OWL/TTL **T-Box** (ontology schema) files for Marie chemistry namespaces.

**Primary source:** [TheWorldAvatar/ontology](https://github.com/TheWorldAvatar/ontology) on GitHub (linked from [Marie ontology-info](https://theworldavatar.io/demos/marie/ontology-info)).

**Refresh:**

```bash
python -m mini_marie.marie.chemistry.download_tbox
```

---

## Files by namespace

| Namespace | Local file(s) | Classes | Properties | Notes |
|-----------|---------------|--------:|-----------:|-------|
| **ontospecies** | `ontospecies/OntoSpecies_v2.owl` (+ v1 `OntoSpecies.owl`) | 183 | 119 | Primary T-Box for species KG |
| **ontokin** | `ontokin/OntoKin.owl` | 58 | 136 | Kinetics / reaction mechanisms |
| **ontocompchem** | `ontocompchem/ontocompchem.owl` | 71 | 46 | QM calculation ontology |
| **ontozeolite** | `ontozeolite/ontozeolite.owl`, `ontozeolite/ontocrystal.owl` | 73 | 126 | Zeolite + crystal ontologies |
| **ontomops** | `ontomops/ontomops-ogm.ttl` | 26 | 44 | MOPs T-Box; Blazegraph `ontomops` ns empty |
| **ontoprovenance** | `ontoprovenance/OntoProvenance.owl` | 15 | 29 | Provenance metadata |
| **ontopesscan** | `ontopesscan/OntoPESScan.owl` | 12 | 13 | PES scan ontology |

Counts from `mini_marie.marie.chemistry.tbox_index` (rdflib parse).

---

## SPARQL extraction status

Full T-Box **CONSTRUCT** from live Blazegraph failed for large namespaces (`ontospecies`, `ontozeolite` → HTTP 524) and returned only partial axioms for small ones. Use these GitHub-hosted OWL files instead.

Marie page links still reference `cambridge-cares/TheWorldAvatar` paths; files live in **`TheWorldAvatar/ontology`**.

---

## MCP servers

One MCP per namespace in `configs/mini_marie_mcps.json`:

| MCP key | Module |
|---------|--------|
| `chemistry-ontospecies` | `mini_marie.marie.chemistry.ontospecies.main` |
| `chemistry-ontokin` | `mini_marie.marie.chemistry.ontokin.main` |
| `chemistry-ontocompchem` | `mini_marie.marie.chemistry.ontocompchem.main` |
| `chemistry-ontozeolite` | `mini_marie.marie.chemistry.ontozeolite.main` |
| `chemistry-ontomops` | `mini_marie.marie.chemistry.ontomops.main` |
| `chemistry-ontoprovenance` | `mini_marie.marie.chemistry.ontoprovenance.main` |
| `chemistry-ontopesscan` | `mini_marie.marie.chemistry.ontopesscan.main` |

Shared tools: `get_namespace_info`, `list_ontology_classes`, `list_ontology_properties`, `get_live_triple_count`, `get_top_instance_types`, `sample_live_triples`, plus **Marie competency tools** (see [`marie/marie_competency_questions.md`](../marie/marie_competency_questions.md)).
