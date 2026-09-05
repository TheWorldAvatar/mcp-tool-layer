"""Generate OntoSynthesis SHACL shapes from the OWL T-Box plus cardinality overrides."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from rdflib import OWL, RDF, RDFS, Graph, URIRef
from rdflib.collection import Collection

REPO_ROOT = Path(__file__).resolve().parents[2]
ONTOLOGY_PATH = REPO_ROOT / "data" / "ontologies" / "ontosynthesis.ttl"
OM2_PATH = REPO_ROOT / "data" / "ontologies" / "om2.ttl"
OUTPUT_PATH = Path(__file__).resolve().parent / "resources" / "ontosynthesis_shacl.ttl"

ONTOSYN = "https://www.theworldavatar.com/kg/OntoSyn/"
OM2 = "http://www.ontology-of-units-of-measure.org/resource/om-2/"
ONTOMOPS = "https://www.theworldavatar.com/kg/ontomops/"
RDFS_NS = "http://www.w3.org/2000/01/rdf-schema#"
XSD = "http://www.w3.org/2001/XMLSchema#"
BIBO_DOCUMENT = "http://purl.org/ontology/bibo/Document"
ONTOLAB_EQUIPMENT = "https://www.theworldavatar.com/kg/OntoLab/LabEquipment"

MEASURE_CLASSES = {
    f"{OM2}Temperature",
    f"{OM2}Duration",
    f"{OM2}Pressure",
    f"{OM2}Volume",
    f"{OM2}TemperatureRate",
    f"{OM2}AmountOfSubstanceFraction",
}

MEASURE_UNIT_CLASS = {
    f"{OM2}Temperature": f"{OM2}TemperatureUnit",
    f"{OM2}Duration": f"{OM2}DurationUnit",
    f"{OM2}Pressure": f"{OM2}PressureUnit",
    f"{OM2}Volume": f"{OM2}VolumeUnit",
    f"{OM2}TemperatureRate": f"{OM2}TemperatureRateUnit",
    f"{OM2}AmountOfSubstanceFraction": f"{OM2}AmountFractionUnit",
}

# Cardinality taken from rdfs:comment / OWL restrictions in ontosynthesis.ttl.
CARDINALITY: dict[tuple[str, str], tuple[int | None, int | None]] = {
    (f"{ONTOSYN}ChemicalSynthesis", f"{ONTOSYN}hasChemicalOutput"): (1, 1),
    (f"{ONTOSYN}ChemicalSynthesis", f"{RDFS_NS}label"): (1, 1),
    (f"{ONTOSYN}ChemicalSynthesis", f"{ONTOSYN}hasYield"): (0, 1),
    (f"{ONTOSYN}ChemicalSynthesis", f"{ONTOSYN}hasDocumentContext"): (0, 1),
    (f"{ONTOSYN}ChemicalSynthesis", f"{ONTOSYN}retrievedFrom"): (1, None),
    (f"{ONTOSYN}ChemicalSynthesis", f"{ONTOSYN}hasSynthesisStep"): (1, None),
    (f"{BIBO_DOCUMENT}", f"{RDFS_NS}label"): (1, 1),
    (f"{ONTOSYN}Add", f"{ONTOSYN}hasAddedChemicalInput"): (1, 1),
    (f"{ONTOSYN}SynthesisStep", f"{RDFS_NS}label"): (1, 1),
    (f"{ONTOSYN}Add", f"{RDFS_NS}label"): (1, 1),
    (f"{ONTOSYN}Stir", f"{RDFS_NS}label"): (1, 1),
    (f"{ONTOSYN}HeatChill", f"{RDFS_NS}label"): (1, 1),
    (f"{ONTOSYN}Evaporate", f"{RDFS_NS}label"): (1, 1),
    (f"{ONTOSYN}Sonicate", f"{RDFS_NS}label"): (1, 1),
    (f"{ONTOSYN}Crystallize", f"{RDFS_NS}label"): (1, 1),
    (f"{ONTOSYN}Transfer", f"{RDFS_NS}label"): (1, 1),
    (f"{ONTOSYN}Separate", f"{RDFS_NS}label"): (1, 1),
    (f"{ONTOSYN}Filter", f"{RDFS_NS}label"): (1, 1),
    (f"{ONTOSYN}Dry", f"{RDFS_NS}label"): (1, 1),
    (f"{ONTOSYN}SynthesisStep", f"{ONTOSYN}hasOrder"): (1, 1),
    (f"{ONTOSYN}Add", f"{ONTOSYN}hasOrder"): (1, 1),
    (f"{ONTOSYN}Stir", f"{ONTOSYN}hasOrder"): (1, 1),
    (f"{ONTOSYN}HeatChill", f"{ONTOSYN}hasOrder"): (1, 1),
    (f"{ONTOSYN}Evaporate", f"{ONTOSYN}hasOrder"): (1, 1),
    (f"{ONTOSYN}Sonicate", f"{ONTOSYN}hasOrder"): (1, 1),
    (f"{ONTOSYN}Crystallize", f"{ONTOSYN}hasOrder"): (1, 1),
    (f"{ONTOSYN}Transfer", f"{ONTOSYN}hasOrder"): (1, 1),
    (f"{ONTOSYN}Separate", f"{ONTOSYN}hasOrder"): (1, 1),
    (f"{ONTOSYN}Filter", f"{ONTOSYN}hasOrder"): (1, 1),
    (f"{ONTOSYN}Dry", f"{ONTOSYN}hasOrder"): (1, 1),
    (f"{ONTOSYN}SynthesisStep", f"{ONTOSYN}hasVesselEnvironment"): (0, 1),
    (f"{ONTOSYN}Add", f"{ONTOSYN}hasVesselEnvironment"): (0, 1),
    (f"{ONTOSYN}Stir", f"{ONTOSYN}hasVesselEnvironment"): (0, 1),
    (f"{ONTOSYN}HeatChill", f"{ONTOSYN}hasVesselEnvironment"): (0, 1),
    (f"{ONTOSYN}Evaporate", f"{ONTOSYN}hasVesselEnvironment"): (0, 1),
    (f"{ONTOSYN}Sonicate", f"{ONTOSYN}hasVesselEnvironment"): (0, 1),
    (f"{ONTOSYN}Crystallize", f"{ONTOSYN}hasVesselEnvironment"): (0, 1),
    (f"{ONTOSYN}Transfer", f"{ONTOSYN}hasVesselEnvironment"): (0, 1),
    (f"{ONTOSYN}Separate", f"{ONTOSYN}hasVesselEnvironment"): (0, 1),
    (f"{ONTOSYN}Filter", f"{ONTOSYN}hasVesselEnvironment"): (0, 1),
    (f"{ONTOSYN}Dry", f"{ONTOSYN}hasVesselEnvironment"): (0, 1),
    (f"{ONTOSYN}ChemicalInput", f"{RDFS_NS}label"): (1, 1),
    (f"{ONTOSYN}ChemicalOutput", f"{RDFS_NS}label"): (1, 1),
    (f"{ONTOSYN}ChemicalInput", f"{ONTOSYN}hasAmount"): (0, 1),
    (f"{ONTOSYN}VesselEnvironment", f"{RDFS_NS}label"): (1, 1),
    (f"{ONTOSYN}Vessel", f"{RDFS_NS}label"): (0, 1),
    (f"{ONTOSYN}Equipment", f"{RDFS_NS}label"): (1, 1),
    (f"{ONTOSYN}DocumentContext", f"{RDFS_NS}label"): (1, 1),
    (f"{ONTOSYN}Supplier", f"{RDFS_NS}label"): (1, 1),
}

DATATYPE_HINTS: dict[str, str] = {
    f"{ONTOSYN}hasOrder": "xsd:integer",
    f"{ONTOSYN}hasAmount": "xsd:string",
    f"{ONTOSYN}hasPurity": "xsd:string",
    f"{ONTOSYN}hasAlternativeNames": "xsd:string",
    f"{ONTOSYN}hasChemicalFormula": "xsd:string",
    f"{ONTOSYN}hasChemicalDescription": "xsd:string",
    f"{ONTOSYN}hasParameter": "xsd:string",
    f"{ONTOSYN}hasTargetPh": "xsd:double",
    f"{ONTOSYN}hasRotaryEvaporator": "xsd:boolean",
    f"{ONTOSYN}hasVacuum": "xsd:boolean",
    f"{ONTOSYN}isLayered": "xsd:boolean",
    f"{ONTOSYN}isLayeredTransfer": "xsd:boolean",
    f"{ONTOSYN}isRepeated": "xsd:integer",
    f"{ONTOSYN}isSealed": "xsd:boolean",
    f"{ONTOSYN}isStirred": "xsd:boolean",
    f"{ONTOSYN}isStirredHeatChill": "xsd:boolean",
    f"{ONTOSYN}isVacuumFiltration": "xsd:boolean",
    f"{ONTOSYN}isWait": "xsd:boolean",
    f"{RDFS_NS}label": "xsd:string",
    f"{OM2}hasNumericalValue": "xsd:double|xsd:integer",
    f"{ONTOMOPS}hasCCDCNumber": "xsd:string",
}


def _local(iri: str) -> str:
    return iri.rsplit("/", 1)[-1].rsplit("#", 1)[-1]


def _curie(iri: str) -> str:
    if iri.startswith(ONTOSYN):
        return f"ontosyn:{_local(iri)}"
    if iri.startswith(OM2):
        return f"om-2:{_local(iri)}"
    if iri.startswith(ONTOMOPS):
        return f"ontomops:{_local(iri)}"
    if iri.startswith(RDFS_NS):
        return f"rdfs:{_local(iri)}"
    if iri == BIBO_DOCUMENT:
        return "bibo:Document"
    if iri == ONTOLAB_EQUIPMENT:
        return "ontosyn:Equipment"
    return f"<{iri}>"


def _expand_union(graph: Graph, node) -> list[URIRef]:
    if node is None:
        return []
    if (node, RDF.type, OWL.Class) in graph or isinstance(node, URIRef):
        union = graph.value(node, OWL.unionOf)
        if union is not None:
            return list(Collection(graph, union))
        if isinstance(node, URIRef):
            return [node]
    union = graph.value(node, OWL.unionOf)
    if union is not None:
        return list(Collection(graph, union))
    return [node] if isinstance(node, URIRef) else []


def _class_iris(graph: Graph) -> list[str]:
    iris = set()
    for cls in graph.subjects(RDF.type, OWL.Class):
        if not isinstance(cls, URIRef):
            continue
        iri = str(cls)
        if iri.startswith(ONTOSYN) or iri.startswith(ONTOMOPS) or iri in MEASURE_CLASSES:
            iris.add(iri)
    iris.update(MEASURE_CLASSES)
    iris.add(BIBO_DOCUMENT)
    return sorted(iris)


def _properties(graph: Graph) -> list[tuple[str, str, list[str], list[str]]]:
    rows = []
    for pred_type, kind in (
        (OWL.ObjectProperty, "object"),
        (OWL.DatatypeProperty, "datatype"),
    ):
        for prop in graph.subjects(RDF.type, pred_type):
            if not isinstance(prop, URIRef):
                continue
            iri = str(prop)
            if not (iri.startswith(ONTOSYN) or iri.startswith(ONTOMOPS) or iri.startswith(OM2)):
                continue
            domains: list[str] = []
            ranges: list[str] = []
            for domain in graph.objects(prop, RDFS.domain):
                domains.extend(str(item) for item in _expand_union(graph, domain) if isinstance(item, URIRef))
            for rng in graph.objects(prop, RDFS.range):
                ranges.extend(str(item) for item in _expand_union(graph, rng) if isinstance(item, URIRef))
            rows.append((iri, kind, domains, ranges))
    # Shared label / measure literals used by the adapter even if absent from the T-Box file.
    rows.append((f"{RDFS_NS}label", "datatype", [], [f"{XSD}string"]))
    rows.append((f"{OM2}hasNumericalValue", "datatype", sorted(MEASURE_CLASSES), [f"{XSD}double"]))
    rows.append((f"{OM2}hasUnit", "object", sorted(MEASURE_CLASSES), [f"{OM2}Unit"]))
    return rows


def _unit_individuals(om2_graph: Graph) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = defaultdict(list)
    for subject, _, unit_class in om2_graph.triples((None, RDF.type, None)):
        if not isinstance(subject, URIRef) or not isinstance(unit_class, URIRef):
            continue
        class_iri = str(unit_class)
        if class_iri.endswith("Unit") and class_iri != f"{OM2}Unit":
            grouped[class_iri].append(str(subject))
    for values in grouped.values():
        values.sort()
    return grouped


def _ontology_index() -> tuple[list[str], dict[str, list[tuple[str, str, list[str]]]], dict[str, list[str]]]:
    graph = Graph()
    graph.parse(ONTOLOGY_PATH, format="turtle")
    om2_graph = Graph()
    if OM2_PATH.exists():
        om2_graph.parse(OM2_PATH, format="turtle")
    unit_individuals = _unit_individuals(om2_graph)
    classes = _class_iris(graph)
    props_by_class: dict[str, list[tuple[str, str, list[str]]]] = defaultdict(list)
    for iri, kind, domains, ranges in _properties(graph):
        targets = domains or classes
        for cls in targets:
            if cls not in classes and cls not in MEASURE_CLASSES:
                continue
            props_by_class[cls].append((iri, kind, ranges))
        if iri == f"{RDFS_NS}label":
            for cls in classes:
                props_by_class[cls].append((iri, kind, ranges))
    return classes, props_by_class, unit_individuals


def _property_block(
    cls: str,
    iri: str,
    kind: str,
    ranges: list[str],
    unit_individuals: dict[str, list[str]],
) -> list[str]:
    min_c, max_c = CARDINALITY.get((cls, iri), (None, None))
    block = [f"    sh:path {_curie(iri)}"]
    if iri == f"{OM2}hasUnit":
        unit_cls = MEASURE_UNIT_CLASS.get(cls, f"{OM2}Unit")
        block.append(f"    sh:class {_curie(unit_cls)}")
        block.append("    sh:nodeKind sh:IRI")
        allowed = unit_individuals.get(unit_cls, [])
        if allowed:
            listed = " ".join(_curie(item) for item in allowed)
            block.append(f"    sh:in ( {listed} )")
    elif kind == "object":
        class_range = next((rng for rng in ranges if not rng.startswith(XSD)), None)
        if class_range == ONTOLAB_EQUIPMENT:
            class_range = f"{ONTOSYN}Equipment"
        if class_range:
            block.append(f"    sh:class {_curie(class_range)}")
        block.append("    sh:nodeKind sh:IRI")
    else:
        hint = DATATYPE_HINTS.get(iri)
        if hint and "|" in hint:
            alts = " ".join(f"[ sh:datatype {item} ]" for item in hint.split("|"))
            block.append(f"    sh:or ( {alts} )")
        elif hint:
            block.append(f"    sh:datatype {hint}")
    if min_c is not None:
        block.append(f"    sh:minCount {min_c}")
    if max_c is not None:
        block.append(f"    sh:maxCount {max_c}")
    return block


def _render_shapes(
    classes: list[str],
    props_by_class: dict[str, list[tuple[str, str, list[str]]]],
    unit_individuals: dict[str, list[str]],
    *,
    header: list[str],
    allowed_paths: set[str] | None = None,
    allowed_classes: set[str] | None = None,
    sparql: str = "",
) -> str:
    lines = [
        "@prefix ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/> .",
        "@prefix om-2: <http://www.ontology-of-units-of-measure.org/resource/om-2/> .",
        "@prefix ontomops: <https://www.theworldavatar.com/kg/ontomops/> .",
        "@prefix bibo: <http://purl.org/ontology/bibo/> .",
        "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .",
        "@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .",
        "@prefix sh: <http://www.w3.org/ns/shacl#> .",
        "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .",
        "",
        *header,
        "",
    ]
    for cls in classes:
        if allowed_classes is not None and cls not in allowed_classes:
            continue
        seen: set[str] = set()
        prop_blocks = []
        for iri, kind, ranges in props_by_class.get(cls, []):
            if iri in seen:
                continue
            if allowed_paths is not None and iri not in allowed_paths:
                continue
            seen.add(iri)
            prop_blocks.append(_property_block(cls, iri, kind, ranges, unit_individuals))
        if not prop_blocks:
            continue
        lines.append(f"{_curie(cls)}Shape a sh:NodeShape ;")
        lines.append(f"  sh:targetClass {_curie(cls)} ;")
        for idx, block in enumerate(prop_blocks):
            suffix = " ;" if idx < len(prop_blocks) - 1 else " ."
            lines.append("  sh:property [")
            lines.append(" ;\n".join(block) + "\n  ]" + suffix)
        lines.append("")
    if sparql.strip():
        lines.append(sparql.rstrip())
        lines.append("")
    return "\n".join(lines) + "\n"


def generate() -> str:
    classes, props_by_class, unit_individuals = _ontology_index()
    return _render_shapes(
        classes,
        props_by_class,
        unit_individuals,
        header=[
            "# OntoSynthesis SHACL shapes for OntoLogX.",
            "# Generated from data/ontologies/ontosynthesis.ttl plus data/ontologies/om2.ttl.",
            "# om-2:hasUnit is an object property to an OM-2 unit individual, not xsd:string.",
            "# closedByTypes is intentionally omitted: gold graphs type steps as both",
            "# the concrete subclass and ontosyn:SynthesisStep.",
        ],
        sparql=_SPARQL_SHAPES,
    )


_SPARQL_SHAPES = """
# T-Box SPARQL shapes added after the 3-paper OntoLogX run.
# These were invisible to property-only SHACL: graphs conformed while violating comments.

