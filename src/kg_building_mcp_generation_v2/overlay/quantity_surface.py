"""Compile OM-2 quantity tables from the occurrence surface plus the OM-2 T-Box."""

from __future__ import annotations

import json
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Mapping

from rdflib import Graph, URIRef
from rdflib.namespace import OWL, RDF, RDFS

OM2_NS = "http://www.ontology-of-units-of-measure.org/resource/om-2/"
EXAMPLE_NUMBER = "1"
_REPO = Path(__file__).resolve().parents[3]
_DEFAULT_OM2 = _REPO / "data" / "ontologies" / "om2.ttl"
_DEFAULT_ALIASES = _REPO / "data" / "ontologies" / "om2_unit_aliases.json"


def _local(iri: str) -> str:
    text = str(iri or "")
    if "#" in text:
        return text.rsplit("#", 1)[-1]
    return text.rsplit("/", 1)[-1]


def _subclasses(graph: Graph, root: URIRef) -> set[URIRef]:
    found = {root}
    changed = True
    while changed:
        changed = False
        for child, _, parent in graph.triples((None, RDFS.subClassOf, None)):
            if parent in found and child not in found and isinstance(child, URIRef):
                found.add(child)
                changed = True
    found.discard(root)
    return {node for node in found if isinstance(node, URIRef)}


def load_om2_graph(context: Any | None = None) -> Graph:
    graph = Graph()
    bundle = (getattr(context, "contract", None) or {}).get("tbox_bundle") or {}
    candidates: list[Path] = []
    for item in [bundle.get("primary") or {}, *(bundle.get("supporting") or [])]:
        raw = str(item.get("resolved_path") or "").strip()
        if raw:
            candidates.append(Path(raw))
    loaded = False
    for path in candidates:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if OM2_NS not in text and path.name not in {"om2.ttl", "om2_mock.ttl"}:
            continue
        graph.parse(str(path), format="turtle")
        loaded = True
    if not loaded and _DEFAULT_OM2.is_file():
        graph.parse(str(_DEFAULT_OM2), format="turtle")
    return graph


def _quantity_class_iris(graph: Graph) -> list[str]:
    root = URIRef(OM2_NS + "Quantity")
    iris = sorted({str(node) for node in _subclasses(graph, root)})
    if iris:
        return iris
    return sorted(
        {
            str(node)
            for node in graph.subjects(RDF.type, OWL.Class)
            if isinstance(node, URIRef) and str(node).startswith(OM2_NS)
            and _local(str(node)) not in {"Quantity", "Unit"}
            and not _local(str(node)).endswith("Unit")
        }
    )


def _match_quantity_class(stem: str, quantity_iris: list[str]) -> str | None:
    wanted = stem.casefold()
    exact = [iri for iri in quantity_iris if _local(iri).casefold() == wanted]
    if len(exact) == 1:
        return exact[0]
    if exact:
        return sorted(exact)[0]
    prefixed = [
        iri
        for iri in quantity_iris
        if _local(iri).casefold().startswith(wanted)
        or wanted.startswith(_local(iri).casefold())
    ]
    if len(prefixed) == 1:
        return prefixed[0]
    scored = sorted(
        quantity_iris,
        key=lambda iri: SequenceMatcher(
            None, wanted, _local(iri).casefold()
        ).ratio(),
        reverse=True,
    )
    if not scored:
        return None
    ratio = SequenceMatcher(None, wanted, _local(scored[0]).casefold()).ratio()
    return scored[0] if ratio >= 0.5 else None


def _unit_local_to_class(graph: Graph, quantity_iris: list[str]) -> dict[str, str]:
    unit_root = URIRef(OM2_NS + "Unit")
    unit_classes = _subclasses(graph, unit_root)
    class_map: dict[str, str] = {}
    for unit_class in unit_classes:
        local = _local(str(unit_class))
        stem = local[:-4] if local.endswith("Unit") else local
        matched = _match_quantity_class(stem, quantity_iris)
        if matched:
            class_map[str(unit_class)] = matched
    mapping: dict[str, str] = {}
    for subject, _, type_iri in graph.triples((None, RDF.type, None)):
        if not isinstance(subject, URIRef) or not isinstance(type_iri, URIRef):
            continue
        quantity_iri = class_map.get(str(type_iri))
        if quantity_iri:
            mapping[_local(str(subject))] = quantity_iri
    return mapping


def _qualitative_by_class(quantity_iris: list[str]) -> dict[str, dict[str, str]]:
    return {iri: {} for iri in quantity_iris}


