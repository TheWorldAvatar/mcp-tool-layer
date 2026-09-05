"""Generate OntoSpecies SHACL shapes from ontospecies-subgraph.ttl.

Used as the OntoLogX extension-layer correction oracle: inherit the full
OntoSyn main graph, then add Species typing plus formula / CCDC /
characterization. Cardinality is fail-closed on value nodes that exist,
but characterization itself is optional.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from rdflib import OWL, RDF, RDFS, Graph, URIRef
from rdflib.collection import Collection

REPO_ROOT = Path(__file__).resolve().parents[2]
ONTOLOGY_PATH = REPO_ROOT / "data" / "ontologies" / "ontospecies-subgraph.ttl"
OUTPUT_PATH = Path(__file__).resolve().parent / "resources" / "ontospecies_shacl.ttl"

ONTOSPECIES = "http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#"
PERIODIC = "http://www.daml.org/2003/01/periodictable/PeriodicTable#"
ONTOSYN = "https://www.theworldavatar.com/kg/OntoSyn/"
RDFS_NS = "http://www.w3.org/2000/01/rdf-schema#"
XSD = "http://www.w3.org/2001/XMLSchema#"

CARDINALITY: dict[tuple[str, str], tuple[int | None, int | None]] = {
    (f"{ONTOSPECIES}Species", f"{RDFS_NS}label"): (0, 1),
    (f"{ONTOSPECIES}Species", f"{ONTOSPECIES}hasProductName"): (0, 1),
    (f"{ONTOSPECIES}Species", f"{ONTOSPECIES}hasMolecularFormula"): (0, 1),
    (f"{ONTOSPECIES}Species", f"{ONTOSPECIES}hasChemicalFormula"): (0, 1),
    (f"{ONTOSPECIES}Species", f"{ONTOSPECIES}hasCCDCNumber"): (0, 1),
    (f"{ONTOSPECIES}Species", f"{ONTOSPECIES}hasCharacterizationSession"): (0, 1),
    (f"{ONTOSPECIES}MolecularFormula", f"{ONTOSPECIES}hasMolecularFormulaValue"): (1, 1),
    (f"{ONTOSPECIES}ChemicalFormula", f"{ONTOSPECIES}hasChemicalFormulaValue"): (1, 1),
    (f"{ONTOSPECIES}CCDCNumber", f"{ONTOSPECIES}hasCCDCNumberValue"): (1, 1),
    (f"{ONTOSPECIES}HNMRData", f"{ONTOSPECIES}hasShifts"): (0, 1),
    (f"{ONTOSPECIES}HNMRData", f"{ONTOSPECIES}hasTemperature"): (0, 1),
    (f"{ONTOSPECIES}InfraredSpectroscopyData", f"{ONTOSPECIES}hasBands"): (0, 1),
    (f"{ONTOSPECIES}Solvent", f"{ONTOSPECIES}hasSolventName"): (0, 1),
    (f"{ONTOSPECIES}Material", f"{ONTOSPECIES}hasMaterialName"): (0, 1),
    (f"{ONTOSPECIES}Device", f"{ONTOSPECIES}hasDeviceName"): (0, 1),
    (f"{ONTOSPECIES}HNMRDevice", f"{ONTOSPECIES}hasDeviceName"): (0, 1),
    (f"{ONTOSPECIES}HNMRDevice", f"{ONTOSPECIES}hasFrequency"): (0, 1),
    (f"{ONTOSPECIES}ElementalAnalysisDevice", f"{ONTOSPECIES}hasDeviceName"): (0, 1),
    (f"{ONTOSPECIES}InfraredSpectroscopyDevice", f"{ONTOSPECIES}hasDeviceName"): (0, 1),
    (f"{ONTOSPECIES}WeightPercentage", f"{ONTOSPECIES}hasPercentageValue"): (0, 1),
    (f"{PERIODIC}Element", f"{ONTOSPECIES}hasElementName"): (0, 1),
    (f"{PERIODIC}Element", f"{ONTOSPECIES}hasElementSymbol"): (0, 1),
    (f"{ONTOSPECIES}AtomicWeight", f"{ONTOSPECIES}hasAtomicWeightValue"): (0, 1),
}

DATATYPE_HINTS: dict[str, str] = {
    f"{ONTOSPECIES}hasProductName": "xsd:string",
    f"{ONTOSPECIES}hasMolecularFormulaValue": "xsd:string",
    f"{ONTOSPECIES}hasChemicalFormulaValue": "xsd:string",
    f"{ONTOSPECIES}hasCCDCNumberValue": "xsd:string",
    f"{ONTOSPECIES}hasShifts": "xsd:string",
    f"{ONTOSPECIES}hasTemperature": "xsd:string",
    f"{ONTOSPECIES}hasBands": "xsd:string",
    f"{ONTOSPECIES}hasSolventName": "xsd:string",
    f"{ONTOSPECIES}hasMaterialName": "xsd:string",
    f"{ONTOSPECIES}hasDeviceName": "xsd:string",
    f"{ONTOSPECIES}hasFrequency": "xsd:string",
    f"{ONTOSPECIES}hasWeightPercentageCalculatedValue": "xsd:string",
    f"{ONTOSPECIES}hasWeightPercentageExperimentalValue": "xsd:string",
    f"{ONTOSPECIES}hasElementName": "xsd:string",
    f"{ONTOSPECIES}hasElementSymbol": "xsd:string",
    f"{ONTOSPECIES}hasPercentageValue": "xsd:float|xsd:double",
    f"{ONTOSPECIES}hasAtomicWeightValue": "xsd:float|xsd:double",
    f"{RDFS_NS}label": "xsd:string",
}


def _local(iri: str) -> str:
    return iri.rsplit("/", 1)[-1].rsplit("#", 1)[-1]


def _curie(iri: str) -> str:
    if iri.startswith(ONTOSPECIES):
        return f"ontospecies:{_local(iri)}"
    if iri.startswith(PERIODIC):
        return f"periodic:{_local(iri)}"
    if iri.startswith(ONTOSYN):
        return f"ontosyn:{_local(iri)}"
    if iri.startswith(RDFS_NS):
        return f"rdfs:{_local(iri)}"
    return f"<{iri}>"


def _expand_union(graph: Graph, node) -> list[URIRef]:
    if node is None:
        return []
    union = graph.value(node, OWL.unionOf)
    if union is not None:
        return [item for item in Collection(graph, union) if isinstance(item, URIRef)]
    if isinstance(node, URIRef):
        return [node]
    return []


def _class_iris(graph: Graph) -> list[str]:
    iris = set()
    for cls in graph.subjects(RDF.type, OWL.Class):
        if not isinstance(cls, URIRef):
            continue
        iri = str(cls)
        if iri.startswith(ONTOSPECIES) or iri.startswith(PERIODIC):
            iris.add(iri)
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
            if not iri.startswith(ONTOSPECIES):
                continue
            domains: list[str] = []
            ranges: list[str] = []
            for domain in graph.objects(prop, RDFS.domain):
                domains.extend(
                    str(item) for item in _expand_union(graph, domain) if isinstance(item, URIRef)
                )
            for rng in graph.objects(prop, RDFS.range):
                ranges.extend(
                    str(item) for item in _expand_union(graph, rng) if isinstance(item, URIRef)
                )
            rows.append((iri, kind, domains, ranges))
    rows.append((f"{RDFS_NS}label", "datatype", [], [f"{XSD}string"]))
    return rows


def _ontology_index() -> tuple[list[str], dict[str, list[tuple[str, str, list[str]]]]]:
    graph = Graph()
    graph.parse(str(ONTOLOGY_PATH), format="turtle")
    classes = _class_iris(graph)
    props_by_class: dict[str, list[tuple[str, str, list[str]]]] = defaultdict(list)
    for iri, kind, domains, ranges in _properties(graph):
        targets = domains or classes
        for cls in targets:
            if cls not in classes:
                continue
            props_by_class[cls].append((iri, kind, ranges))
        if iri == f"{RDFS_NS}label":
            for cls in classes:
                props_by_class[cls].append((iri, kind, ranges))
    return classes, props_by_class


def _property_block(cls: str, iri: str, kind: str, ranges: list[str]) -> list[str]:
    min_c, max_c = CARDINALITY.get((cls, iri), (None, None))
    block = [f"    sh:path {_curie(iri)}"]
    if kind == "object":
        class_range = next((rng for rng in ranges if not rng.startswith(XSD)), None)
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


_SPARQL_SHAPES = """
# Extension success criterion: the inherited ChemicalSynthesis must expose
# its product as ontospecies:Species (same node as ChemicalOutput, or a
# newly linked output). Characterization is optional.