ontosyn:UniqueStepOrderShape a sh:NodeShape ;
  sh:targetClass ontosyn:ChemicalSynthesis ;
  sh:sparql [
    sh:message "ontosyn:hasOrder must be unique within one ChemicalSynthesis" ;
    sh:select '''
      PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
      SELECT $this ?order
      WHERE {
        $this ontosyn:hasSynthesisStep ?a, ?b .
        FILTER(?a != ?b)
        ?a ontosyn:hasOrder ?order .
        ?b ontosyn:hasOrder ?order .
      }
    '''
  ] .

ontosyn:ContiguousStepOrderShape a sh:NodeShape ;
  sh:targetClass ontosyn:ChemicalSynthesis ;
  sh:sparql [
    sh:message "ontosyn:hasOrder must start at 1 and max(order) must equal step count" ;
    sh:select '''
      PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
      PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
      SELECT $this ?minO ?maxO ?n
      WHERE {
        {
          SELECT $this (COUNT(?step) AS ?n) (MIN(xsd:integer(?ord)) AS ?minO) (MAX(xsd:integer(?ord)) AS ?maxO)
          WHERE {
            $this ontosyn:hasSynthesisStep ?step .
            ?step ontosyn:hasOrder ?ord .
          }
        }
        FILTER(?minO != 1 || ?maxO != ?n)
      }
    '''
  ] .