def load_source_unit_aliases(alias_path: str | Path | None = None) -> dict[str, str]:
    """Load the prepared source-spelling → OM-2 individual table."""
    path = Path(alias_path) if alias_path is not None else _DEFAULT_ALIASES
    if not path.is_file():
        raise FileNotFoundError(f"OM-2 unit alias table is missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    aliases = payload.get("aliases") if isinstance(payload, dict) else None
    if not isinstance(aliases, dict) or not aliases:
        raise ValueError(f"OM-2 alias table {path} has no aliases object.")
    cleaned: dict[str, str] = {}
    for raw_alias, raw_target in aliases.items():
        alias = str(raw_alias or "").strip()
        target = str(raw_target or "").strip()
        if not alias or not target:
            raise ValueError(
                f"OM-2 alias table has an empty row: {raw_alias!r} -> {raw_target!r}"
            )
        if target.startswith(("http://", "https://")):
            target = _local(target)
        cleaned[alias] = target
    return cleaned


def _aliases_by_class(unit_local_to_class: dict[str, str]) -> dict[str, list[str]]:
    aliases: dict[str, list[str]] = {}
    for alias, unit_local in load_source_unit_aliases().items():
        class_iri = unit_local_to_class.get(unit_local)
        if not class_iri:
            continue
        aliases.setdefault(class_iri, []).append(str(alias))
    for class_iri, values in aliases.items():
        aliases[class_iri] = sorted(
            dict.fromkeys(values), key=lambda item: (len(item), item)
        )
    return aliases


def compile_om2_runtime_tables(
    *,
    om2_graph: Graph | None = None,
    context: Any | None = None,
    alias_path: str | Path | None = None,
) -> tuple[dict[str, URIRef], dict[URIRef, frozenset[URIRef]]]:
    """Build the maps inlined into generated ``_fixed_om2_runtime.py``."""
    graph = om2_graph if om2_graph is not None else load_om2_graph(context)
    quantity_iris = _quantity_class_iris(graph)
    if not quantity_iris:
        raise ValueError("OM-2 T-Box has no quantity classes.")
    unit_local_to_class = _unit_local_to_class(graph, quantity_iris)
    if not unit_local_to_class:
        raise ValueError("OM-2 T-Box has no typed unit individuals under om2:Unit.")
    aliases = load_source_unit_aliases(alias_path)
    missing = sorted(
        {target for target in aliases.values() if target not in unit_local_to_class}
    )
    if missing:
        raise ValueError(
            "OM-2 alias table names unit individuals that are not in the T-Box: "
            + ", ".join(missing)
        )
    unit_map = {alias: URIRef(OM2_NS + target) for alias, target in aliases.items()}
    quantity_classes = {
        URIRef(OM2_NS + local): frozenset({URIRef(class_iri)})
        for local, class_iri in unit_local_to_class.items()
    }
    return unit_map, quantity_classes


def _teaching_aliases(aliases: list[str]) -> list[str]:
    """Prefer multi-character aliases so instruction examples stay unambiguous."""
    plain = [str(alias) for alias in aliases if " " not in str(alias)]
    longer = [alias for alias in plain if len(alias) >= 2]
    return longer or plain


def _example_labels(
    class_iri: str,
    aliases: list[str],
    qualitative: Mapping[str, str],
) -> list[str]:
    labels: list[str] = []
    seen: set[str] = set()
    for alias in _teaching_aliases(aliases):
        text = f"{EXAMPLE_NUMBER} {alias}"
        if text not in seen:
            labels.append(text)
            seen.add(text)
        if len(labels) >= 2:
            break
    for value in dict.fromkeys(qualitative.values()):
        text = str(value).strip()
        if text and text not in seen:
            labels.append(text)
            seen.add(text)
        if len(labels) >= 4:
            break
    if not labels:
        labels.append(f"{EXAMPLE_NUMBER} <unit>")
    return labels


def _owner_compact_example(
    compiled: Mapping[str, Any],
    classes: Mapping[str, Mapping[str, Any]],
) -> str:
    """First richest owner-create call, from public_tools order."""
    best: tuple[int, int, str] | None = None
    for index, tool in enumerate(compiled.get("public_tools") or []):
        name = str(tool.get("name") or "")
        args: list[str] = []
        for item in tool.get("quantities") or []:
            facet = str(item.get("parameter") or "")
            range_iri = str(item.get("range_iri") or "")
            if not facet:
                continue
            examples = list(
                (classes.get(range_iri) or {}).get("example_labels")
                or [f"{EXAMPLE_NUMBER} <unit>"]
            )
            args.append(f"{facet}={examples[0]!r}")
        if not name or not args:
            continue
        candidate = (len(args), -index, f"{name}(..., {', '.join(args)})")
        if best is None or candidate[:2] > best[:2]:
            best = candidate
    if best:
        return best[2]
    return "create_*(..., <compiled_quantity_argument>='<number> <unit>')"


def _collect_facets(compiled: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    facets: dict[str, dict[str, Any]] = {}
    for tool in compiled.get("public_tools") or []:
        tool_name = str(tool.get("name") or "")
        for group, attach in (
            ("quantities", "owner"),
            ("parent_quantities", "bound_root"),
        ):
            for item in tool.get(group) or []:
                facet = str(item.get("parameter") or item.get("predicate_local") or "")
                if not facet:
                    continue
                current = facets.setdefault(
                    facet,
                    {
                        "facet": facet,
                        "predicate_local": str(item.get("predicate_local") or facet),
                        "predicate_iri": str(item.get("predicate_iri") or ""),
                        "range_iri": str(item.get("range_iri") or ""),
                        "attach_to": attach,
                        "owner_tools": [],
                    },
                )
                if tool_name and tool_name not in current["owner_tools"]:
                    current["owner_tools"].append(tool_name)
                if attach == "bound_root":
                    current["attach_to"] = "bound_root"
    return dict(sorted(facets.items()))


def compile_quantity_surface(
    compiled: Mapping[str, Any],
    *,
    om2_graph: Graph | None = None,
    context: Any | None = None,
) -> dict[str, Any]:
    graph = om2_graph if om2_graph is not None else load_om2_graph(context)
    quantity_iris = _quantity_class_iris(graph)
    unit_local_to_class = _unit_local_to_class(graph, quantity_iris)
    qualitative = _qualitative_by_class(quantity_iris)
    aliases = _aliases_by_class(unit_local_to_class)
    facets = _collect_facets(compiled)
    used_class_iris = sorted(
        {str(item["range_iri"]) for item in facets.values() if item.get("range_iri")}
    )
    classes: dict[str, dict[str, Any]] = {}
    for class_iri in used_class_iris or quantity_iris:
        class_aliases = aliases.get(class_iri, [])
        classes[class_iri] = {
            "class_iri": class_iri,
            "class_local": _local(class_iri),
            "sibling_class_locals": sorted(
                _local(other)
                for other in (used_class_iris or quantity_iris)
                if other != class_iri
            ),
            "allowed_unit_aliases": class_aliases[:8],
            "qualitative_labels": sorted(qualitative.get(class_iri, {})),
            "example_labels": _example_labels(
                class_iri, class_aliases, qualitative.get(class_iri, {})
            ),
        }
    example_calls: dict[str, str] = {}
    for facet, spec in facets.items():
        class_info = classes.get(str(spec.get("range_iri") or ""), {})
        examples = list(class_info.get("example_labels") or [f"{EXAMPLE_NUMBER} <unit>"])
        tool = (spec.get("owner_tools") or ["create_*"])[0]
        example_calls[facet] = f"{tool}(..., {facet}={examples[0]!r})"
    return {
        "schema_version": "om2-compact-lexeme-surface.v1",
        "example_number": EXAMPLE_NUMBER,
        "facets": facets,
        "classes": classes,
        "unit_local_to_class": unit_local_to_class,
        "source_unit_aliases": load_source_unit_aliases(),
        "example_calls": example_calls,
        "compact_example_call": _owner_compact_example(compiled, classes),
        "quantity_class_iris": quantity_iris,
    }


def facet_table_lines(surface: Mapping[str, Any]) -> list[str]:
    lines: list[str] = []
    for facet, spec in (surface.get("facets") or {}).items():
        range_iri = str(spec.get("range_iri") or "")
        local = _local(range_iri)
        attach = str(spec.get("attach_to") or "owner")
        host = "bound root" if attach == "bound_root" else "owning create_* occurrence"
        lines.append(f"{facet} -> {local} (attach to {host})")
    return lines


def class_example_lines(surface: Mapping[str, Any]) -> list[str]:
    """Full class dump for structured errors, not the instruction."""
    lines: list[str] = []
    for class_iri, spec in (surface.get("classes") or {}).items():
        examples = ", ".join(repr(item) for item in spec.get("example_labels") or [])
        qualitative = ", ".join(repr(item) for item in spec.get("qualitative_labels") or [])
        aliases = ", ".join(spec.get("allowed_unit_aliases") or [])
        extra = f" qualitative: {qualitative};" if qualitative else ""
        lines.append(
            f"{spec.get('class_local') or _local(class_iri)}: {examples};"
            f"{extra} aliases: {aliases}"
        )
    return lines


def class_short_example_lines(surface: Mapping[str, Any]) -> list[str]:
    """One numeric example per class, plus one qualitative if compiled."""
    lines: list[str] = []
    for class_iri, spec in (surface.get("classes") or {}).items():
        labels = [str(item) for item in spec.get("example_labels") or [] if str(item)]
        numeric = next((item for item in labels if item[:1].isdigit()), None)
        qualitative = next((item for item in labels if not item[:1].isdigit()), None)
        local = str(spec.get("class_local") or _local(class_iri))
        if numeric and qualitative:
            lines.append(f"{local}: {numeric!r}; also {qualitative!r}")
        elif numeric:
            lines.append(f"{local}: {numeric!r}")
        elif qualitative:
            lines.append(f"{local}: {qualitative!r}")
    return lines