ontospecies:SpeciesOutputRequiredShape a sh:NodeShape ;
  sh:targetClass ontosyn:ChemicalSynthesis ;
  sh:sparql [
    sh:message "ChemicalSynthesis must haveChemicalOutput a node typed ontospecies:Species" ;
    sh:select '''
      PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
      PREFIX ontospecies: <http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#>
      SELECT $this
      WHERE {
        FILTER NOT EXISTS {
          $this ontosyn:hasChemicalOutput ?out .
          ?out a ontospecies:Species .
        }
      }
    '''
  ] .

ontospecies:UniqueSpeciesOutputShape a sh:NodeShape ;
  sh:targetClass ontosyn:ChemicalSynthesis ;
  sh:sparql [
    sh:message "At most one ontospecies:Species may be linked via hasChemicalOutput" ;
    sh:select '''
      PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
      PREFIX ontospecies: <http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#>
      SELECT $this
      WHERE {
        $this ontosyn:hasChemicalOutput ?a, ?b .
        FILTER(?a != ?b)
        ?a a ontospecies:Species .
        ?b a ontospecies:Species .
      }
    '''
  ] .

ontospecies:SpeciesMustBeOutputShape a sh:NodeShape ;
  sh:targetClass ontospecies:Species ;
  sh:sparql [
    sh:message "ontospecies:Species must be a ChemicalOutput; do not mint a detached species identity" ;
    sh:select '''
      PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
      SELECT $this
      WHERE {
        FILTER NOT EXISTS { ?synth ontosyn:hasChemicalOutput $this . }
      }
    '''
  ] .
