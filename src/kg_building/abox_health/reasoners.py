"""SHACL, OWL-RL, and optional OWL-DL reasoner gates."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from rdflib import Graph, URIRef
from rdflib.namespace import OWL, RDF, SH

from .report import Finding


def _owlready_iri(path: Path) -> str:
    """IRI owlready2 can open. Windows ``file:///D:`` is read as ``/D:``."""
    resolved = Path(path).resolve()
    if os.name == "nt":
        return "file://" + resolved.as_posix()
    return resolved.as_uri()


def _as_rdfxml(path: Path | None = None, graph: Graph | None = None) -> Path:
    """owlready2 loads RDF/XML reliably; published graphs here are Turtle."""
    data = Graph()
    if graph is not None:
        for triple in graph:
            data.add(triple)
    elif path is not None:
        data.parse(str(path), format="turtle")
    handle = tempfile.NamedTemporaryFile(suffix=".owl", delete=False)
    handle.close()
    dest = Path(handle.name)
    data.serialize(dest, format="xml")
    return dest


def check_shacl(
    abox: Graph,
    *,
    tbox: Graph,
    shacl_path: Path,
    om2_path: Path | None = None,
) -> list[Finding]:
    try:
        import pyshacl
    except ImportError:
        return [
            Finding(
                code="SHACL_SKIPPED",
                check="shacl",
                severity="warning",
                message="pyshacl is not installed; SHACL gate skipped.",
            )
        ]
    data = Graph()
    for triple in abox:
        data.add(triple)
    if om2_path is not None and om2_path.is_file():
        data.parse(str(om2_path), format="turtle")
    shacl_graph = Graph()
    shacl_graph.parse(str(shacl_path), format="turtle")
    conforms, results_graph, results_text = pyshacl.validate(
        data,
        shacl_graph=shacl_graph,
        ont_graph=tbox,
        inference="rdfs",
        abort_on_first=False,
        allow_infos=True,
        allow_warnings=True,
    )
    if conforms:
        return []
    findings: list[Finding] = []
    for result in results_graph.subjects(RDF.type, SH.ValidationResult):
        severity = results_graph.value(result, SH.resultSeverity)
        if severity == SH.Warning or severity == SH.Info:
            continue
        focus = results_graph.value(result, SH.focusNode)
        message = results_graph.value(result, SH.resultMessage)
        path = results_graph.value(result, SH.resultPath)
        iris = tuple(
            str(node)
            for node in (focus, path)
            if isinstance(node, URIRef)
        )
        findings.append(
            Finding(
                code="SHACL_VIOLATION",
                check="shacl",
                message=str(message or "SHACL constraint violation"),
                iris=iris,
            )
        )
    if not findings:
        snippet = " ".join(
            line.strip()
            for line in str(results_text).splitlines()
            if line.strip()
        )[:800]
        findings.append(
            Finding(
                code="SHACL_VIOLATION",
                check="shacl",
                message=snippet or "SHACL validation failed.",
            )
        )
    return findings


def check_owlrl(abox: Graph, *, tbox: Graph) -> list[Finding]:
    try:
        import owlrl
    except ImportError:
        return [
            Finding(
                code="OWLRL_SKIPPED",
                check="owlrl",
                severity="warning",
                message="owlrl is not installed; OWL-RL gate skipped.",
            )
        ]
    combined = Graph()
    for triple in tbox:
        combined.add(triple)
    for triple in abox:
        combined.add(triple)
    owlrl.DeductiveClosure(owlrl.OWLRL_Semantics).expand(combined)
    nothing = [
        str(subject)
        for subject in combined.subjects(RDF.type, OWL.Nothing)
        if isinstance(subject, URIRef)
    ]
    if not nothing:
        return []
    return [
        Finding(
            code="OWL_RL_NOTHING",
            check="owlrl",
            message="OWL-RL closure typed individuals as owl:Nothing.",
            iris=tuple(sorted(nothing)),
        )
    ]


def check_owl_dl(abox: Graph, *, tbox_paths: list[Path]) -> list[Finding]:
    try:
        from owlready2 import World, sync_reasoner
        from owlready2.base import OwlReadyInconsistentOntologyError
    except ImportError:
        return [
            Finding(
                code="OWL_DL_SKIPPED",
                check="owl_dl",
                severity="warning",
                message="owlready2 is not installed; OWL-DL reasoner skipped.",
            )
        ]
    world = World()
    tmp_files: list[Path] = []
    try:
        try:
            for path in tbox_paths:
                converted = _as_rdfxml(path=Path(path))
                tmp_files.append(converted)
                world.get_ontology(_owlready_iri(converted)).load()
            converted_abox = _as_rdfxml(graph=abox)
            tmp_files.append(converted_abox)
            world.get_ontology(_owlready_iri(converted_abox)).load()
            sync_reasoner(world, infer_property_values=False)
        except OwlReadyInconsistentOntologyError:
            iris: tuple[str, ...] = ()
            try:
                iris = tuple(str(cls) for cls in world.inconsistent_classes())
            except Exception:
                pass
            return [
                Finding(
                    code="OWL_DL_INCONSISTENT",
                    check="owl_dl",
                    message="OWL-DL reasoner found the ontology inconsistent.",
                    iris=iris,
                )
            ]
        except Exception as exc:
            detail = str(exc).strip() or type(exc).__name__
            return [
                Finding(
                    code="OWL_DL_SKIPPED",
                    check="owl_dl",
                    severity="warning",
                    message=f"OWL-DL reasoner did not run ({detail}).",
                )
            ]
    finally:
        for item in tmp_files:
            item.unlink(missing_ok=True)
    inconsistent = [str(cls) for cls in world.inconsistent_classes()]
    if not inconsistent:
        return []
    return [
        Finding(
            code="OWL_DL_INCONSISTENT",
            check="owl_dl",
            message="OWL-DL reasoner found inconsistent classes.",
            iris=tuple(inconsistent),
        )
    ]
