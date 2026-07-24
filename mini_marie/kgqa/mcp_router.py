"""Dynamic MCP server selection for KGQA questions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Sequence

from mini_marie.kg_catalog import catalog as kg_catalog
from mini_marie.kgqa.question_catalog import CatalogEntry, match_catalog

MAX_MCP_SERVERS = 3

CHEMISTRY_KEYWORDS = {
    "chemistry-ontospecies": [
        "species",
        "formula",
        "inchi",
        "smiles",
        "pka",
        "pubchem",
        "chebi",
        "ontospecies",
        "methylamine",
        "ionic strength",
    ],
    "chemistry-ontokin": ["reaction", "mechanism", "kinetic", "ontokin", "thermo"],
    "chemistry-ontocompchem": ["gaussian", "calculation", "orbital", "ontocompchem", "dft"],
    "chemistry-ontozeolite": [
        "zeolite",
        "zeolitic",
        "framework",
        "iza",
        "aen",
        "ontozeolite",
        "unit cell",
        "framework code",
    ],
    "chemistry-ontomops": ["polyhedra", "cbu", "ontomops", "mop cage", "outer diameter", "angstrom"],
    "chemistry-ontoprovenance": ["provenance", "ontoprovenance"],
    "chemistry-ontopesscan": ["pesscan", "scan", "ontopesscan"],
}

MOF_KEYWORDS = [
    "mof",
    "uio-66",
    "zif-8",
    "zif8",
    "pld",
    "lcd",
    "topology",
    "tobassco",
    "co2 uptake",
    "refcode",
    "metal-organic framework",
    "hkust",
    "mil-101",
    "dut-67",
]

CITY_KEYWORDS = ["bremen", "kaiserslautern", "wkt", "geo", "city"]

MOPS_KEYWORDS = [
    "vmop",
    "irmop",
    "mop",
    "mops",
    "polyhedra",
    "mop synthesis",
    "synthesis route",
    "ccdc",
    "merged_tll",
    "outer diameter",
    "inner sphere",
    "icosahedral",
    "cbu",
]

SG_KEYWORDS = {
    "sg-old": [
        "singapore",
        "sg-old",
        "carpark",
        "emission",
        "dispersion",
        "land-use",
        "plot regulation",
        "jurong",
        "pollutant",
        "concentration",
        "gfa",
        "land lot",
        "land plot",
        "landplot",
        "create tower",
        "mmsi",
        "virtual sensor",
        "ontoplot",
        "availablelots",
        "zoning",
    ],
}


@dataclass
class RouteResult:
    mcp_servers: List[str]
    catalog_entry: Optional[CatalogEntry] = None
    domain: str = "unknown"
    reason: str = ""


def _dedupe_keep_order(names: Sequence[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for name in names:
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


def _score_keywords(question: str, keywords: Sequence[str]) -> int:
    q = question.lower()
    return sum(1 for kw in keywords if kw in q)


def _heuristic_route(question: str) -> RouteResult:
    q = question.lower()
    scores: List[tuple[int, str]] = []

    sg_score = _score_keywords(q, SG_KEYWORDS["sg-old"])
    if sg_score:
        scores.append((sg_score + 2, "sg-old"))
    mops_score = _score_keywords(q, MOPS_KEYWORDS)
    if mops_score:
        scores.append((mops_score + 1, "twa-mops"))
    if _score_keywords(q, CITY_KEYWORDS):
        scores.append((_score_keywords(q, CITY_KEYWORDS), "twa-city"))
    for mcp, kws in CHEMISTRY_KEYWORDS.items():
        s = _score_keywords(q, kws)
        if s:
            scores.append((s, mcp))
    if _score_keywords(q, MOF_KEYWORDS):
        scores.append((_score_keywords(q, MOF_KEYWORDS), "mof-twa"))

    scores.sort(key=lambda x: (-x[0], x[1]))
    servers = _dedupe_keep_order([s[1] for s in scores])[:MAX_MCP_SERVERS]

    domain = "unknown"
    if servers:
        if servers[0].startswith("chemistry-"):
            domain = "chemistry"
        elif servers[0] == "mof-twa":
            domain = "mof"
        elif servers[0] == "twa-city":
            domain = "city"
        elif servers[0] == "twa-mops":
            domain = "mops"
        elif servers[0] == "sg-old":
            domain = "sg"
    else:
        servers = ["kg-catalog"]
        domain = "catalog"
        return RouteResult(
            mcp_servers=servers[:MAX_MCP_SERVERS],
            domain=domain,
            reason="fallback: kg-catalog only",
        )

    if "kg-catalog" not in servers and len(servers) < MAX_MCP_SERVERS:
        servers = _dedupe_keep_order(["kg-catalog"] + servers)[:MAX_MCP_SERVERS]

    return RouteResult(
        mcp_servers=servers,
        domain=domain,
        reason="keyword heuristic",
    )


def route_question(question: str) -> RouteResult:
    """Resolve MCP servers for a natural-language question."""
    entry = match_catalog(question)
    if entry and entry.mcp_servers:
        servers = _dedupe_keep_order(entry.mcp_servers)[:MAX_MCP_SERVERS]
        if len(servers) < MAX_MCP_SERVERS and "kg-catalog" not in servers:
            servers = _dedupe_keep_order(["kg-catalog"] + servers)[:MAX_MCP_SERVERS]
        return RouteResult(
            mcp_servers=servers,
            catalog_entry=entry,
            domain=entry.domain,
            reason="catalog match",
        )

    heuristic = _heuristic_route(question)
    return heuristic


def domain_for_recording(recording_path: str, workflow_id: Optional[str] = None) -> str:
    """Infer replay domain from path or workflow id prefix."""
    path = recording_path.replace("\\", "/").lower()
    wf = (workflow_id or "").lower()

    if "/chemistry/competency_runs/" in path or wf.startswith("mq"):
        return "chemistry"
    if "/mof_case/competency_runs/" in path or wf.startswith("cq"):
        return "mof_competency"
    if "/twa_city/" in path or "city" in path:
        return "city"
    if "/twa_mops/" in path or "mops" in path:
        return "mops"
    if "/mof_case/workflow_runs/" in path:
        return "mof_workflow"
    if wf.startswith("xq"):
        return "cross_kg"
    return "unknown"
