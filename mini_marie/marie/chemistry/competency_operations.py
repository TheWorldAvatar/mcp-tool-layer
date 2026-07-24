"""Marie competency MCP operations — corpus-first offline, SPARQL fallback."""

from __future__ import annotations

from typing import Optional, Sequence

from mini_marie.marie.chemistry import query_builder as qb
from mini_marie.marie.chemistry.limits import ONLINE_PROBE_LIMIT, probe_limit
from mini_marie.marie.chemistry.sparql import format_tsv


def lookup_individuals(
    namespace: str,
    class_local: str,
    label_fragment: str = "",
    identifier_type: str = "",
) -> str:
    if namespace == "ontoprovenance" and class_local == "Person" and label_fragment.strip():
        from mini_marie.marie.chemistry.provenance_corpus import ProvenanceCorpusStore

        store = ProvenanceCorpusStore()
        try:
            if store.stats()["person_rows"] > 0:
                rows = store.search_persons(label_fragment, limit=5)
                if rows:
                    return format_tsv(
                        [{"subject": r["person_iri"], "label": r["name_value"]} for r in rows]
                    )
        finally:
            store.close()
    return qb.lookup_individuals(namespace, class_local, label_fragment, identifier_type)


def get_linked_values(
    namespace: str,
    class_local: str,
    subject_label: str,
    link_property: str,
    value_properties: Optional[Sequence[str]] = None,
    include_metadata: bool = False,
    identifier_type: str = "",
    related_class: str = "",
) -> str:
    return qb.get_linked_values(
        namespace,
        class_local,
        subject_label,
        link_property,
        value_properties,
        include_metadata,
        identifier_type,
        related_class,
    )


def filter_by_literal(
    namespace: str,
    class_local: str,
    property_local: str,
    value_fragment: str,
    match: str = "contains",
    use_value_node: bool = False,
    subject_label: str = "",
) -> str:
    if namespace == "ontospecies" and class_local == "Species" and property_local == "hasMolecularFormula":
        from mini_marie.marie.chemistry.species_formula_corpus import SpeciesFormulaStore

        store = SpeciesFormulaStore()
        try:
            if store.stats()["formula_rows"] > 0:
                rows = store.lookup_formula(value_fragment, match=match, limit=ONLINE_PROBE_LIMIT)
                if rows:
                    return store.lookup_formula_tsv(value_fragment, match=match, limit=ONLINE_PROBE_LIMIT)
        finally:
            store.close()

    if namespace == "ontospecies" and class_local == "Species" and property_local == "hasUse":
        from mini_marie.marie.chemistry.species_uses_corpus import SpeciesUsesCorpusStore

        store = SpeciesUsesCorpusStore()
        try:
            if store.stats()["use_rows"] > 0:
                return store.search_uses_tsv(value_fragment, limit=ONLINE_PROBE_LIMIT)
        finally:
            store.close()

    if namespace == "ontozeolite" and property_local == "hasFrameworkCode":
        from mini_marie.marie.chemistry.zeolite_corpus import ZeoliteCorpusStore

        store = ZeoliteCorpusStore()
        try:
            stats = store.stats()
            if stats["framework_index_rows"] > 0:
                rows = store.materials_by_framework(value_fragment, limit=ONLINE_PROBE_LIMIT)
                if rows:
                    return format_tsv(
                        [
                            {
                                "subject": r["material_iri"],
                                "label": r.get("label") or "",
                                "formula": r.get("formula") or "",
                                "framework_code": r.get("framework_code") or value_fragment,
                            }
                            for r in rows
                        ]
                    )
        finally:
            store.close()

    if namespace == "ontozeolite" and property_local in ("hasGuestSpecies", "hasGuestFormula"):
        from mini_marie.marie.chemistry.zeolite_corpus import ZeoliteCorpusStore

        store = ZeoliteCorpusStore()
        try:
            if store.stats()["property_rows"] > 0:
                return store.query_property_tsv(
                    property_local=property_local,
                    value_filter=value_fragment,
                    limit=ONLINE_PROBE_LIMIT,
                )
        finally:
            store.close()

    if namespace == "ontozeolite" and property_local == "hasLatticeSystem":
        from mini_marie.marie.chemistry.zeolite_corpus import ZeoliteCorpusStore

        store = ZeoliteCorpusStore()
        try:
            if store.stats()["property_rows"] > 0:
                return store.query_property_tsv(
                    property_local="hasLatticeSystem",
                    value_filter=value_fragment,
                    limit=ONLINE_PROBE_LIMIT,
                )
        finally:
            store.close()

    return qb.filter_by_literal(
        namespace,
        class_local,
        property_local,
        value_fragment,
        match,
        use_value_node,
        subject_label,
    )


