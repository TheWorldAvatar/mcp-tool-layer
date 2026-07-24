"""Generic SPARQL query builders for chemistry namespace MCP tools."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from mini_marie.marie.chemistry.limits import ONLINE_PROBE_LIMIT, sparql_timeout, warm_max_rows
from mini_marie.marie.chemistry.registry import endpoint
from mini_marie.marie.chemistry.sparql import execute_sparql_get, format_tsv

PREFIX_MAP: Dict[str, tuple[str, str]] = {
    "ontospecies": ("os", "http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#"),
    "ontokin": ("ok", "http://www.theworldavatar.com/ontology/ontokin/OntoKin.owl#"),
    "ontocompchem": ("occ", "http://www.theworldavatar.com/ontology/ontocompchem/OntoCompChem.owl#"),
    "ontozeolite": ("oz", "http://www.theworldavatar.com/kg/ontozeolite/"),
    "ontomops": ("om", "https://www.theworldavatar.com/kg/ontomops/"),
    "ontoprovenance": ("op", "http://www.theworldavatar.com/ontology/ontoprovenance/OntoProvenance.owl#"),
    "ontopesscan": ("ops", "http://www.theworldavatar.com/ontology/ontopesscan/OntoPESScan.owl#"),
}

EXTRA_PREFIXES: Dict[str, Dict[str, str]] = {
    "ontospecies": {},
    "ontokin": {},
    "ontocompchem": {},
    "ontozeolite": {"oc": "http://www.theworldavatar.com/kg/ontocrystal/"},
    "ontomops": {},
    "ontoprovenance": {},
    "ontopesscan": {},
}

VALUE_NODE_PROPERTIES = frozenset(
    {
        "hasMolecularFormula",
        "hasDissociationConstants",
        "hasHydrogenBondDonorCount",
        "hasHydrogenBondAcceptorCount",
        "hasSMILES",
        "hasInChI",
        "hasInChIKey",
    }
)

PKA_METADATA_PROPERTIES = (
    "hasMeasurementMethod",
    "hasReliabilityAssessment",
    "hasTemperature",
    "hasIonicStrength",
    "hasAcidityLabel",
    "hasProvenance",
)

IDENTIFIER_PROPERTIES = {
    "inchi": "hasInChI",
    "smiles": "hasSMILES",
    "inchikey": "hasInChIKey",
}

COMPCHEM_RESULT_TYPES = {
    "homo": "HOMOEnergy",
    "lumo": "LUMOEnergy",
    "zpe": "ZeroPointEnergy",
    "zero": "ZeroPointEnergy",
    "rotational": "RotationalConstants",
    "rotationalconstants": "RotationalConstants",
}


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _prefix_block(namespace: str) -> str:
    alias, iri = PREFIX_MAP[namespace]
    lines = [f"PREFIX {alias}: <{iri}>"]
    for name, extra_iri in EXTRA_PREFIXES.get(namespace, {}).items():
        lines.append(f"PREFIX {name}: <{extra_iri}>")
    lines.append("PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>")
    return "\n".join(lines)


def _class_iri(namespace: str, class_local: str) -> str:
    alias, _ = PREFIX_MAP[namespace]
    return f"{alias}:{class_local}"


def _execute(namespace: str, query: str) -> List[Dict[str, Any]]:
    return execute_sparql_get(query, endpoint(namespace), timeout=sparql_timeout(namespace))


def _limit_clause(row_limit: Optional[int], namespace: str) -> str:
    """Probe tier uses small LIMIT; warm tier uses namespace safety cap."""
    if row_limit is not None:
        return f"LIMIT {int(row_limit)}\n"
    return f"LIMIT {warm_max_rows(namespace)}\n"


def _value_node_pattern(namespace: str, prop_local: str, subject: str, object_var: str) -> str:
    alias, _ = PREFIX_MAP[namespace]
    if prop_local in VALUE_NODE_PROPERTIES and namespace == "ontospecies":
        return (
            f"  {subject} {alias}:{prop_local} ?{object_var}Node .\n"
            f"  ?{object_var}Node os:value ?{object_var} .\n"
        )
    return f"  {subject} {alias}:{prop_local} ?{object_var} .\n"


def _subject_match_block(
    namespace: str,
    class_local: str,
    fragment: str,
    identifier_type: str,
    subject_var: str = "?subject",
) -> str:
    class_iri = _class_iri(namespace, class_local)
    frag = fragment.strip()
    id_type = identifier_type.strip().lower()
    lines = [f"  {subject_var} a {class_iri} ."]

    if id_type in IDENTIFIER_PROPERTIES and frag:
        prop = IDENTIFIER_PROPERTIES[id_type]
        lines.append(_value_node_pattern(namespace, prop, subject_var, "idVal").rstrip())
        if id_type == "smiles":
            lines.append(f'  FILTER(STR(?idVal) = "{_esc(frag)}")')
        else:
            lines.append(f'  FILTER(CONTAINS(STR(?idVal), "{_esc(frag)}"))')
        return "\n".join(lines)

    if frag and namespace == "ontoprovenance":
        lower = _esc(frag.lower())
        return f"""  {subject_var} a {class_iri} .
  {subject_var} ?anyProp ?subjectLabel .
  FILTER(CONTAINS(LCASE(STR(?subjectLabel)), "{lower}"))"""

    if not frag:
        return "\n".join(lines)

    lower = _esc(frag.lower())
    if namespace == "ontospecies":
        return f"""  {subject_var} a {class_iri} .
  {{
    {subject_var} rdfs:label ?subjectLabel .
    FILTER(CONTAINS(LCASE(STR(?subjectLabel)), "{lower}"))
  }} UNION {{
    {subject_var} os:hasMolecularFormula ?mf .
    ?mf os:value ?subjectLabel .
    FILTER(CONTAINS(LCASE(STR(?subjectLabel)), "{lower}"))
  }} UNION {{
    {subject_var} os:hasSMILES ?sm .
    ?sm os:value ?subjectLabel .
    FILTER(CONTAINS(LCASE(STR(?subjectLabel)), "{lower}"))
  }}"""

    lines.append(f"  {subject_var} rdfs:label ?subjectLabel .")
    lines.append(f'  FILTER(CONTAINS(LCASE(STR(?subjectLabel)), "{lower}"))')
    return "\n".join(lines)


def lookup_individuals_rows(
    namespace: str,
    class_local: str,
    label_fragment: str = "",
    identifier_type: str = "",
    *,
    row_limit: Optional[int] = ONLINE_PROBE_LIMIT,
) -> List[Dict[str, Any]]:
    """Find individuals of class_local by label, formula, identifier, or SMILES substring."""
    class_iri = _class_iri(namespace, class_local)
    frag = label_fragment.strip()
    id_type = identifier_type.strip().lower()

    q = f"""
{_prefix_block(namespace)}
SELECT ?subject ?label WHERE {{
  ?subject a {class_iri} .
"""
    if id_type in IDENTIFIER_PROPERTIES and frag:
        prop = IDENTIFIER_PROPERTIES[id_type]
        alias, _ = PREFIX_MAP[namespace]
        if prop in VALUE_NODE_PROPERTIES and namespace == "ontospecies":
            q += f"  ?subject {alias}:{prop} ?idNode .\n"
            q += f"  ?idNode os:value ?label .\n"
        else:
            q += f"  ?subject {alias}:{prop} ?label .\n"
        if id_type == "smiles":
            q += f'  FILTER(STR(?label) = "{_esc(frag)}")\n'
        else:
            q += f'  FILTER(CONTAINS(STR(?label), "{_esc(frag)}"))\n'
    elif frag and namespace == "ontoprovenance":
        lower = _esc(frag.lower())
        q += f"  ?subject ?anyProp ?label .\n"
        q += f'  FILTER(CONTAINS(LCASE(STR(?label)), "{lower}"))\n'
    elif frag and namespace == "ontospecies":
        lower = _esc(frag.lower())
        q += f"""  {{
    ?subject rdfs:label ?label .
    FILTER(CONTAINS(LCASE(STR(?label)), "{lower}"))
  }} UNION {{
    ?subject os:hasMolecularFormula ?mf .
    ?mf os:value ?label .
    FILTER(CONTAINS(LCASE(STR(?label)), "{lower}"))
  }} UNION {{
    ?subject os:hasSMILES ?sm .
    ?sm os:value ?label .
    FILTER(CONTAINS(LCASE(STR(?label)), "{lower}"))
  }}
"""
    elif frag:
        q += f'  ?subject rdfs:label ?label .\n'
        q += f'  FILTER(CONTAINS(LCASE(STR(?label)), "{_esc(frag.lower())}"))\n'
    else:
        q += "  OPTIONAL { ?subject rdfs:label ?label }\n"

    q += f"}}\n{_limit_clause(row_limit, namespace)}"
    return _execute(namespace, q)


def lookup_individuals(
    namespace: str,
    class_local: str,
    label_fragment: str = "",
    identifier_type: str = "",
) -> str:
    return format_tsv(
        lookup_individuals_rows(namespace, class_local, label_fragment, identifier_type)
    )


def get_linked_values_rows(
    namespace: str,
    class_local: str,
    subject_label: str,
    link_property: str,
    value_properties: Optional[Sequence[str]] = None,
    include_metadata: bool = False,
    identifier_type: str = "",
    related_class: str = "",
    *,
    row_limit: Optional[int] = ONLINE_PROBE_LIMIT,
) -> List[Dict[str, Any]]:
    """Traverse link_property from subject; expand os:value nodes and optional nested properties."""
    alias, _ = PREFIX_MAP[namespace]
    props = list(value_properties or [])
    if include_metadata and link_property == "hasDissociationConstants":
        for p in PKA_METADATA_PROPERTIES:
            if p not in props:
                props.append(p)

    select_vars = ["?subject", "?subjectLabel", "?linked"]
    if link_property in VALUE_NODE_PROPERTIES and namespace == "ontospecies":
        select_vars.append("?value")
    for i, prop in enumerate(props):
        select_vars.append(f"?v{i}val")

    q = f"""
{_prefix_block(namespace)}
SELECT {' '.join(select_vars)} WHERE {{
{_subject_match_block(namespace, class_local, subject_label, identifier_type)}
  OPTIONAL {{ ?subject rdfs:label ?subjectLabel }}
  ?subject {alias}:{link_property} ?linked .
"""
    if link_property in VALUE_NODE_PROPERTIES and namespace == "ontospecies":
        q += "  ?linked os:value ?value .\n"

    for i, prop in enumerate(props):
        if prop in VALUE_NODE_PROPERTIES and namespace == "ontospecies":
            q += f"  OPTIONAL {{ ?linked {alias}:{prop} ?v{i}node . ?v{i}node os:value ?v{i}val }}\n"
        else:
            q += f"  OPTIONAL {{ ?linked {alias}:{prop} ?v{i}val }}\n"

    if related_class:
        q += f"  ?linked a {_class_iri(namespace, related_class)} .\n"

    q += f"}}\n{_limit_clause(row_limit, namespace)}"
    return _execute(namespace, q)


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
    return format_tsv(
        get_linked_values_rows(
            namespace,
            class_local,
            subject_label,
            link_property,
            value_properties,
            include_metadata,
            identifier_type,
            related_class,
        )
    )


def filter_by_literal_rows(
    namespace: str,
    class_local: str,
    property_local: str,
    value_fragment: str,
    match: str = "contains",
    use_value_node: bool = False,
    subject_label: str = "",
    *,
    row_limit: Optional[int] = ONLINE_PROBE_LIMIT,
) -> List[Dict[str, Any]]:
    """Filter individuals by a literal property value (optionally via os:value node)."""
    if property_local == "hasFrameworkCode" and namespace == "ontozeolite":
        return _zeolite_materials_by_framework_rows(value_fragment, row_limit=row_limit)

    if namespace == "ontozeolite" and property_local in ("hasGuestSpecies", "hasGuestFormula", "hasLatticeSystem"):
        from mini_marie.marie.chemistry.zeolite_corpus import ZeoliteCorpusStore

        store = ZeoliteCorpusStore()
        try:
            rows = store.query_property(
                property_local=property_local,
                value_filter=value_fragment,
                limit=row_limit if row_limit is not None else warm_max_rows(namespace),
            )
            return [
                {
                    "subject": r.get("material_iri", ""),
                    "label": r.get("label", ""),
                    "propVal": r.get("property_value", ""),
                }
                for r in rows
            ]
        finally:
            store.close()

    alias, _ = PREFIX_MAP[namespace]
    class_iri = _class_iri(namespace, class_local)
    val = _esc(value_fragment.strip())
    use_value = use_value_node or property_local in VALUE_NODE_PROPERTIES

    if match == "equals":
        filter_expr = f'LCASE(STR(?propVal)) = LCASE("{val}")'
    else:
        filter_expr = f'CONTAINS(LCASE(STR(?propVal)), LCASE("{val}"))'

    q = f"""
{_prefix_block(namespace)}
SELECT ?subject ?label ?propVal WHERE {{
  ?subject a {class_iri} .
"""
    if subject_label.strip():
        q += f'  ?subject rdfs:label ?label .\n'
        q += f'  FILTER(CONTAINS(LCASE(STR(?label)), "{_esc(subject_label.lower())}"))\n'
    else:
        q += "  OPTIONAL { ?subject rdfs:label ?label }\n"

    if use_value and namespace == "ontospecies":
        q += f"  ?subject {alias}:{property_local} ?propNode .\n"
        q += "  ?propNode os:value ?propVal .\n"
        q += f"  FILTER({filter_expr})\n"
    else:
        q += f"  ?subject {alias}:{property_local} ?propVal .\n"
        q += f"  FILTER({filter_expr})\n"

    q += f"}}\n{_limit_clause(row_limit, namespace)}"
    return _execute(namespace, q)


def filter_by_literal(
    namespace: str,
    class_local: str,
    property_local: str,
    value_fragment: str,
    match: str = "contains",
    use_value_node: bool = False,
    subject_label: str = "",
) -> str:
    return format_tsv(
        filter_by_literal_rows(
            namespace,
            class_local,
            property_local,
            value_fragment,
            match,
            use_value_node,
            subject_label,
        )
    )


def _zeolite_materials_by_framework_rows(
    framework_code: str,
    *,
    row_limit: Optional[int] = ONLINE_PROBE_LIMIT,
) -> List[Dict[str, Any]]:
    code = _esc(framework_code.strip().upper())
    q = f"""
{_prefix_block("ontozeolite")}
SELECT ?subject ?label ?propVal WHERE {{
  ?fw a oz:ZeoliteFramework .
  ?fw oz:hasFrameworkCode "{code}" .
  ?fw oz:hasZeoliticMaterial ?subject .
  ?subject a oz:ZeoliticMaterial .
  OPTIONAL {{ ?subject rdfs:label ?label }}
  OPTIONAL {{ ?subject oz:hasChemicalFormula ?propVal }}
}}
{_limit_clause(row_limit, "ontozeolite")}"""
    return _execute("ontozeolite", q)


def _zeolite_materials_by_framework(framework_code: str) -> str:
    return format_tsv(_zeolite_materials_by_framework_rows(framework_code))


def traverse_mechanism_reactions_rows(
    namespace: str,
    mechanism_label: str = "",
    reaction_fragment: str = "",
    mechanism_iri_fragment: str = "",
    *,
    row_limit: Optional[int] = ONLINE_PROBE_LIMIT,
) -> List[Dict[str, Any]]:
    """OntoKin: list reactions linked to mechanisms via ok:hasReaction and ok:hasEquation."""
    if namespace == "ontokin":
        from mini_marie.marie.chemistry.ontokin_corpus import OntokinCorpusStore

        store = OntokinCorpusStore()
        try:
            if store.stats()["reaction_edge_rows"] > 0:
                cap = row_limit if row_limit is not None else warm_max_rows("ontokin")
                rows = store.traverse_reactions(
                    mechanism_label=mechanism_label,
                    reaction_fragment=reaction_fragment,
                    mechanism_iri_fragment=mechanism_iri_fragment,
                    limit=cap,
                )
                return [
                    {
                        "mechanism": r["mechanism_iri"],
                        "mechanismLabel": r.get("mechanism_label", ""),
                        "reaction": r["reaction_iri"],
                        "equation": r.get("equation", ""),
                    }
                    for r in rows
                ]
        finally:
            store.close()

    if namespace != "ontokin":
        return []

    frag_m = _esc(mechanism_label.strip().lower()) if mechanism_label.strip() else ""
    frag_r = _esc(reaction_fragment.strip()) if reaction_fragment.strip() else ""
    frag_iri = _esc(mechanism_iri_fragment.strip()) if mechanism_iri_fragment.strip() else ""

    q = f"""
{_prefix_block(namespace)}
SELECT ?mechanism ?mechanismLabel ?reaction ?equation WHERE {{
  ?mechanism a ok:ReactionMechanism .
  ?mechanism ok:hasReaction ?reaction .
  OPTIONAL {{ ?mechanism rdfs:label ?mechanismLabel }}
  OPTIONAL {{ ?reaction ok:hasEquation ?equation }}
"""
    if frag_m:
        q += f'  FILTER(CONTAINS(LCASE(STR(?mechanismLabel)), "{frag_m}"))\n'
    if frag_iri:
        q += f'  FILTER(CONTAINS(STR(?mechanism), "{frag_iri}"))\n'
    if frag_r:
        q += (
            f'  FILTER(CONTAINS(STR(?equation), "{frag_r}") || '
            f'CONTAINS(STR(?reaction), "{frag_r}"))\n'
        )
    q += f"}}\n{_limit_clause(row_limit, namespace)}"
    return _execute(namespace, q)


def traverse_mechanism_reactions(
    namespace: str,
    mechanism_label: str = "",
    reaction_fragment: str = "",
    mechanism_iri_fragment: str = "",
) -> str:
    rows = traverse_mechanism_reactions_rows(
        namespace, mechanism_label, reaction_fragment, mechanism_iri_fragment
    )
    return format_tsv(rows) if rows else "No results"


def query_calculation_results_rows(
    namespace: str,
    species_label: str,
    result_kinds: Optional[Sequence[str]] = None,
    method_fragment: str = "",
    basis_fragment: str = "",
    *,
    row_limit: Optional[int] = ONLINE_PROBE_LIMIT,
) -> List[Dict[str, Any]]:
    """OntoCompChem: query CalculationResult nodes by species IRI fragment."""
    if namespace != "ontocompchem":
        return []

    if species_label.strip():
        from mini_marie.marie.chemistry.compchem_corpus import CompChemCorpusStore

        store = CompChemCorpusStore()
        try:
            if store.stats()["qm_result_rows"] > 0:
                rows = store.query_results(
                    species_label,
                    result_kinds=list(result_kinds) if result_kinds else None,
                    method_fragment=method_fragment,
                    basis_fragment=basis_fragment,
                    limit=row_limit if row_limit is not None else warm_max_rows(namespace),
                )
                if rows:
                    return [
                        {
                            "result": r["result_iri"],
                            "resultType": r.get("result_type", ""),
                            "value": r.get("value", ""),
                            "unit": r.get("unit", ""),
                            "rotCount": r.get("rot_count", ""),
                        }
                        for r in rows
                    ]
        finally:
            store.close()

    kinds = [k.lower() for k in (result_kinds or ["homo", "lumo"])]
    types = []
    for kind in kinds:
        t = COMPCHEM_RESULT_TYPES.get(kind)
        if t and t not in types:
            types.append(t)
    if not types:
        types = ["HOMOEnergy", "LUMOEnergy"]

    type_filter = ", ".join(f"occ:{t}" for t in types)
    label = _esc(species_label.strip())
    calc_frag = _esc(method_fragment.strip()) if method_fragment.strip() else ""
    basis_frag = _esc(basis_fragment.strip()) if basis_fragment.strip() else ""

    q = f"""
{_prefix_block(namespace)}
SELECT ?result ?resultType ?value ?unit ?rotCount WHERE {{
  ?result a ?resultType .
  FILTER(?resultType IN ({type_filter}))
  ?result occ:value ?value .
  OPTIONAL {{ ?result occ:unit ?unit }}
  OPTIONAL {{ ?result occ:hasRotationalConstantsCount ?rotCount }}
  FILTER(CONTAINS(STR(?result), "{label}"))
"""
    if calc_frag:
        q += f'  FILTER(CONTAINS(STR(?result), "{calc_frag}"))\n'
    if basis_frag:
        q += f'  FILTER(CONTAINS(STR(?result), "{basis_frag}"))\n'
    q += f"}}\n{_limit_clause(row_limit, namespace)}"
    return _execute(namespace, q)


def query_calculation_results(
    namespace: str,
    species_label: str,
    result_kinds: Optional[Sequence[str]] = None,
    method_fragment: str = "",
    basis_fragment: str = "",
) -> str:
    rows = query_calculation_results_rows(
        namespace, species_label, result_kinds, method_fragment, basis_fragment
    )
    return format_tsv(rows) if rows else "No results"


def query_zeolite_property_rows(
    namespace: str,
    material_label: str = "",
    framework_code: str = "",
    property_local: str = "",
    value_filter: str = "",
    *,
    row_limit: Optional[int] = ONLINE_PROBE_LIMIT,
) -> List[Dict[str, Any]]:
    """OntoZeolite/OntoCrystal property queries."""
    if namespace != "ontozeolite":
        return []

    if framework_code.strip() and property_local == "isReferenceZeolite":
        code = _esc(framework_code.strip().upper())
        q = f"""
{_prefix_block(namespace)}
SELECT ?material ?label ?formula ?code WHERE {{
  ?fw a oz:ZeoliteFramework .
  ?fw oz:hasFrameworkCode "{code}" .
  ?fw oz:hasZeoliticMaterial ?material .
  ?material a oz:ZeoliticMaterial .
  ?material oz:isReferenceZeolite true .
  OPTIONAL {{ ?material rdfs:label ?label }}
  OPTIONAL {{ ?material oz:hasChemicalFormula ?formula }}
  BIND("{code}" AS ?code)
}}
{_limit_clause(row_limit, namespace)}"""
        return _execute(namespace, q)

    if framework_code.strip():
        return _zeolite_materials_by_framework_rows(framework_code, row_limit=row_limit)

    mat = _esc(material_label.strip())
    prop = property_local.strip()
    q = f"""
{_prefix_block(namespace)}
SELECT ?material ?label ?propVal WHERE {{
  ?material a oz:ZeoliticMaterial .
  OPTIONAL {{ ?material rdfs:label ?label }}
  FILTER(CONTAINS(STR(?label), "{mat}") || CONTAINS(STR(?material), "{mat}"))
"""
    if prop:
        q += f"  ?material oz:{prop} ?propVal .\n"
        if value_filter.strip():
            vf = _esc(value_filter.strip())
            q += f'  FILTER(CONTAINS(STR(?propVal), "{vf}"))\n'
    q += f"}}\n{_limit_clause(row_limit, namespace)}"
    return _execute(namespace, q)


def query_zeolite_property(
    namespace: str,
    material_label: str = "",
    framework_code: str = "",
    property_local: str = "",
    value_filter: str = "",
) -> str:
    rows = query_zeolite_property_rows(
        namespace, material_label, framework_code, property_local, value_filter
    )
    return format_tsv(rows) if rows else "No results"


def count_instances_rows(
    namespace: str,
    class_local: str,
    required_property: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """COUNT DISTINCT individuals of a class, optionally requiring a property."""
    alias, _ = PREFIX_MAP[namespace]
    class_iri = _class_iri(namespace, class_local)
    q = f"""
{_prefix_block(namespace)}
SELECT (COUNT(DISTINCT ?x) AS ?count) WHERE {{
  ?x a {class_iri} .
"""
    if required_property:
        q += f"  ?x {alias}:{required_property} ?prop .\n"
    q += "}\n"
    return _execute(namespace, q)


def count_instances(
    namespace: str,
    class_local: str,
    required_property: Optional[str] = None,
) -> str:
    return format_tsv(count_instances_rows(namespace, class_local, required_property))


def compare_rate_constants_rows(
    namespace: str,
    equation_fragment: str = "",
    *,
    row_limit: Optional[int] = ONLINE_PROBE_LIMIT,
) -> List[Dict[str, Any]]:
    if namespace != "ontokin":
        return []
    from mini_marie.marie.chemistry.ontokin_rate_corpus import OntokinRateCorpusStore

    store = OntokinRateCorpusStore()
    try:
        if store.stats()["model_rows"] == 0:
            return []
        return store.search_rate_models(
            equation_fragment=equation_fragment,
            limit=row_limit if row_limit is not None else warm_max_rows("ontokin"),
        )
    finally:
        store.close()


def query_species_pka_rows(
    namespace: str,
    reliability: str = "",
    method: str = "",
    acidity_label: str = "",
    *,
    row_limit: Optional[int] = ONLINE_PROBE_LIMIT,
) -> List[Dict[str, Any]]:
    if namespace != "ontospecies":
        return []
    from mini_marie.marie.chemistry.species_pka_corpus import SpeciesPkaCorpusStore

    store = SpeciesPkaCorpusStore()
    try:
        if store.stats()["pka_rows"] == 0:
            return []
        return store.query_pka(
            reliability=reliability,
            method=method,
            acidity_label=acidity_label,
            limit=row_limit if row_limit is not None else warm_max_rows("ontospecies"),
        )
    finally:
        store.close()


def list_zeolite_numeric_props_rows(
    namespace: str,
    *,
    row_limit: Optional[int] = ONLINE_PROBE_LIMIT,
) -> List[Dict[str, Any]]:
    if namespace != "ontozeolite":
        return []
    from mini_marie.marie.chemistry.zeolite_corpus import ZeoliteCorpusStore

    store = ZeoliteCorpusStore()
    try:
        if store.stats()["material_rows"] == 0:
            return []
        return store.materials_numeric_rows(
            limit=row_limit if row_limit is not None else warm_max_rows("ontozeolite")
        )
    finally:
        store.close()


def _join_store_rows(fn, namespace: str) -> List[Dict[str, Any]]:
    if namespace != "ontospecies":
        return []
    from mini_marie.marie.chemistry.species_join_corpus import SpeciesJoinStore

    store = SpeciesJoinStore()
    try:
        if not store.is_built():
            return []
        return fn(store)
    finally:
        store.close()


def query_pka_enriched_rows(
    namespace: str,
    species_fragment: str = "",
    reliability_fragment: str = "",
    method_fragment: str = "",
    ref_label_fragment: str = "",
    acidity_label_fragment: str = "",
    *,
    row_limit: Optional[int] = ONLINE_PROBE_LIMIT,
) -> List[Dict[str, Any]]:
    limit = row_limit if row_limit is not None else warm_max_rows("ontospecies")
    return _join_store_rows(
        lambda s: s.query_pka_enriched(
            species_fragment=species_fragment,
            reliability_fragment=reliability_fragment,
            method_fragment=method_fragment,
            ref_label_fragment=ref_label_fragment,
            acidity_label_fragment=acidity_label_fragment,
            limit=limit,
        ),
        namespace,
    )


def query_physprops_wide_rows(
    namespace: str,
    smiles: str = "",
    *,
    row_limit: Optional[int] = ONLINE_PROBE_LIMIT,
) -> List[Dict[str, Any]]:
    limit = row_limit if row_limit is not None else warm_max_rows("ontospecies")
    return _join_store_rows(
        lambda s: s.lookup_physprops_by_smiles(smiles, limit=limit),
        namespace,
    )


def search_uses_enriched_rows(
    namespace: str,
    use_fragment: str = "",
    *,
    row_limit: Optional[int] = ONLINE_PROBE_LIMIT,
) -> List[Dict[str, Any]]:
    limit = row_limit if row_limit is not None else warm_max_rows("ontospecies")
    return _join_store_rows(
        lambda s: s.lookup_uses_enriched(use_fragment, limit=limit),
        namespace,
    )


def query_species_profile_top_rows(
    namespace: str,
    *,
    row_limit: Optional[int] = ONLINE_PROBE_LIMIT,
) -> List[Dict[str, Any]]:
    limit = row_limit if row_limit is not None else warm_max_rows("ontospecies")
    return _join_store_rows(
        lambda s: s.top_species_by_pka_count(limit=limit),
        namespace,
    )


def query_pka_with_provenance_rows(
    namespace: str,
    species_fragment: str = "",
    *,
    row_limit: Optional[int] = ONLINE_PROBE_LIMIT,
) -> List[Dict[str, Any]]:
    limit = row_limit if row_limit is not None else warm_max_rows("ontospecies")
    rows = _join_store_rows(
        lambda s: s.query_pka_enriched(
            species_fragment=species_fragment,
            limit=limit,
        ),
        namespace,
    )
    return [r for r in rows if str(r.get("provenance") or "").strip()]