ontosyn:BareSynthesisStepShape a sh:NodeShape ;
  sh:targetClass ontosyn:SynthesisStep ;
  sh:sparql [
    sh:message "SynthesisStep must also be a concrete subclass (Add/Stir/HeatChill/...)" ;
    sh:select '''
      PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
      PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
      SELECT $this
      WHERE {
        $this a ontosyn:SynthesisStep .
        FILTER NOT EXISTS { $this a ontosyn:Add }
        FILTER NOT EXISTS { $this a ontosyn:Stir }
        FILTER NOT EXISTS { $this a ontosyn:HeatChill }
        FILTER NOT EXISTS { $this a ontosyn:Evaporate }
        FILTER NOT EXISTS { $this a ontosyn:Sonicate }
        FILTER NOT EXISTS { $this a ontosyn:Crystallize }
        FILTER NOT EXISTS { $this a ontosyn:Transfer }
        FILTER NOT EXISTS { $this a ontosyn:Separate }
        FILTER NOT EXISTS { $this a ontosyn:Filter }
        FILTER NOT EXISTS { $this a ontosyn:Dry }
      }
    '''
  ] .

ontosyn:WashFilterNeedsSolventShape a sh:NodeShape ;
  sh:targetClass ontosyn:Filter ;
  sh:sparql [
    sh:message "Filter whose label describes a wash must have ontosyn:hasWashingSolvent" ;
    sh:select '''
      PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
      PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
      SELECT $this ?label
      WHERE {
        $this rdfs:label ?label .
        FILTER(REGEX(LCASE(STR(?label)), "wash"))
        FILTER NOT EXISTS { $this ontosyn:hasWashingSolvent ?solvent }
      }
    '''
  ] .