def count_instances(
    namespace: str,
    class_local: str,
    required_property: Optional[str] = None,
) -> str:
    return qb.count_instances(namespace, class_local, required_property)


def traverse_mechanism_reactions(
    namespace: str,
    mechanism_label: str = "",
    reaction_fragment: str = "",
    mechanism_iri_fragment: str = "",
) -> str:
    if namespace == "ontokin":
        from mini_marie.marie.chemistry.ontokin_corpus import OntokinCorpusStore

        store = OntokinCorpusStore()
        try:
            if store.stats()["reaction_edge_rows"] > 0:
                return store.traverse_reactions_tsv(
                    mechanism_label=mechanism_label,
                    reaction_fragment=reaction_fragment,
                    mechanism_iri_fragment=mechanism_iri_fragment,
                    limit=ONLINE_PROBE_LIMIT,
                )
        finally:
            store.close()
    return qb.traverse_mechanism_reactions(
        namespace, mechanism_label, reaction_fragment, mechanism_iri_fragment
    )


def query_calculation_results(
    namespace: str,
    species_label: str,
    result_kinds: Optional[Sequence[str]] = None,
    method_fragment: str = "",
    basis_fragment: str = "",
) -> str:
    if namespace == "ontocompchem":
        from mini_marie.marie.chemistry.compchem_corpus import CompChemCorpusStore

        store = CompChemCorpusStore()
        try:
            if store.stats()["qm_result_rows"] > 0:
                return store.query_results_tsv(
                    species_label,
                    result_kinds=list(result_kinds) if result_kinds else None,
                    method_fragment=method_fragment,
                    basis_fragment=basis_fragment,
                    limit=ONLINE_PROBE_LIMIT,
                )
        finally:
            store.close()
    return qb.query_calculation_results(
        namespace, species_label, result_kinds, method_fragment, basis_fragment
    )


def query_zeolite_property(
    namespace: str,
    material_label: str = "",
    framework_code: str = "",
    property_local: str = "",
    value_filter: str = "",
) -> str:
    if namespace == "ontozeolite":
        from mini_marie.marie.chemistry.zeolite_corpus import ZeoliteCorpusStore

        store = ZeoliteCorpusStore()
        try:
            stats = store.stats()
            if stats["material_rows"] > 0:
                return store.query_property_tsv(
                    material_label=material_label,
                    framework_code=framework_code,
                    property_local=property_local,
                    value_filter=value_filter,
                    limit=ONLINE_PROBE_LIMIT,
                )
        finally:
            store.close()
    return qb.query_zeolite_property(
        namespace, material_label, framework_code, property_local, value_filter
    )


def ontomops_instance_note() -> str:
    return (
        "OntoMOPs A-box is not in chemistry Blazegraph (ontomops namespace is empty). "
        "Use MCP servers `twa-mops` (synthesis) or `mof-twa` (MOF properties) for MOP instance data."
    )


def search_species_names(
    query: str,
    *,
    limit: int = ONLINE_PROBE_LIMIT,
    fuzzy: bool = True,
    min_score: int = 70,
) -> str:
    from mini_marie.marie.chemistry.species_corpus import SpeciesCorpusStore

    store = SpeciesCorpusStore()
    try:
        stats = store.stats()
        if stats["name_rows"] == 0:
            return (
                "No local species corpus yet. Warm with:\n"
                "  python -m mini_marie.marie.chemistry.warm_species_corpus --batch-size 50 --max-batches 1"
            )
        return store.search_names_tsv(
            query, limit=probe_limit(limit), fuzzy=fuzzy, min_score=min_score
        )
    finally:
        store.close()


def list_species_by_formula(
    formula: str, *, match: str = "equals", limit: int = ONLINE_PROBE_LIMIT
) -> str:
    from mini_marie.marie.chemistry.species_formula_corpus import SpeciesFormulaStore

    store = SpeciesFormulaStore()
    try:
        if store.stats()["formula_rows"] == 0:
            return (
                "No formula index yet. Build with:\n"
                "  python -m mini_marie.marie.chemistry.build_species_formula_index --build"
            )
        return store.lookup_formula_tsv(formula, match=match, limit=probe_limit(limit))
    finally:
        store.close()


