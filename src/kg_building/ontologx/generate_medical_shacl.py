"""Generate OntoMed SHACL shapes from the medical T-Box.

Used as the OntoLogX correction oracle for medical graphs. Shapes follow the
OntoSyn generator: one NodeShape per class, object properties get ``sh:class``
plus ``sh:nodeKind sh:IRI``, datatype properties get ``sh:datatype`` from
``rdfs:range``. Binary checklist fields additionally constrain the active
value to ``"1"`` and stay optional (omit when false). closedByTypes is omitted
so a node may carry extra types.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from rdflib import OWL, RDF, RDFS, Graph, URIRef
from rdflib.collection import Collection

from paths import REPO_ROOT

ONTOLOGY_PATH = REPO_ROOT / "data" / "ontologies" / "medical_case_schema_de_non_flat_v4.ttl"
OUTPUT_PATH = Path(__file__).resolve().parent / "resources" / "medical_shacl.ttl"

MED = "https://www.theworldavatar.com/kg/medical/"
RDFS_NS = "http://www.w3.org/2000/01/rdf-schema#"
XSD = "http://www.w3.org/2001/XMLSchema#"
VALUE_KIND = URIRef(f"{MED}valueKind")
BINARY_CHECKLIST = "binary_checklist"

# Object hops that the T-Box treats as at most one per case. Diagnosis,
# procedure, and complication stay unbounded. No minCount: Pipeline/OX often
# omit SurgicalApproach or PathologyOutcome.
CARDINALITY: dict[tuple[str, str], tuple[int | None, int | None]] = {
    (f"{MED}MedicalCase", f"{RDFS_NS}label"): (1, 1),
    (f"{MED}MedicalCase", f"{MED}hasPatientInfo"): (0, 1),
    (f"{MED}MedicalCase", f"{MED}hasTimeline"): (0, 1),
    (f"{MED}MedicalCase", f"{MED}hasSurgicalApproach"): (0, 1),
    (f"{MED}MedicalCase", f"{MED}hasSurgicalTeam"): (0, 1),
    (f"{MED}MedicalCase", f"{MED}hasPathologyOutcome"): (0, 1),
}

# CSV cells are single-valued; checklist/free-text fields never repeat.
DEFAULT_DATATYPE_MAX = 1


def _local(iri: str) -> str:
    return iri.rsplit("/", 1)[-1].rsplit("#", 1)[-1]


def _curie(iri: str) -> str:
    if iri.startswith(MED):
        return f"medical:{_local(iri)}"
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
        if iri.startswith(MED) and not iri.endswith("/Thing"):
            iris.add(iri)
    iris.add(f"{MED}MedicalCase")
    return sorted(iris)


def _value_kind(graph: Graph, prop: URIRef) -> str:
    raw = graph.value(prop, VALUE_KIND)
    return str(raw).strip() if raw is not None else ""


def _xsd_hint(ranges: list[str]) -> str | None:
    seen: list[str] = []
    for rng in ranges:
        if rng.startswith(XSD):
            curie = f"xsd:{rng.rsplit('#', 1)[-1]}"
            if curie not in seen:
                seen.append(curie)
    return "|".join(seen) if seen else None


def _append_datatype(block: list[str], hint: str | None) -> None:
    if not hint:
        return
    if "|" in hint:
        alts = " ".join(f"[ sh:datatype {item} ]" for item in hint.split("|"))
        block.append(f"    sh:or ( {alts} )")
        return
    block.append(f"    sh:datatype {hint}")


def _properties(graph: Graph) -> list[tuple[str, str, list[str], list[str], str]]:
    rows = []
    for pred_type, kind in (
        (OWL.ObjectProperty, "object"),
        (OWL.DatatypeProperty, "datatype"),
    ):
        for prop in graph.subjects(RDF.type, pred_type):
            if not isinstance(prop, URIRef):
                continue
            iri = str(prop)
            if not iri.startswith(MED):
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
            rows.append((iri, kind, domains, ranges, _value_kind(graph, prop)))
    rows.append((f"{RDFS_NS}label", "datatype", [], [f"{XSD}string"], ""))
    return rows


def _ontology_index() -> tuple[list[str], dict[str, list[tuple[str, str, list[str], str]]]]:
    graph = Graph()
    graph.parse(str(ONTOLOGY_PATH), format="turtle")
    classes = _class_iris(graph)
    props_by_class: dict[str, list[tuple[str, str, list[str], str]]] = defaultdict(list)
    for iri, kind, domains, ranges, value_kind in _properties(graph):
        targets = domains or classes
        for cls in targets:
            if cls not in classes:
                continue
            props_by_class[cls].append((iri, kind, ranges, value_kind))
        if iri == f"{RDFS_NS}label":
            for cls in classes:
                props_by_class[cls].append((iri, kind, ranges, value_kind))
    return classes, props_by_class


def _property_block(
    cls: str,
    iri: str,
    kind: str,
    ranges: list[str],
    value_kind: str,
) -> list[str]:
    min_c, max_c = CARDINALITY.get((cls, iri), (None, None))
    if kind == "datatype" and max_c is None:
        max_c = DEFAULT_DATATYPE_MAX
    block = [f"    sh:path {_curie(iri)}"]
    if kind == "object":
        class_range = next((rng for rng in ranges if not rng.startswith(XSD)), None)
        if class_range:
            block.append(f"    sh:class {_curie(class_range)}")
        block.append("    sh:nodeKind sh:IRI")
    else:
        _append_datatype(block, _xsd_hint(ranges))
        if value_kind == BINARY_CHECKLIST:
            # T-Box: active value is "1"; otherwise omit the triple.
            block.append("    sh:in ( \"1\"^^xsd:string )")
    if min_c is not None:
        block.append(f"    sh:minCount {min_c}")
    if max_c is not None:
        block.append(f"    sh:maxCount {max_c}")
    return block


_SPARQL_SHAPES = """
# Comment-level constraints that property SHACL cannot see. Analogous to
# OntoSyn unique/contiguous hasOrder and NonVacuousGraphShape.