ontosyn:HeatHoldIsNotStirShape a sh:NodeShape ;
  sh:targetClass ontosyn:Stir ;
  sh:sparql [
    sh:message "A solvothermal heat-and-hold belongs on HeatChill, not Stir" ;
    sh:select '''
      PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
      PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
      SELECT $this ?label
      WHERE {
        $this rdfs:label ?label .
        FILTER(REGEX(LCASE(STR(?label)), "heat|solvothermal|degc|°c"))
      }
    '''
  ] .

ontosyn:CoolingNeedsTemperatureShape a sh:NodeShape ;
  sh:targetClass ontosyn:HeatChill ;
  sh:sparql [
    sh:message "Cooling / room-temperature HeatChill must keep hasTargetTemperature" ;
    sh:select '''
      PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
      PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
      SELECT $this ?label
      WHERE {
        $this rdfs:label ?label .
        FILTER(REGEX(LCASE(STR(?label)), "cool|room temperature|ambient"))
        FILTER NOT EXISTS { $this ontosyn:hasTargetTemperature ?temp }
      }
    '''
  ] .

ontosyn:VesselEnvironmentValuesShape a sh:NodeShape ;
  sh:targetClass ontosyn:VesselEnvironment ;
  sh:property [
    sh:path rdfs:label ;
    sh:minCount 1 ;
    sh:maxCount 1 ;
    sh:datatype xsd:string
  ] .

