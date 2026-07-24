"""
Registry of chemistry corpus facets: one materialized index per MCP query pattern.

Competency questions guide which facets to build first; goal is full KG coverage
from multiple MCP angles (resolve, traverse, filter, aggregate) — not one index.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional

QueryPattern = Literal[
    "resolve",       # entity lookup by name/label/identifier
    "traverse",      # follow object property from subject
    "filter",        # find subjects by literal property value
    "graph",         # multi-hop or join (mechanism→reaction, fw→material)
    "aggregate",     # count/rank/group (derived from corpus or local SQL)
    "property_scan", # numeric/threshold filter on cached literals
]

CorpusStatus = Literal["implemented", "partial", "planned"]


@dataclass(frozen=True)
class CorpusFacet:
    id: str
    namespace: str
    query_pattern: QueryPattern
    mcp_tools: List[str]
    description: str
    warm_module: Optional[str]
    warm_cli: Optional[str]
    status: CorpusStatus
    marie_mq_hints: List[str] = field(default_factory=list)
    estimated_rows: Optional[str] = None
    sqlite_tables: List[str] = field(default_factory=list)


# --- OntoSpecies (50M triples, ~37k Species) ---

ONTO_SPECIES_FACETS: List[CorpusFacet] = [
    CorpusFacet(
        id="ontospecies_names",
        namespace="ontospecies",
        query_pattern="resolve",
        mcp_tools=["search_species_names", "lookup_individuals"],
        description="All species + searchable aliases (label, IUPAC, SMILES, InChI, formula, CID).",
        warm_module="mini_marie.marie.chemistry.species_corpus",
        warm_cli="python -m mini_marie.marie.chemistry.warm_species_corpus",
        status="implemented",
        marie_mq_hints=["MQ2", "MQ3", "MQ5", "MQ17"],
        estimated_rows="~150k–250k name rows",
        sqlite_tables=["corpus_species", "corpus_species_names"],
    ),
    CorpusFacet(
        id="ontospecies_pka",
        namespace="ontospecies",
        query_pattern="traverse",
        mcp_tools=["query_species_pka", "get_linked_values(hasDissociationConstants)"],
        description="All pKa measurements + metadata (T, ionic strength, method, reliability, acidity label).",
        warm_module="mini_marie.marie.chemistry.species_pka_corpus",
        warm_cli="python -m mini_marie.marie.chemistry.warm_species_pka_corpus",
        status="implemented",
        marie_mq_hints=["MQ4–MQ19"],
        estimated_rows="~100k+ measurement rows",
        sqlite_tables=["corpus_species_pka"],
    ),
    CorpusFacet(
        id="ontospecies_formula_index",
        namespace="ontospecies",
        query_pattern="filter",
        mcp_tools=["filter_by_literal(hasMolecularFormula)", "list_species_by_formula"],
        description="Exact formula → species IRIs (derived from names corpus).",
        warm_module="mini_marie.marie.chemistry.species_formula_corpus",
        warm_cli="python -m mini_marie.marie.chemistry.build_species_formula_index --build",
        status="implemented",
        marie_mq_hints=["MQ3"],
        sqlite_tables=["corpus_species_formula"],
    ),
    CorpusFacet(
        id="ontospecies_joins",
        namespace="ontospecies",
        query_pattern="graph",
        mcp_tools=[
            "query_pka_with_provenance",
            "query_species_pka",
            "search_species_uses",
            "query_species_physprops",
        ],
        description=(
            "Competency join tables (identifiers spine, pKa+prov, uses+id, physprops wide, profile counts) "
            "built locally from warmed corpus facets."
        ),
        warm_module="mini_marie.marie.chemistry.species_join_corpus",
        warm_cli="python -m mini_marie.marie.chemistry.build_species_join_index --build",
        status="implemented",
        marie_mq_hints=["MQ1", "MQ2", "MQ4–MQ19"],
        estimated_rows="derived from corpus (~3k pKa enriched, ~23k uses, ~37k profile)",
        sqlite_tables=[
            "corpus_species_identifiers",
            "corpus_species_pka_enriched",
            "corpus_species_uses_enriched",
            "corpus_species_physprops_wide",
            "corpus_species_profile",
        ],
    ),
    CorpusFacet(
        id="ontospecies_uses",
        namespace="ontospecies",
        query_pattern="traverse",
        mcp_tools=["search_species_uses", "get_linked_values(hasUse)"],
        description="Species → use/application literals (inverted index for 'find uses of X').",
        warm_module="mini_marie.marie.chemistry.species_uses_corpus",
        warm_cli="python -m mini_marie.marie.chemistry.warm_species_uses_corpus",
        status="implemented",
        marie_mq_hints=["MQ2"],
        estimated_rows="~50k",
        sqlite_tables=["corpus_species_uses"],
    ),
    CorpusFacet(
        id="ontospecies_physprops",
        namespace="ontospecies",
        query_pattern="traverse",
        mcp_tools=["query_species_physprops", "get_linked_values(H-bond, PSA, …)"],
        description="PubChem-style scalar properties per species (donors, acceptors, mass, …).",
        warm_module="mini_marie.marie.chemistry.species_physprops_corpus",
        warm_cli="python -m mini_marie.marie.chemistry.warm_species_physprops_corpus",
        status="implemented",
        marie_mq_hints=["MQ1"],
        sqlite_tables=["corpus_species_properties"],
    ),
]

# --- OntoKin (~64k triples) ---

ONTOKIN_FACETS: List[CorpusFacet] = [
    CorpusFacet(
        id="ontokin_mechanisms",
        namespace="ontokin",
        query_pattern="resolve",
        mcp_tools=["lookup_individuals(ReactionMechanism)", "search_mechanisms"],
        description="Mechanism IRIs + labels + source DOI/IRI fragments.",
        warm_module="mini_marie.marie.chemistry.ontokin_corpus",
        warm_cli="python -m mini_marie.marie.chemistry.warm_ontokin_corpus --facet mechanisms",
        status="implemented",
        marie_mq_hints=["MQ20", "MQ23", "MQ28"],
        sqlite_tables=["corpus_mechanisms"],
    ),
    CorpusFacet(
        id="ontokin_reaction_graph",
        namespace="ontokin",
        query_pattern="graph",
        mcp_tools=["traverse_mechanism_reactions", "search_reactions"],
        description="Mechanism → reaction → equation rows; substring index on equations.",
        warm_module="mini_marie.marie.chemistry.ontokin_corpus",
        warm_cli="python -m mini_marie.marie.chemistry.warm_ontokin_corpus --facet graph",
        status="implemented",
        marie_mq_hints=["MQ21–MQ25"],
        estimated_rows="~25k",
        sqlite_tables=["corpus_reaction_edges"],
    ),
    CorpusFacet(
        id="ontokin_rate_models",
        namespace="ontokin",
        query_pattern="traverse",
        mcp_tools=["get_linked_values(rate constants)", "compare_rate_constants"],
        description="Rate parameters + kinetic/transport/thermo models per reaction.",
        warm_module="mini_marie.marie.chemistry.ontokin_rate_corpus",
        warm_cli="python -m mini_marie.marie.chemistry.warm_ontokin_rate_corpus",
        status="implemented",
        marie_mq_hints=["MQ26–MQ30"],
        sqlite_tables=["corpus_reaction_models"],
    ),
]

# --- OntoCompChem (~5k triples) ---

ONTOCOMPCHEM_FACETS: List[CorpusFacet] = [
    CorpusFacet(
        id="ontocompchem_results",
        namespace="ontocompchem",
        query_pattern="filter",
        mcp_tools=["query_calculation_results", "list_qm_results"],
        description="Calculation results by species fragment × method × basis × result kind.",
        warm_module="mini_marie.marie.chemistry.compchem_corpus",
        warm_cli="python -m mini_marie.marie.chemistry.warm_compchem_corpus",
        status="implemented",
        marie_mq_hints=["MQ31–MQ33"],
        sqlite_tables=["corpus_qm_results"],
    ),
]

# --- OntoZeolite (~22M triples) ---

ONTOZEOLITE_FACETS: List[CorpusFacet] = [
    CorpusFacet(
        id="ontozeolite_framework_index",
        namespace="ontozeolite",
        query_pattern="filter",
        mcp_tools=["filter_by_literal(hasFrameworkCode)", "list_materials_by_framework"],
        description="Framework code → all zeolitic materials (256 codes).",
        warm_module="mini_marie.marie.chemistry.zeolite_corpus",
        warm_cli="python -m mini_marie.marie.chemistry.warm_zeolite_corpus",
        status="implemented",
        marie_mq_hints=["MQ34", "MQ42"],
        estimated_rows="~2k materials",
        sqlite_tables=["corpus_zeolite_by_framework"],
    ),
    CorpusFacet(
        id="ontozeolite_material_names",
        namespace="ontozeolite",
        query_pattern="resolve",
        mcp_tools=["query_zeolite_property(material_label)", "search_zeolite_materials"],
        description="Material formula/label search (|(Quin)|[Si34O68], |Na20|[Al20Si76O192], …).",
        warm_module="mini_marie.marie.chemistry.zeolite_corpus",
        warm_cli="python -m mini_marie.marie.chemistry.warm_zeolite_corpus",
        status="implemented",
        marie_mq_hints=["MQ36", "MQ37", "MQ48"],
        sqlite_tables=["corpus_zeolite_materials"],
    ),
    CorpusFacet(
        id="ontozeolite_material_properties",
        namespace="ontozeolite",
        query_pattern="property_scan",
        mcp_tools=["filter_by_literal(hasLatticeSystem)", "filter_numeric(area,volume)"],
        description="Lattice, guest species, element composition, area/volume per material.",
        warm_module="mini_marie.marie.chemistry.zeolite_corpus",
        warm_cli="python -m mini_marie.marie.chemistry.warm_zeolite_corpus",
        status="implemented",
        marie_mq_hints=["MQ40–MQ47"],
        sqlite_tables=["corpus_zeolite_properties"],
    ),
    CorpusFacet(
        id="ontozeolite_reference_map",
        namespace="ontozeolite",
        query_pattern="filter",
        mcp_tools=["query_zeolite_property(isReferenceZeolite)"],
        description="Framework code → reference zeolite material.",
        warm_module="mini_marie.marie.chemistry.zeolite_corpus",
        warm_cli="python -m mini_marie.marie.chemistry.warm_zeolite_corpus",
        status="implemented",
        marie_mq_hints=["MQ35", "MQ38"],
        sqlite_tables=["corpus_zeolite_reference"],
    ),
]

# --- OntoProvenance (~173 triples) ---

ONTOPROVENANCE_FACETS: List[CorpusFacet] = [
    CorpusFacet(
        id="ontoprovenance_persons",
        namespace="ontoprovenance",
        query_pattern="resolve",
        mcp_tools=["lookup_individuals(Person)", "search_authors"],
        description="Person names + literals on Person individuals.",
        warm_module="mini_marie.marie.chemistry.provenance_corpus",
        warm_cli="python -m mini_marie.marie.chemistry.warm_provenance_corpus",
        status="implemented",
        marie_mq_hints=["MQ17", "MQ18"],
        sqlite_tables=["corpus_provenance_persons"],
    ),
    CorpusFacet(
        id="ontoprovenance_publications",
        namespace="ontoprovenance",
        query_pattern="graph",
        mcp_tools=["get_linked_values", "resolve_doi"],
        description="Publication / reference IRIs linked from measurements and entities.",
        warm_module="mini_marie.marie.chemistry.provenance_corpus",
        warm_cli="python -m mini_marie.marie.chemistry.warm_provenance_corpus",
        status="implemented",
        marie_mq_hints=["MQ17", "MQ48", "MQ55"],
        sqlite_tables=["corpus_provenance_refs"],
    ),
]

ALL_FACETS: List[CorpusFacet] = (
    ONTO_SPECIES_FACETS
    + ONTOKIN_FACETS
    + ONTOCOMPCHEM_FACETS
    + ONTOZEOLITE_FACETS
    + ONTOPROVENANCE_FACETS
)

FACETS_BY_NAMESPACE: Dict[str, List[CorpusFacet]] = {}
for facet in ALL_FACETS:
    FACETS_BY_NAMESPACE.setdefault(facet.namespace, []).append(facet)


def list_facets(
    *,
    namespace: Optional[str] = None,
    query_pattern: Optional[QueryPattern] = None,
    status: Optional[CorpusStatus] = None,
) -> List[CorpusFacet]:
    out = ALL_FACETS
    if namespace:
        out = [f for f in out if f.namespace == namespace]
    if query_pattern:
        out = [f for f in out if f.query_pattern == query_pattern]
    if status:
        out = [f for f in out if f.status == status]
    return out


def facet_summary() -> Dict[str, object]:
    by_ns: Dict[str, Dict[str, int]] = {}
    for f in ALL_FACETS:
        bucket = by_ns.setdefault(f.namespace, {"implemented": 0, "partial": 0, "planned": 0, "total": 0})
        bucket[f.status] += 1
        bucket["total"] += 1
    by_pattern: Dict[str, int] = {}
    for f in ALL_FACETS:
        by_pattern[f.query_pattern] = by_pattern.get(f.query_pattern, 0) + 1
    return {
        "namespaces": by_ns,
        "query_patterns": by_pattern,
        "total_facets": len(ALL_FACETS),
        "implemented": len(list_facets(status="implemented")),
    }
