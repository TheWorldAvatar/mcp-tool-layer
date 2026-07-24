# Chemistry stack — `theworldavatar.io/chemistry`

Public HTTPS front for TWA chemistry knowledge graphs (Blazegraph) and the Marie demo. Probed by mini_marie on **2026-06-09**.

**Host:** `https://theworldavatar.io/chemistry/blazegraph`  
**Pattern:** `https://theworldavatar.io/chemistry/blazegraph/namespace/{name}/sparql`  
**Workbench UI:** [https://theworldavatar.io/chemistry/blazegraph/ui/#splash](https://theworldavatar.io/chemistry/blazegraph/ui/#splash)

MOF data (`ontomofs`) is **not** on this Blazegraph host; it lives on a separate Ontop stack (see [OntoMOFs](#ontomofs-separate-ontop-stack) below).

**T-Box (OWL) files:** local copies under [`tbox/`](tbox/README.md), downloaded from [TheWorldAvatar/ontology](https://github.com/TheWorldAvatar/ontology) (Marie [ontology-info](https://theworldavatar.io/demos/marie/ontology-info) page). Refresh with `python -m mini_marie.marie.chemistry.download_tbox`.

---

## Access rules (required for queries)

| Rule | Detail |
|------|--------|
| HTTP method | **GET** with `?query=` URL-encoded SPARQL |
| POST | Often **403 Forbidden** — prefer GET (same pattern as [SG_OLD_ENDPOINTS.md](../SG_OLD_ENDPOINTS.md)) |
| User-Agent | Default Python `urllib` may get **403**; use e.g. `curl/8.0` |
| Probe scripts | `mini_marie.probe_chemistry_namespaces`, `mini_marie.probe_chemistry_blazegraph`, `mini_marie.probe_marie_backend` |

Example:

```bash
curl -G -A "curl/8.0" \
  --data-urlencode 'query=SELECT (COUNT(*) AS ?n) WHERE { ?s ?p ?o }' \
  "https://theworldavatar.io/chemistry/blazegraph/namespace/ontospecies/sparql"
```

---

## Blazegraph namespaces (8 reachable)

All namespaces below respond **200** on the public chemistry host. Triple counts from `python -m mini_marie.probe_chemistry_namespaces` (2026-06-09).

| Namespace | Endpoint | Triples | Role |
|-----------|----------|--------:|------|
| **ontospecies** | [sparql](https://theworldavatar.io/chemistry/blazegraph/namespace/ontospecies/sparql) | 50,639,123 | Species, geometries, PubChem-linked chemical data |
| **ontozeolite** | [sparql](https://theworldavatar.io/chemistry/blazegraph/namespace/ontozeolite/sparql) | 21,655,971 | Zeolite frameworks and atomic structures |
| **ontokin** | [sparql](https://theworldavatar.io/chemistry/blazegraph/namespace/ontokin/sparql) | 63,573 | OntoKin ontology (kinetics / quantity kinds) |
| **ontocompchem** | [sparql](https://theworldavatar.io/chemistry/blazegraph/namespace/ontocompchem/sparql) | 5,247 | Computational chemistry (often cross-linked with OntoSpecies) |
| **ontoprovenance** | [sparql](https://theworldavatar.io/chemistry/blazegraph/namespace/ontoprovenance/sparql) | 173 | Authors, publications, provenance metadata |
| **kb** | [sparql](https://theworldavatar.io/chemistry/blazegraph/namespace/kb/sparql) | 0 | Empty namespace (reserved / legacy) |
| **ontomops** | [sparql](https://theworldavatar.io/chemistry/blazegraph/namespace/ontomops/sparql) | 0 | MOPs namespace exists but has no triples here |
| **ontopesscan** | [sparql](https://theworldavatar.io/chemistry/blazegraph/namespace/ontopesscan/sparql) | 0 | Empty namespace (reserved / legacy) |

**Not on chemistry Blazegraph (404):** `ontomofs`, `ontomof`.

---

## RDF IRI prefixes (inside triples)

Blazegraph **namespace names** (URL path segments) differ from the **RDF IRI roots** used in `?s ?p ?o`. Common prefixes observed in live samples:

| Blazegraph ns | Instance / data IRIs | Ontology / T-box IRIs |
|---------------|----------------------|------------------------|
| **ontospecies** | `http://www.theworldavatar.com/kb/ontospecies/` | `http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#` |
| **ontocompchem** | (geometry / compchem individuals; often under `kb/ontospecies/`) | — |
| **ontokin** | — | `http://www.theworldavatar.com/ontology/ontokin/OntoKin.owl#` |
| **ontozeolite** | `http://www.theworldavatar.com/kg/ontozeolite/` | related: `http://www.theworldavatar.com/kg/ontocrystal/` |
| **ontoprovenance** | `http://www.theworldavatar.com/kb/ontoprovenance/` | `http://www.theworldavatar.com/ontology/ontoprovenance/OntoProvenance.owl#` |
| **ontomops** | — | `https://www.theworldavatar.com/kg/ontomops/` (used in repo; empty on this host) |

**SPARQL prefix examples:**

```sparql
PREFIX ontospecies: <http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#>
PREFIX ontokin:      <http://www.theworldavatar.com/ontology/ontokin/OntoKin.owl#>
PREFIX ontozeolite:  <http://www.theworldavatar.com/kg/ontozeolite/>
PREFIX ontomops:     <https://www.theworldavatar.com/kg/ontomops/>
```

---

## OntoMOFs (separate Ontop stack)

Metal–organic framework data is **not** served from `theworldavatar.io/chemistry/blazegraph`.

| Item | Address |
|------|---------|
| **Host** | `http://68.183.227.15:3840` |
| **SPARQL (Ontop)** | `http://68.183.227.15:3840/ontop/sparql/` |
| **RDF prefix** | `https://www.theworldavatar.com/kg/ontomofs_vkg/` |
| **MOF instances** | ~850,701 (COUNT probe, 2026-06-09) |

Example MOF count query:

```sparql
SELECT (COUNT(?m) AS ?n) WHERE {
  ?m a <https://www.theworldavatar.com/kg/ontomofs_vkg/MetalOrganicFramework> .
}
```

See also `mini_marie.mop_mof.mof` and `configs/mof_twa.json` for the MOF MCP workflow.

---

## Marie demo (UI + API)

The Marie v6 demo sits on the same TWA host and queries the chemistry KGs above.

| Item | Address |
|------|---------|
| **Demo home** | `https://theworldavatar.io/demos/marie` |
| **Search (species)** | `https://theworldavatar.io/demos/marie/search/species` |
| **Search (zeolite frameworks)** | `https://theworldavatar.io/demos/marie/search/zeolite-frameworks` |
| **Search (zeolitic materials)** | `https://theworldavatar.io/demos/marie/search/zeolitic-materials` |
| **Plots** | `/demos/marie/plots/species`, `/demos/marie/plots/zeolite-frameworks` |
| **Ontology / history info** | `/demos/marie/ontology-info`, `/demos/marie/history-info` |

API paths probed by `mini_marie.probe_marie_backend` (availability varies):

- `/demos/marie/api/query`, `/demos/marie/api/search`, `/demos/marie/api/ask`, `/demos/marie/api/chat`
- `/chemistry/marie/query`, `/chemistry/marie-agent/query`, `/chemistry/agent/query`

---

## Legacy / alternate hosts

These mirror the same `{ns}` Blazegraph names but may be internal or stale. Prefer the public `theworldavatar.io` host.

| Host key | Pattern |
|----------|---------|
| `178_128_3838` | `http://178.128.105.213:3838/blazegraph/namespace/{ns}/sparql` |
| `68_183_3838` | `http://68.183.227.15:3838/blazegraph/namespace/{ns}/sparql` |
| `68_183_3840_ontop_mof` | `http://68.183.227.15:3840/ontop/sparql/` (OntoMOFs only) |

Cross-host probe: `python -m mini_marie.probe_chemistry_hosts`

---

## Local probe cache

Latest namespace discovery is written under:

```
data/mini_marie_cache/chemistry_blazegraph/
├── namespace_probe.json    # triple counts + endpoints (fast probe)
├── probe_report.json       # full discovery + download estimates (slow)
└── hosts_probe.json        # multi-host comparison
```

**Refresh probes:**

```bash
python -m mini_marie.probe_chemistry_namespaces   # fast: counts per namespace
python -m mini_marie.probe_chemistry_blazegraph   # slow: discovery + size estimates
python -m mini_marie.probe_chemistry_hosts        # compare TWA vs legacy IPs
python -m mini_marie.probe_marie_backend          # Marie demo routes + chemistry_kg counts
```

---

## Competency cache (SQLite)

Full-scale warm cache for Marie chemistry competency tools. Endpoints are **fragile** — warm incrementally with delays, never unbounded list-all queries.

See [`mini_marie/marie/chemistry/CHEMISTRY_CACHING.md`](../../chemistry/CHEMISTRY_CACHING.md) for architecture and safety rules.

**Full-space option catalog:** each tool argument variant (A, B, C, …) is a separate cache entry. Use `--missing-only --batch N` to warm the rest bit-by-bit.

| Item | Value |
|------|-------|
| DB | `data/mini_marie_cache/chemistry/chemistry_cache.sqlite` |
| Option catalog | `mini_marie/marie/chemistry/warm_option_catalog.json` |
| Discover enums | `python -m mini_marie.marie.chemistry.discover_warm_options` |
| Probe tier | LIMIT 5 (MCP / online probe) |
| Full tier | Namespace warm caps (10k–500k rows) |
| Inter-call delay | 3s default between warm requests |

```bash
# Discover all 256 zeolite framework codes (one SPARQL query)
python -m mini_marie.marie.chemistry.discover_warm_options --target framework_codes

# Coverage: function_x(A) cached, still need B,C,D,…
python -m mini_marie.marie.chemistry.cache_status --coverage

# Warm next 10 missing variants only
python -m mini_marie.marie.chemistry.warm_chemistry_cache --full-space --missing-only --batch 10 --delay 3

# One dimension at a time
python -m mini_marie.marie.chemistry.warm_chemistry_cache \
  --full-space --dimension ontozeolite_framework_materials --missing-only --batch 20
```

---

## Related repo files

| File | Purpose |
|------|---------|
| `mini_marie/probe_chemistry_namespaces.py` | Canonical namespace list + OntoMOFs sidecar |
| `mini_marie/probe_chemistry_blazegraph.py` | Namespace discovery + download size estimates |
| `mini_marie/probe_chemistry_hosts.py` | Multi-host endpoint matrix |
| `mini_marie/probe_marie_backend.py` | Marie demo + SPARQL endpoint map |
| `mini_marie/marie/chemistry/download_tbox.py` | Download T-Box OWL from TheWorldAvatar/ontology |
| `mini_marie/marie/chemistry/warm_chemistry_cache.py` | Full-tier cache warm CLI (fragile-endpoint safe) |
| `mini_marie/marie/chemistry/chemistry_cache.py` | SQLite cache + invoke_tool tiers |
| `mini_marie/marie/chemistry/CHEMISTRY_CACHING.md` | Cache architecture and commands |
| `configs/chemistry.json` | Chemistry MCP server (RDKit tools — not Blazegraph) |