medical:NonVacuousGraphShape a sh:NodeShape ;
  sh:targetNode medical:GraphIntegrityAnchor ;
  sh:sparql [
    sh:message "Graph must contain at least one medical:MedicalCase" ;
    sh:select '''
      PREFIX medical: <https://www.theworldavatar.com/kg/medical/>
      SELECT $this
      WHERE {
        FILTER NOT EXISTS { ?case a medical:MedicalCase }
      }
    '''
  ] .

medical:ExclusiveSurgicalApproachShape a sh:NodeShape ;
  sh:targetClass medical:SurgicalApproach ;
  sh:sparql [
    sh:message "Final surgical approach is exclusive: at most one of offen, VATS, RATS may be the string 1" ;
    sh:select '''
      PREFIX medical: <https://www.theworldavatar.com/kg/medical/>
      SELECT $this
      WHERE {
        {
          $this medical:offen ?a .
          FILTER(STR(?a) = "1")
          $this medical:VATS ?b .
          FILTER(STR(?b) = "1")
        } UNION {
          $this medical:offen ?a .
          FILTER(STR(?a) = "1")
          $this medical:RATS ?b .
          FILTER(STR(?b) = "1")
        } UNION {
          $this medical:VATS ?a .
          FILTER(STR(?a) = "1")
          $this medical:RATS ?b .
          FILTER(STR(?b) = "1")
        }
      }
    '''
  ] .
"""


def generate() -> str:
    classes, props_by_class = _ontology_index()
    lines = [
        "@prefix medical: <https://www.theworldavatar.com/kg/medical/> .",
        "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .",
        "@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .",
        "@prefix sh: <http://www.w3.org/ns/shacl#> .",
        "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .",
        "",
        "# OntoMed SHACL shapes for OntoLogX.",
        "# Generated from data/ontologies/medical_case_schema_de_non_flat_v4.ttl.",
        "# Datatype ranges follow the T-Box (xsd:string, or xsd:integer for Alter /",
        "# Verweildauer / Dauer_d_zwischen_TuKo_und_OP).",
        "# binary_checklist fields accept only \"1\" and must be omitted when false.",
        "# Object hops from MedicalCase are optional; do not require every grouping.",
        "# closedByTypes is omitted so a node may carry extra types.",
        "",
    ]
    for cls in classes:
        seen: set[str] = set()
        prop_blocks = []
        for iri, kind, ranges, value_kind in props_by_class.get(cls, []):
            if iri in seen:
                continue
            seen.add(iri)
            prop_blocks.append(_property_block(cls, iri, kind, ranges, value_kind))
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
