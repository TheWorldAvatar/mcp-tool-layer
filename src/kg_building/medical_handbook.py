"""Render the frozen OntoMed T-Box handbook from the medical OWL file.

generic-noprompt and with-prompt load ``data/ontologies/medical_parsed.md``.
That freeze is the OntoMed analog of ``ontosynthesis_parsed.md``: class and
property comments from the T-Box, not a raw Turtle dump and not the thin
MCP ``parsed.md`` table. Regenerating it uses only the medical T-Box.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rdflib import Graph, Literal, RDFS, URIRef

from src.extraction_prompt_generation.tbox.parser import parse_ontology_ttl

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TBOX = _REPO_ROOT / "data" / "ontologies" / "medical_case_schema_de_non_flat_v4.ttl"
DEFAULT_HANDBOOK = _REPO_ROOT / "data" / "ontologies" / "medical_parsed.md"
MED = "https://www.theworldavatar.com/kg/medical/"
VALUE_KIND = URIRef(f"{MED}valueKind")
CSV_HEADER = URIRef(f"{MED}csvHeader")
CLASS_ORDER = (
    "MedicalCase",
    "PatientInfo",
    "CaseTimeline",
    "SurgicalApproach",
    "Procedure",
    "SurgicalTeam",
    "Diagnosis",
    "Complication",
    "PathologyOutcome",
)
SKIP_CLASSES = {"Thing"}


def _literal(graph: Graph, subject: URIRef, predicate: URIRef) -> str:
    for obj in graph.objects(subject, predicate):
        if isinstance(obj, Literal):
            return str(obj).replace("\r\n", "\n").replace("\r", "\n").strip()
    return ""


def _property_iris(parsed: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, spec in (parsed.get("properties") or {}).items():
        iri = str((spec or {}).get("iri") or "").strip()
        if name and iri:
            out[str(name)] = iri
    return out


def render_medical_parsed_markdown(ttl_path: Path | None = None) -> str:
    """Build the comment-preserving OntoMed handbook from the v4 T-Box."""
    path = Path(ttl_path or DEFAULT_TBOX)
    parsed = parse_ontology_ttl(str(path))
    graph = Graph()
    graph.parse(str(path), format="turtle")
    iris = _property_iris(parsed)
    classes = parsed.get("classes") or {}
    lines = [
        "# Authoritative OntoMed T-Box handbook",
        "",
        "This freeze is compiled from `medical_case_schema_de_non_flat_v4.ttl`.",
        "Class and property comments are verbatim T-Box text.",
        "`valueKind` and `csvHeader` are the T-Box annotations used for encoding",
        "and for TTL→CSV scoring. This is not occurrence/ownership mapping and",
        "not the with-prompt constructor guidance.",
        "",
    ]
    ordered = [name for name in CLASS_ORDER if name in classes]
    extras = sorted(
        name
        for name in classes
        if name not in CLASS_ORDER and name not in SKIP_CLASSES
    )
    for class_name in ordered + extras:
        spec = classes[class_name]
        lines.append(f"## Class: `{class_name}`")
        lines.append("")
        parents = [
            item
            for item in (spec.get("parent_classes") or [])
            if item and item not in SKIP_CLASSES
        ]
        if parents:
            joined = ", ".join(f"`{item}`" for item in parents)
            lines.append(f"**Parent classes:** {joined}")
            lines.append("")
        comment = str(spec.get("comment") or "").strip()
        if comment:
            lines.append("**Comment:**")
            lines.append("")
            lines.append(comment)
            lines.append("")
        datatype = spec.get("datatype_properties") or {}
        if datatype:
            lines.append("### Datatype properties")
            lines.append("")
            for prop_name in sorted(datatype):
                iri = iris.get(prop_name, "")
                subject = URIRef(iri) if iri else None
                range_name = str(datatype.get(prop_name) or "")
                kind = _literal(graph, subject, VALUE_KIND) if subject else ""
                header = _literal(graph, subject, CSV_HEADER) if subject else ""
                prop_comment = ""
                if subject is not None:
                    prop_comment = _literal(graph, subject, RDFS.comment)
                if not prop_comment:
                    prop_comment = str(
                        ((parsed.get("properties") or {}).get(prop_name) or {}).get(
                            "comment"
                        )
                        or ""
                    ).strip()
                lines.append(f"#### `{prop_name}`")
                lines.append("")
                meta = [f"Range: `{range_name}`" if range_name else ""]
                if kind:
                    meta.append(f"valueKind: `{kind}`")
                if header:
                    meta.append(f"csvHeader: `{header}`")
                lines.append("- " + "; ".join(item for item in meta if item))
                if prop_comment:
                    lines.append("")
                    lines.append(prop_comment)
                lines.append("")
        objects = spec.get("object_properties") or {}
        if objects:
            lines.append("### Object properties")
            lines.append("")
            lines.append("| Property | Range |")
            lines.append("|----------|-------|")
            for prop_name in sorted(objects):
                lines.append(f"| `{prop_name}` | `{objects[prop_name]}` |")
            lines.append("")
            for prop_name in sorted(objects):
                prop_comment = str(
                    ((parsed.get("properties") or {}).get(prop_name) or {}).get(
                        "comment"
                    )
                    or ""
                ).strip()
                if not prop_comment:
                    continue
                lines.append(f"- `{prop_name}`: {prop_comment}")
            if any(
                str(
                    ((parsed.get("properties") or {}).get(name) or {}).get("comment")
                    or ""
                ).strip()
                for name in objects
            ):
                lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_frozen_handbook(
    ttl_path: Path | None = None,
    dest: Path | None = None,
) -> Path:
    out = Path(dest or DEFAULT_HANDBOOK)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_medical_parsed_markdown(ttl_path), encoding="utf-8")
    return out


if __name__ == "__main__":
    path = write_frozen_handbook()
    print(f"Wrote {path}")