def search_species_uses(
    query: str, *, limit: int = ONLINE_PROBE_LIMIT, species_iri: str = ""
) -> str:
    from mini_marie.marie.chemistry.species_uses_corpus import SpeciesUsesCorpusStore

    store = SpeciesUsesCorpusStore()
    try:
        if store.stats()["use_rows"] == 0:
            return (
                "No uses corpus yet. Warm with:\n"
                "  python -m mini_marie.marie.chemistry.warm_species_uses_corpus --max-batches 0"
            )
        return store.search_uses_tsv(
            query, limit=probe_limit(limit), species_iri=species_iri
        )
    finally:
        store.close()


def query_species_physprops(
    *,
    species_iri: str = "",
    property_local: str = "",
    limit: int = ONLINE_PROBE_LIMIT,
) -> str:
    from mini_marie.marie.chemistry.species_physprops_corpus import SpeciesPhyspropsCorpusStore

    store = SpeciesPhyspropsCorpusStore()
    try:
        if store.stats()["property_rows"] == 0:
            return (
                "No physprops corpus yet. Warm with:\n"
                "  python -m mini_marie.marie.chemistry.warm_species_physprops_corpus --max-batches 0"
            )
        return store.query_properties_tsv(
            species_iri=species_iri,
            property_local=property_local,
            limit=probe_limit(limit),
        )
    finally:
        store.close()


def query_species_pka(
    species_iri: str = "",
    reliability: str = "",
    method: str = "",
    acidity_label: str = "",
    limit: int = ONLINE_PROBE_LIMIT,
) -> str:
    from mini_marie.marie.chemistry.species_pka_corpus import SpeciesPkaCorpusStore

    store = SpeciesPkaCorpusStore()
    try:
        stats = store.stats()
        if stats["pka_rows"] == 0:
            return (
                "No local pKa corpus yet. Warm with:\n"
                "  python -m mini_marie.marie.chemistry.warm_species_pka_corpus --batch-size 50 --max-batches 1"
            )
        return store.query_pka_tsv(
            species_iri=species_iri,
            reliability=reliability,
            method=method,
            acidity_label=acidity_label,
            limit=probe_limit(limit),
        )
    finally:
        store.close()


def search_mechanisms(query: str, *, limit: int = ONLINE_PROBE_LIMIT) -> str:
    from mini_marie.marie.chemistry.ontokin_corpus import OntokinCorpusStore

    store = OntokinCorpusStore()
    try:
        if store.stats()["mechanism_rows"] == 0:
            return (
                "No mechanism corpus yet. Warm with:\n"
                "  python -m mini_marie.marie.chemistry.warm_ontokin_corpus --facet mechanisms --max-batches 0"
            )
        rows = store.search_mechanisms(query, limit=probe_limit(limit))
        return format_tsv([{"mechanism": r["mechanism_iri"], "label": r.get("label", "")} for r in rows])
    finally:
        store.close()


def search_zeolite_materials(query: str, *, limit: int = ONLINE_PROBE_LIMIT) -> str:
    from mini_marie.marie.chemistry.zeolite_corpus import ZeoliteCorpusStore

    store = ZeoliteCorpusStore()
    try:
        if store.stats()["material_rows"] == 0:
            return (
                "No zeolite corpus yet. Warm with:\n"
                "  python -m mini_marie.marie.chemistry.warm_zeolite_corpus --max-batches 0"
            )
        rows = store.search_materials(query, limit=probe_limit(limit))
        return format_tsv(rows)
    finally:
        store.close()


def search_authors(query: str, *, limit: int = ONLINE_PROBE_LIMIT) -> str:
    from mini_marie.marie.chemistry.provenance_corpus import ProvenanceCorpusStore

    store = ProvenanceCorpusStore()
    try:
        if store.stats()["person_rows"] == 0:
            return (
                "No provenance corpus yet. Warm with:\n"
                "  python -m mini_marie.marie.chemistry.warm_provenance_corpus"
            )
        return store.search_persons_tsv(query, limit=probe_limit(limit))
    finally:
        store.close()