"""


def generate() -> str:
    classes, props_by_class = _ontology_index()
    lines = [
        "@prefix ontospecies: <http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#> .",
        "@prefix periodic: <http://www.daml.org/2003/01/periodictable/PeriodicTable#> .",
        "@prefix ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/> .",
        "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .",
        "@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .",
        "@prefix sh: <http://www.w3.org/ns/shacl#> .",
        "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .",
        "",
        "# OntoSpecies SHACL for OntoLogX extension (inherit OntoSyn main graph).",
        "# Generated from data/ontologies/ontospecies-subgraph.ttl.",
        "# closedByTypes is omitted so a ChemicalOutput may also be a Species.",
        "",
    ]
    for cls in classes:
        seen: set[str] = set()
        prop_blocks = []
        for iri, kind, ranges in props_by_class.get(cls, []):
            if iri in seen:
                continue
            seen.add(iri)
            prop_blocks.append(_property_block(cls, iri, kind, ranges))
        if not prop_blocks:
            continue
        lines.append(f"{_curie(cls)}Shape a sh:NodeShape ;")
        lines.append(f"  sh:targetClass {_curie(cls)} ;")
        for idx, block in enumerate(prop_blocks):
            suffix = " ;" if idx < len(prop_blocks) - 1 else " ."
            lines.append("  sh:property [")
            lines.append(" ;\n".join(block) + "\n  ]" + suffix)
        lines.append("")
    lines.append(_SPARQL_SHAPES.rstrip())
    lines.append("")
    return "\n".join(lines) + "\n"


def write_shapes(path: Path | None = None) -> Path:
    dest = path or OUTPUT_PATH
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(generate(), encoding="utf-8")
    return dest


def main() -> None:
    dest = write_shapes()
    print(f"Wrote {dest}")


if __name__ == "__main__":
    main()
