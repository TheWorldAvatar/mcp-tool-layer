"""Register generic Marie competency tools on per-namespace chemistry MCP servers."""

from __future__ import annotations

from typing import Optional, Sequence

from fastmcp import FastMCP

from mini_marie.marie.chemistry import competency_operations as cq


def register_competency_tools(mcp: FastMCP, namespace: str) -> None:
    ns = namespace

    @mcp.tool(
        name="lookup_individuals",
        description=(
            "Find individuals of an OWL class by rdfs:label substring or identifier "
            "(identifier_type: inchi|smiles|inchikey). Returns at most 5 rows."
        ),
    )
    def lookup_individuals(
        class_local: str,
        label_fragment: str = "",
        identifier_type: str = "",
    ) -> str:
        return cq.lookup_individuals(ns, class_local, label_fragment, identifier_type)

    @mcp.tool(
        name="get_linked_values",
        description=(
            "Traverse an object property from a subject (by label or identifier). "
            "Auto-expands os:value reification for OntoSpecies. "
            "Set include_metadata=true for pKa metadata on hasDissociationConstants. "
            "Returns at most 5 rows."
        ),
    )
    def get_linked_values(
        class_local: str,
        subject_label: str,
        link_property: str,
        value_properties: Optional[Sequence[str]] = None,
        include_metadata: bool = False,
        identifier_type: str = "",
        related_class: str = "",
    ) -> str:
        return cq.get_linked_values(
            ns,
            class_local,
            subject_label,
            link_property,
            value_properties,
            include_metadata,
            identifier_type,
            related_class,
        )

    @mcp.tool(
        name="filter_by_literal",
        description=(
            "Filter individuals by a property literal (match=contains|equals). "
            "OntoSpecies reified properties use os:value automatically. "
            "OntoZeolite: use property_local=hasFrameworkCode with IZA code (e.g. AEN). "
            "Returns at most 5 rows."
        ),
    )
    def filter_by_literal(
        class_local: str,
        property_local: str,
        value_fragment: str,
        match: str = "contains",
        use_value_node: bool = False,
        subject_label: str = "",
    ) -> str:
        return cq.filter_by_literal(
            ns,
            class_local,
            property_local,
            value_fragment,
            match,
            use_value_node,
            subject_label,
        )

    @mcp.tool(
        name="count_instances",
        description="COUNT DISTINCT individuals of an OWL class; optional required_property filter.",
    )
    def count_instances(class_local: str, required_property: str = "") -> str:
        prop = required_property.strip() or None
        return cq.count_instances(ns, class_local, prop)

    if namespace == "ontospecies":

        @mcp.tool(
            name="search_species_names",
            description=(
                "Search local OntoSpecies corpus by name (label, IUPAC, formula, SMILES, InChI, CID). "
                "Uses warmed SQLite index + optional fuzzy match. "
                "Warm corpus: python -m mini_marie.marie.chemistry.warm_species_corpus"
            ),
        )
        def search_species_names(
            query: str,
            limit: int = 20,
            fuzzy: bool = True,
            min_score: int = 70,
        ) -> str:
            return cq.search_species_names(
                query, limit=min(limit, 50), fuzzy=fuzzy, min_score=min_score
            )

        @mcp.tool(
            name="query_species_pka",
            description=(
                "Query local pKa measurement corpus (value + T, ionic strength, method, "
                "reliability, acidity label). Filter by species_iri and/or metadata. "
                "Warm: python -m mini_marie.marie.chemistry.warm_species_pka_corpus"
            ),
        )
        def query_species_pka(
            species_iri: str = "",
            reliability: str = "",
            method: str = "",
            acidity_label: str = "",
            limit: int = 100,
        ) -> str:
            return cq.query_species_pka(
                species_iri=species_iri,
                reliability=reliability,
                method=method,
                acidity_label=acidity_label,
                limit=limit,
            )

        @mcp.tool(
            name="list_species_by_formula",
            description="Offline formula index (derived from names corpus). match=equals|contains.",
        )
        def list_species_by_formula(
            formula: str,
            match: str = "equals",
            limit: int = 50,
        ) -> str:
            return cq.list_species_by_formula(formula, match=match, limit=limit)

        @mcp.tool(name="search_species_uses", description="Search local hasUse corpus.")
        def search_species_uses(query: str, limit: int = 50, species_iri: str = "") -> str:
            return cq.search_species_uses(query, limit=limit, species_iri=species_iri)

        @mcp.tool(name="query_species_physprops", description="Query local H-bond/mass/TPSA corpus.")
        def query_species_physprops(
            species_iri: str = "",
            property_local: str = "",
            limit: int = 100,
        ) -> str:
            return cq.query_species_physprops(
                species_iri=species_iri, property_local=property_local, limit=limit
            )

        @mcp.tool(
            name="query_pka_enriched",
            description=(
                "Query pKa rows from OntoSpecies join index (species + provenance ref label). "
                "Build: python -m mini_marie.marie.chemistry.build_species_join_index --build"
            ),
        )
        def query_pka_enriched(
            species_fragment: str = "",
            reliability_fragment: str = "",
            method_fragment: str = "",
            ref_label_fragment: str = "",
            acidity_label_fragment: str = "",
            limit: int = 100,
        ) -> str:
            return cq.query_pka_enriched(
                species_fragment=species_fragment,
                reliability_fragment=reliability_fragment,
                method_fragment=method_fragment,
                ref_label_fragment=ref_label_fragment,
                acidity_label_fragment=acidity_label_fragment,
                limit=limit,
            )

        @mcp.tool(
            name="query_physprops_wide",
            description="Lookup wide physprops row by SMILES from join index.",
        )
        def query_physprops_wide(smiles: str = "", limit: int = 10) -> str:
            return cq.query_physprops_wide(smiles=smiles, limit=limit)

        @mcp.tool(
            name="search_uses_enriched",
            description="Search hasUse values from join index (species identifiers attached).",
        )
        def search_uses_enriched(use_fragment: str = "", limit: int = 50) -> str:
            return cq.search_uses_enriched(use_fragment=use_fragment, limit=limit)

        @mcp.tool(
            name="query_species_profile_top",
            description="Top species by pKa measurement count (join profile table).",
        )
        def query_species_profile_top(limit: int = 10) -> str:
            return cq.query_species_profile_top(limit=limit)

        @mcp.tool(
            name="query_pka_with_provenance",
            description="pKa rows with non-empty provenance/ref label (optionally filter species).",
        )
        def query_pka_with_provenance(species_fragment: str = "", limit: int = 100) -> str:
            return cq.query_pka_with_provenance(species_fragment=species_fragment, limit=limit)

    if namespace == "ontokin":

        @mcp.tool(
            name="traverse_mechanism_reactions",
            description=(
                "List reactions linked to ReactionMechanism individuals "
                "(by mechanism label, mechanism IRI fragment, or reaction label/DOI fragment). "
                "Returns at most 5 rows."
            ),
        )
        def traverse_mechanism_reactions(
            mechanism_label: str = "",
            reaction_fragment: str = "",
            mechanism_iri_fragment: str = "",
        ) -> str:
            return cq.traverse_mechanism_reactions(
                ns, mechanism_label, reaction_fragment, mechanism_iri_fragment
            )

        @mcp.tool(name="search_mechanisms", description="Search local OntoKin mechanism corpus.")
        def search_mechanisms(query: str, limit: int = 50) -> str:
            return cq.search_mechanisms(query, limit=limit)

    if namespace == "ontocompchem":

        @mcp.tool(
            name="query_calculation_results",
            description=(
                "Query GaussianCalculation results for a species label. "
                "result_kinds: homo, lumo, zpe, rotational. "
                "Optional method_fragment and basis_fragment filters. Returns at most 5 rows."
            ),
        )
        def query_calculation_results(
            species_label: str,
            result_kinds: Optional[Sequence[str]] = None,
            method_fragment: str = "",
            basis_fragment: str = "",
        ) -> str:
            return cq.query_calculation_results(
                ns, species_label, result_kinds, method_fragment, basis_fragment
            )

    if namespace == "ontozeolite":

        @mcp.tool(
            name="query_zeolite_property",
            description=(
                "Zeolite queries: framework_code (e.g. AEN), reference zeolite "
                "(framework_code + isReferenceZeolite), or material_label + property_local. "
                "Returns at most 5 rows."
            ),
        )
        def query_zeolite_property(
            material_label: str = "",
            framework_code: str = "",
            property_local: str = "",
            value_filter: str = "",
        ) -> str:
            return cq.query_zeolite_property(
                ns, material_label, framework_code, property_local, value_filter
            )

        @mcp.tool(name="search_zeolite_materials", description="Search local zeolite material corpus.")
        def search_zeolite_materials(query: str, limit: int = 50) -> str:
            return cq.search_zeolite_materials(query, limit=limit)

    if namespace == "ontoprovenance":

        @mcp.tool(name="search_authors", description="Search local Person name corpus.")
        def search_authors(query: str, limit: int = 50) -> str:
            return cq.search_authors(query, limit=limit)

    if namespace == "ontomops":

        @mcp.tool(
            name="ontomops_instance_routing",
            description="Explain where MOP instance data lives (twa-mops / mof-twa MCPs)",
        )
        def ontomops_instance_routing() -> str:
            return cq.ontomops_instance_note()