ontosyn:MeasureLabelShape a sh:NodeShape ;
  sh:targetClass om-2:Temperature, om-2:Duration, om-2:Pressure, om-2:Volume, om-2:TemperatureRate, om-2:AmountOfSubstanceFraction ;
  sh:property [
    sh:path rdfs:label ;
    sh:minCount 1 ;
    sh:maxCount 1 ;
    sh:datatype xsd:string
  ] .

ontosyn:NumericMeasureNeedsIriUnitShape a sh:NodeShape ;
  sh:targetClass om-2:Temperature, om-2:Duration, om-2:Pressure, om-2:Volume, om-2:TemperatureRate, om-2:AmountOfSubstanceFraction ;
  sh:sparql [
    sh:message "A numeric OM-2 measure must have om-2:hasUnit pointing at an OM-2 unit IRI, not a string" ;
    sh:select '''
      PREFIX om-2: <http://www.ontology-of-units-of-measure.org/resource/om-2/>
      SELECT $this ?unit
      WHERE {
        $this om-2:hasNumericalValue ?value .
        FILTER NOT EXISTS {
          $this om-2:hasUnit ?unit .
          FILTER(isIRI(?unit))
        }
      }
    '''
  ] .
"""


_PREFIX_IRIS = {
    "ontosyn": ONTOSYN,
    "om-2": OM2,
    "om2": OM2,
    "ontomops": ONTOMOPS,
    "rdfs": RDFS_NS,
    "bibo": "http://purl.org/ontology/bibo/",
}

_BARE_IRIS = {
    "Document": BIBO_DOCUMENT,
    "MetalOrganicPolyhedron": f"{ONTOMOPS}MetalOrganicPolyhedron",
    "hasCCDCNumber": f"{ONTOMOPS}hasCCDCNumber",
    "AmountOfSubstanceFraction": f"{OM2}AmountOfSubstanceFraction",
    "Temperature": f"{OM2}Temperature",
    "Duration": f"{OM2}Duration",
    "Pressure": f"{OM2}Pressure",
    "Volume": f"{OM2}Volume",
    "TemperatureRate": f"{OM2}TemperatureRate",
}

SPARQL_LAYER_BLOCKS: dict[int, str] = {
    2: "",
    3: """
