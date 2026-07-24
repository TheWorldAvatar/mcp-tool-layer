# Chemistry competency coverage (Marie MQ1–MQ61)

Online MCP probes return **at most 5 rows** (`ONLINE_PROBE_LIMIT`). Full-tier results are cached for offline replay — see [`CHEMISTRY_CACHING.md`](CHEMISTRY_CACHING.md).

**Probe:** `python -m mini_marie.marie.chemistry.probe_competency`  
**Warm:** `python -m mini_marie.marie.chemistry.warm_chemistry_cache --comprehensive --missing-only`

## Generic tools (all data namespaces)

| Tool | Purpose |
|------|---------|
| `lookup_individuals` | Find individuals by class local name + label/formula/SMILES/InChI fragment |
| `get_linked_values` | Traverse object property; expands `os:value` reification on OntoSpecies |
| `filter_by_literal` | Filter by property literal (`match=contains\|equals`); framework code on zeolites |
| `count_instances` | `COUNT DISTINCT` with optional required property |

Namespace extensions: `traverse_mechanism_reactions` (ontokin), `query_calculation_results` (ontocompchem), `query_zeolite_property` (ontozeolite), `ontomops_instance_routing` (ontomops).

## MQ coverage

| MQ | Topic | Tool + parameters | Status |
|----|-------|-------------------|--------|
| MQ1 | H-bond donors/acceptors | `get_linked_values(Species, SMILES, hasHydrogenBondDonorCount, …)` | online-5 + warm |
| MQ2 | Species uses | `get_linked_values(Species, label, hasUse)` | online-5 + warm |
| MQ3 | Molecular formula | `filter_by_literal(Species, hasMolecularFormula, C6H8O6, equals)` | online-5 + warm |
| MQ4–7 | pKa by InChI/SMILES/label | `get_linked_values` + `identifier_type` | online-5 + warm |
| MQ8–11 | pKa temperature/ranking | `get_linked_values(..., include_metadata=true)` | partial (warm cap) |
| MQ12–16 | pKa metadata analytics | `get_linked_values` + metadata | partial |
| MQ17 | Provenance for species | `lookup_individuals(Person, …)` on ontoprovenance | partial |
| MQ18–19 | Perrin / acidity labels | `get_linked_values` + metadata | partial |
| MQ20 | List mechanisms | `lookup_individuals(ReactionMechanism)` | online-5 only (avoid unbounded warm) |
| MQ21–25 | Mechanism / reaction search | `traverse_mechanism_reactions(...)` | online-5 + warm |
| MQ26–30 | Rate / model comparisons | multi-mechanism joins | workflow + cache |
| MQ31–33 | QM energies | `query_calculation_results(...)` | online-5 + warm |
| MQ34 | Framework code materials | `filter_by_literal(..., hasFrameworkCode, AEN)` | online-5 + warm |
| MQ35 | Reference zeolite | `query_zeolite_property(framework_code=SFN, …)` | online-5 + warm |
| MQ36–48 | Zeolite structure/guests | zeolite tools | partial |
| MQ49–61 | MOP instances | route to **twa-mops** / **mof-twa** | route-to-twa-mops |

## Live data notes

- **OntoSpecies:** labels are often molecular formulas; use `identifier_type=smiles` or `inchi`.
- **OntoKin:** reactions use `ok:hasEquation`, not `rdfs:label`.
- **OntoCompChem:** filter by species UUID fragment in result IRI.
- **OntoZeolite:** materials link from framework via `oz:hasZeoliticMaterial`.
- **OntoProvenance:** person names on arbitrary literal predicates.