def lookup_provenance_ref(ref_fragment: str, *, limit: int = ONLINE_PROBE_LIMIT) -> str:
    from mini_marie.marie.chemistry.provenance_corpus import ProvenanceCorpusStore

    store = ProvenanceCorpusStore()
    try:
        return store.lookup_ref_label_tsv(ref_fragment, limit=probe_limit(limit))
    finally:
        store.close()


def query_pka_with_provenance(
    species_fragment: str = "", *, limit: int = ONLINE_PROBE_LIMIT
) -> str:
    from mini_marie.marie.chemistry.species_join_corpus import SpeciesJoinStore

    store = SpeciesJoinStore()
    try:
        if store.is_built():
            rows = store.query_pka_enriched(
                species_fragment=species_fragment,
                limit=probe_limit(limit),
            )
            rows = [r for r in rows if str(r.get("provenance") or r.get("ref_label") or "").strip()]
            if rows:
                out = [
                    {
                        "species": r["species_iri"],
                        "label": r.get("primary_label") or "",
                        "pka": r.get("pka_value") or "",
                        "reliability": r.get("reliability") or "",
                        "method": r.get("method") or "",
                        "provenance": r.get("ref_label") or r.get("provenance") or "",
                    }
                    for r in rows
                ]
                return format_tsv(out)
    finally:
        store.close()

    from mini_marie.marie.chemistry.provenance_corpus import ProvenanceCorpusStore

    store = ProvenanceCorpusStore()
    try:
        return store.join_pka_provenance_tsv(limit=probe_limit(limit))
    finally:
        store.close()


def query_pka_enriched(
    *,
    species_fragment: str = "",
    reliability_fragment: str = "",
    method_fragment: str = "",
    ref_label_fragment: str = "",
    acidity_label_fragment: str = "",
    limit: int = ONLINE_PROBE_LIMIT,
) -> str:
    from mini_marie.marie.chemistry.species_join_corpus import SpeciesJoinStore

    store = SpeciesJoinStore()
    try:
        if not store.is_built():
            return (
                "No OntoSpecies join index yet. Build with:\n"
                "  python -m mini_marie.marie.chemistry.build_species_join_index --build"
            )
        return store.query_pka_enriched_tsv(
            species_fragment=species_fragment,
            reliability_fragment=reliability_fragment,
            method_fragment=method_fragment,
            ref_label_fragment=ref_label_fragment,
            acidity_label_fragment=acidity_label_fragment,
            limit=probe_limit(limit),
        )
    finally:
        store.close()


def query_physprops_wide(*, smiles: str = "", limit: int = ONLINE_PROBE_LIMIT) -> str:
    from mini_marie.marie.chemistry.species_join_corpus import SpeciesJoinStore

    store = SpeciesJoinStore()
    try:
        if not store.is_built():
            return (
                "No OntoSpecies join index yet. Build with:\n"
                "  python -m mini_marie.marie.chemistry.build_species_join_index --build"
            )
        rows = store.lookup_physprops_by_smiles(smiles, limit=probe_limit(limit))
        if not rows:
            return "No results"
        return format_tsv(rows)
    finally:
        store.close()


def search_uses_enriched(*, use_fragment: str = "", limit: int = ONLINE_PROBE_LIMIT) -> str:
    from mini_marie.marie.chemistry.species_join_corpus import SpeciesJoinStore

    store = SpeciesJoinStore()
    try:
        if not store.is_built():
            return (
                "No OntoSpecies join index yet. Build with:\n"
                "  python -m mini_marie.marie.chemistry.build_species_join_index --build"
            )
        rows = store.lookup_uses_enriched(use_fragment, limit=probe_limit(limit))
        if not rows:
            return "No results"
        return format_tsv(rows)
    finally:
        store.close()


def query_species_profile_top(*, limit: int = ONLINE_PROBE_LIMIT) -> str:
    from mini_marie.marie.chemistry.species_join_corpus import SpeciesJoinStore

    store = SpeciesJoinStore()
    try:
        if not store.is_built():
            return (
                "No OntoSpecies join index yet. Build with:\n"
                "  python -m mini_marie.marie.chemistry.build_species_join_index --build"
            )
        rows = store.top_species_by_pka_count(limit=probe_limit(limit))
        if not rows:
            return "No results"
        return format_tsv(rows)
    finally:
        store.close()