ontosyn:UniqueStepOrderShape a sh:NodeShape ;
  sh:targetClass ontosyn:ChemicalSynthesis ;
  sh:sparql [
    sh:message "ontosyn:hasOrder must be unique within one ChemicalSynthesis" ;
    sh:select '''
      PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
      SELECT $this ?order
      WHERE {
        $this ontosyn:hasSynthesisStep ?a, ?b .
        FILTER(?a != ?b)
        ?a ontosyn:hasOrder ?order .
        ?b ontosyn:hasOrder ?order .
      }
    '''
  ] .

ontosyn:ContiguousStepOrderShape a sh:NodeShape ;
  sh:targetClass ontosyn:ChemicalSynthesis ;
  sh:sparql [
    sh:message "ontosyn:hasOrder must start at 1 and max(order) must equal step count" ;
    sh:select '''
      PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
      PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
      SELECT $this ?minO ?maxO ?n
      WHERE {
        {
          SELECT $this (COUNT(?step) AS ?n) (MIN(xsd:integer(?ord)) AS ?minO) (MAX(xsd:integer(?ord)) AS ?maxO)
          WHERE {
            $this ontosyn:hasSynthesisStep ?step .
            ?step ontosyn:hasOrder ?ord .
          }
        }
        FILTER(?minO != 1 || ?maxO != ?n)
      }
    '''
  ] .

