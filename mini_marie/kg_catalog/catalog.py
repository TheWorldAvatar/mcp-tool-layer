"""Plain functions for KG catalog (importable without FastMCP)."""

from __future__ import annotations

import json
from pathlib import Path

from mini_marie.cache_paths import mini_marie_cache_root

DOMAINS = [
    {
        "id": "mof",
        "mcp_server": "mof-twa",
        "module": "mini_marie.mop_mof.mof.main",
        "endpoint": "http://68.183.227.15:3840/ontop/sparql/",
        "cache": "mini_marie_cache/mof_competency/competency_cache.sqlite",
        "scale": "~850k MOF individuals",
    },
    {
        "id": "city",
        "mcp_server": "twa-city",
        "module": "mini_marie.zaha.twa_city.main",
        "endpoint": "bremen + kaiserslautern cmpg Ontop",
        "cache": "mini_marie_cache/twa_city/city_cache.sqlite",
        "scale": "Bremen + Kaiserslautern buildings",
    },
    {
        "id": "mops",
        "mcp_server": "twa-mops",
        "module": "mini_marie.mop_mof.mops.main",
        "endpoint": "local merged_tll RDF",
        "cache": "label index under mini_marie_cache",
        "scale": "MOP synthesis local graph",
    },
    {
        "id": "sg_ontop",
        "mcp_server": "sg-old",
        "endpoint": "https://sg-old.theworldavatar.io/ontop/sparql/",
        "cache": "mini_marie_cache/sg_old/ontop_cache.sqlite",
        "scale": "~114k buildings + land-use/GFA",
    },
    {
        "id": "sg_carpark",
        "mcp_server": "sg-old",
        "endpoint": "sg-old .../namespace/carpark/sparql",
        "cache": "mini_marie_cache/sg_old/sg_cache.sqlite",
        "scale": "~4k triples",
    },
    {
        "id": "sg_kb",
        "mcp_server": "sg-old",
        "endpoint": "sg-old .../namespace/kb/sparql",
        "cache": "mini_marie_cache/sg_old/sg_cache.sqlite",
        "scale": "~115k triples (dispersion/emissions)",
    },
    {
        "id": "sg_plot",
        "mcp_server": "sg-old",
        "endpoint": "sg-old .../namespace/plot/sparql",
        "cache": "mini_marie_cache/sg_old/sg_cache.sqlite",
        "scale": "~216 triples",
    },
    {
        "id": "sg_company",
        "mcp_server": "sg-old",
        "endpoint": "sg-old .../namespace/company/sparql",
        "cache": "mini_marie_cache/sg_old/sg_cache.sqlite",
        "scale": "OWL T-box (~989 triples)",
    },
    {
        "id": "chemistry_ontospecies",
        "mcp_server": "chemistry-ontospecies",
        "module": "mini_marie.marie.chemistry.ontospecies.main",
        "endpoint": "https://theworldavatar.io/chemistry/blazegraph/namespace/ontospecies/sparql",
        "cache": "mini_marie_cache/chemistry/chemistry_cache.sqlite",
        "scale": "OntoSpecies chemical species",
    },
    {
        "id": "chemistry_ontokin",
        "mcp_server": "chemistry-ontokin",
        "module": "mini_marie.marie.chemistry.ontokin.main",
        "endpoint": "https://theworldavatar.io/chemistry/blazegraph/namespace/ontokin/sparql",
        "cache": "mini_marie_cache/chemistry/chemistry_cache.sqlite",
        "scale": "OntoKin reaction mechanisms",
    },
    {
        "id": "chemistry_ontocompchem",
        "mcp_server": "chemistry-ontocompchem",
        "module": "mini_marie.marie.chemistry.ontocompchem.main",
        "endpoint": "https://theworldavatar.io/chemistry/blazegraph/namespace/ontocompchem/sparql",
        "cache": "mini_marie_cache/chemistry/chemistry_cache.sqlite",
        "scale": "OntoCompChem calculations",
    },
    {
        "id": "chemistry_ontozeolite",
        "mcp_server": "chemistry-ontozeolite",
        "module": "mini_marie.marie.chemistry.ontozeolite.main",
        "endpoint": "https://theworldavatar.io/chemistry/blazegraph/namespace/ontozeolite/sparql",
        "cache": "mini_marie_cache/chemistry/chemistry_cache.sqlite",
        "scale": "OntoZeolite frameworks",
    },
    {
        "id": "chemistry_ontomops",
        "mcp_server": "chemistry-ontomops",
        "module": "mini_marie.marie.chemistry.ontomops.main",
        "endpoint": "https://theworldavatar.io/chemistry/blazegraph/namespace/ontomops/sparql",
        "cache": "mini_marie_cache/chemistry/chemistry_cache.sqlite",
        "scale": "OntoMOPs polyhedra T-box",
    },
    {
        "id": "chemistry_ontoprovenance",
        "mcp_server": "chemistry-ontoprovenance",
        "module": "mini_marie.marie.chemistry.ontoprovenance.main",
        "endpoint": "https://theworldavatar.io/chemistry/blazegraph/namespace/ontoprovenance/sparql",
        "cache": "mini_marie_cache/chemistry/chemistry_cache.sqlite",
        "scale": "OntoProvenance metadata",
    },
    {
        "id": "chemistry_ontopesscan",
        "mcp_server": "chemistry-ontopesscan",
        "module": "mini_marie.marie.chemistry.ontopesscan.main",
        "endpoint": "https://theworldavatar.io/chemistry/blazegraph/namespace/ontopesscan/sparql",
        "cache": "mini_marie_cache/chemistry/chemistry_cache.sqlite",
        "scale": "OntoPESScan scans",
    },
]


def list_kg_domains_text() -> str:
    lines = ["id\tmcp_server\tendpoint\tscale"]
    for d in DOMAINS:
        lines.append(f"{d['id']}\t{d['mcp_server']}\t{d['endpoint']}\t{d['scale']}")
    return "\n".join(lines)


def describe_kg_domain_text(domain_id: str) -> str:
    for d in DOMAINS:
        if d["id"] == domain_id.strip().lower():
            return json.dumps(d, indent=2)
    return f"Unknown domain. Known: {[d['id'] for d in DOMAINS]}"


def kg_cache_status_text() -> str:
    root = mini_marie_cache_root()
    checks = [
        root / "mof_competency" / "competency_cache.sqlite",
        root / "twa_city" / "city_cache.sqlite",
        root / "sg_old" / "sg_cache.sqlite",
        root / "sg_old" / "ontop_cache.sqlite",
        root / "sg_old" / "carpark_triples.ndjson",
        root / "sg_old" / "kb_triples.ndjson",
        root / "chemistry" / "chemistry_cache.sqlite",
    ]
    lines = ["path\texists\tmb"]
    for p in checks:
        mb = round(p.stat().st_size / 1_048_576, 2) if p.exists() else 0
        lines.append(f"{p.relative_to(root)}\t{p.exists()}\t{mb}")
    return "\n".join(lines)