ontosyn:BareSynthesisStepShape a sh:NodeShape ;
  sh:targetClass ontosyn:SynthesisStep ;
  sh:sparql [
    sh:message "SynthesisStep must also be a concrete subclass (Add/Stir/HeatChill/...)" ;
    sh:select '''
      PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
      PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
      SELECT $this
      WHERE {
        $this a ontosyn:SynthesisStep .
        FILTER NOT EXISTS { $this a ontosyn:Add }
        FILTER NOT EXISTS { $this a ontosyn:Stir }
        FILTER NOT EXISTS { $this a ontosyn:HeatChill }
        FILTER NOT EXISTS { $this a ontosyn:Evaporate }
        FILTER NOT EXISTS { $this a ontosyn:Sonicate }
        FILTER NOT EXISTS { $this a ontosyn:Crystallize }
        FILTER NOT EXISTS { $this a ontosyn:Transfer }
        FILTER NOT EXISTS { $this a ontosyn:Separate }
        FILTER NOT EXISTS { $this a ontosyn:Filter }
        FILTER NOT EXISTS { $this a ontosyn:Dry }
      }
    '''
  ] .

ontosyn:WashFilterNeedsSolventShape a sh:NodeShape ;
  sh:targetClass ontosyn:Filter ;
  sh:sparql [
    sh:message "Filter whose label describes a wash must have ontosyn:hasWashingSolvent" ;
    sh:select '''
      PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
      PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
      SELECT $this ?label
      WHERE {
        $this rdfs:label ?label .
        FILTER(REGEX(LCASE(STR(?label)), "wash"))
        FILTER NOT EXISTS { $this ontosyn:hasWashingSolvent ?solvent }
      }
    '''
  ] .

ontosyn:HeatHoldIsNotStirShape a sh:NodeShape ;
  sh:targetClass ontosyn:Stir ;
  sh:sparql [
    sh:message "A solvothermal heat-and-hold belongs on HeatChill, not Stir" ;
    sh:select '''
      PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
      PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
      SELECT $this ?label
      WHERE {
        $this rdfs:label ?label .
        FILTER(REGEX(LCASE(STR(?label)), "heat|solvothermal|degc|°c"))
      }
    '''
  ] .

ontosyn:CoolingNeedsTemperatureShape a sh:NodeShape ;
  sh:targetClass ontosyn:HeatChill ;
  sh:sparql [
    sh:message "Cooling / room-temperature HeatChill must keep hasTargetTemperature" ;
    sh:select '''
      PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
      PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
      SELECT $this ?label
      WHERE {
        $this rdfs:label ?label .
        FILTER(REGEX(LCASE(STR(?label)), "cool|room temperature|ambient"))
        FILTER NOT EXISTS { $this ontosyn:hasTargetTemperature ?temp }
      }
    '''
  ] .

ontosyn:VesselEnvironmentValuesShape a sh:NodeShape ;
  sh:targetClass ontosyn:VesselEnvironment ;
  sh:property [
    sh:path rdfs:label ;
    sh:minCount 1 ;
    sh:maxCount 1 ;
    sh:datatype xsd:string
  ] .

ontosyn:MeasureLabelShape a sh:NodeShape ;
  sh:targetClass om-2:Temperature, om-2:Duration, om-2:Pressure, om-2:Volume, om-2:TemperatureRate, om-2:AmountOfSubstanceFraction ;
  sh:property [
    sh:path rdfs:label ;
    sh:minCount 1 ;
    sh:maxCount 1 ;
    sh:datatype xsd:string
  ] .
""",
    4: """
ontosyn:MeasureLabelShape a sh:NodeShape ;
  sh:targetClass om-2:Temperature, om-2:Duration, om-2:Pressure, om-2:Volume, om-2:TemperatureRate, om-2:AmountOfSubstanceFraction ;
  sh:property [
    sh:path rdfs:label ;
    sh:minCount 1 ;
    sh:maxCount 1 ;
    sh:datatype xsd:string
  ] .

ontosyn:NumericMeasureNeedsIriUnitShape a sh:NodeShape ;
  sh:targetClass om-2:Temperature, om-2:Duration, om-2:Pressure, om-2:Volume, om-2:TemperatureRate, om-2:AmountOfSubstanceFraction ;
  sh:sparql [
    sh:message "A numeric OM-2 measure must have om-2:hasUnit pointing at an OM-2 unit IRI, not a string" ;
    sh:select '''
      PREFIX om-2: <http://www.ontology-of-units-of-measure.org/resource/om-2/>
      SELECT $this ?unit
      WHERE {
        $this om-2:hasNumericalValue ?value .
        FILTER NOT EXISTS {
          $this om-2:hasUnit ?unit .
          FILTER(isIRI(?unit))
        }
      }
    '''
  ] .
""",
}


def expand_surface_token(token: str) -> str:
    name = str(token or "").strip()
    if not name:
        raise ValueError("empty surface token")
    if name.startswith("http://") or name.startswith("https://"):
        return name
    if name in _BARE_IRIS:
        return _BARE_IRIS[name]
    if ":" in name:
        prefix, local = name.split(":", 1)
        base = _PREFIX_IRIS.get(prefix)
        if base is None:
            raise ValueError(f"unknown surface prefix {prefix!r} in {token!r}")
        return f"{base}{local}"
    return f"{ONTOSYN}{name}"


def surface_path_iris(spec: dict) -> set[str]:
    names = list(spec.get("object_properties") or []) + list(spec.get("datatype_properties") or [])
    return {expand_surface_token(name) for name in names}


_MEASURE_PRODUCING_PATHS = {
    f"{ONTOSYN}hasTargetTemperature",
    f"{ONTOSYN}hasStepDuration",
    f"{ONTOSYN}hasTemperatureRate",
    f"{ONTOSYN}hasDryingTemperature",
    f"{ONTOSYN}hasDryingPressure",
    f"{ONTOSYN}hasEvaporationTemperature",
    f"{ONTOSYN}hasEvaporationPressure",
    f"{ONTOSYN}hasStirringTemperature",
    f"{ONTOSYN}hasCrystallizationTargetTemperature",
    f"{ONTOSYN}isEvaporatedToVolume",
    f"{ONTOSYN}hasYield",
    f"{ONTOSYN}hasTransferedAmount",
    f"{OM2}hasNumericalValue",
    f"{OM2}hasUnit",
}


def surface_class_iris(
    spec: dict,
    *,
    props_by_class: dict[str, list[tuple[str, str, list[str]]]] | None = None,
) -> set[str]:
    named = list(spec.get("classes") or []) + list(spec.get("linked_helper_targets") or [])
    allowed = {expand_surface_token(name) for name in named}
    allowed.add(f"{ONTOSYN}ChemicalSynthesis")
    allowed_paths = surface_path_iris(spec)
    for cls, iri in CARDINALITY:
        if iri in allowed_paths and iri != f"{RDFS_NS}label":
            allowed.add(cls)
    for cls, rows in (props_by_class or {}).items():
        for iri, _kind, ranges in rows:
            if iri not in allowed_paths or iri == f"{RDFS_NS}label":
                continue
            allowed.add(cls)
            for rng in ranges:
                if rng in MEASURE_CLASSES or rng == BIBO_DOCUMENT:
                    allowed.add(rng)
    if allowed_paths & _MEASURE_PRODUCING_PATHS:
        allowed.update(MEASURE_CLASSES)
    return allowed


def generate_layer(layer: int, spec: dict | None = None) -> str:
    if spec is None:
        from iteration_guides import load_iteration_surfaces

        spec = load_iteration_surfaces()[int(layer)]
    classes, props_by_class, unit_individuals = _ontology_index()
    allowed_paths = surface_path_iris(spec)
    allowed_classes = surface_class_iris(spec, props_by_class=props_by_class)
    header = [
        f"# OntoSynthesis SHACL for Pipeline iter{layer} owned surface only.",
        "# Parallel to resources/iteration_surfaces.json. Not the full-graph oracle.",
        "# Generated from data/ontologies/ontosynthesis.ttl plus data/ontologies/om2.ttl.",
        f"# slot_kind: {spec.get('slot_kind') or 'unknown'}",
    ]
    return _render_shapes(
        classes,
        props_by_class,
        unit_individuals,
        header=header,
        allowed_paths=allowed_paths,
        allowed_classes=allowed_classes,
        sparql=SPARQL_LAYER_BLOCKS.get(int(layer), ""),
    )


def layered_output_path(layer: int) -> Path:
    return OUTPUT_PATH.with_name(f"ontosynthesis_shacl_iter{int(layer)}.ttl")


def write_layered_shapes() -> dict[int, Path]:
    from iteration_guides import load_iteration_surfaces

    written: dict[int, Path] = {}
    for layer, spec in sorted(load_iteration_surfaces().items()):
        path = layered_output_path(layer)
        path.write_text(generate_layer(layer, spec), encoding="utf-8")
        written[int(layer)] = path
    return written


def main() -> None:
    from layered_shacl import write_manifest

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(generate(), encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH}")
    written = write_layered_shapes()
    for layer, path in written.items():
        print(f"Wrote iter{layer} surface SHACL {path}")
    print(f"Wrote {write_manifest(written)}")


if __name__ == "__main__":
    main()
