"""Orchestrate A-Box connectivity and semantic-health gates."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Sequence

from rdflib import Graph

from .connectivity import check_connectivity
from .constraints import (
    check_disjoint_types,
    check_domain_range,
    check_functional_properties,
    check_om2_quantities,
    check_ordered_members,
    check_owl_restrictions,
)
from .graph_index import (
    infer_root_iris,
    instance_graph,
    load_identity_root,
    load_tbox,
    load_ttl,
    subclass_superclasses,
    typed_instance_nodes,
    vocabulary_iris,
)
from .reasoners import check_owl_dl, check_owlrl, check_shacl
from .report import Finding, HealthReport

_HERE = Path(__file__).resolve()
_REPO_ROOT = _HERE.parents[3]
_OX_RESOURCES = _HERE.parents[1] / "ontologx" / "resources"

_SHACL_BY_TBOX_STEM = {
    "ontosynthesis": _OX_RESOURCES / "ontosynthesis_shacl.ttl",
    "ontomops-subgraph": _OX_RESOURCES / "ontomops_shacl.ttl",
    "ontospecies-subgraph": _OX_RESOURCES / "ontospecies_shacl.ttl",
    "medical": _OX_RESOURCES / "medical_shacl.ttl",
    "medical_case_schema_de_non_flat_v4": _OX_RESOURCES / "medical_shacl.ttl",
    "medical_case_schema_de_non_flat_v3": _OX_RESOURCES / "medical_shacl.ttl",
}

RUN_TTL_GLOBS = (
    "**/ontosynthesis_output/*.ttl",
    "**/ontomops_output/*.ttl",
    "**/ontospecies_output/*.ttl",
    "**/medical_output/*.ttl",
    "**/merged/*/*.ttl",
)


def default_shacl_for_tbox(tbox_path: Path) -> Path | None:
    stem = Path(tbox_path).stem.lower()
    path = _SHACL_BY_TBOX_STEM.get(stem)
    if path is None and "medical" in stem:
        path = _OX_RESOURCES / "medical_shacl.ttl"
    if path is not None and path.is_file():
        return path
    return None


def tboxes_without_om2(tbox_paths: Sequence[Path]) -> list[Path]:
    """Drop OM-2 catalogs so OWL-DL/HermiT does not load the unit ontology."""
    skip = {"om2.ttl", "om2_mock.ttl"}
    return [Path(path) for path in tbox_paths if Path(path).name.lower() not in skip]


def resolve_ontology_tboxes(ontology: str) -> tuple[list[Path], Path | None]:
    from src.extraction_prompt_generation.config.domain_config import (
        load_domain_generation_config,
    )
    from src.extraction_prompt_generation.paths import resolve_domain_config_path

    config = load_domain_generation_config(
        resolve_domain_config_path(ontology_name=str(ontology).strip()),
        repository_root=_REPO_ROOT,
    )
    tboxes = [config.primary_tbox, *config.supporting_tboxes]
    return tboxes, default_shacl_for_tbox(config.primary_tbox)


def _om2_path(tbox_paths: Sequence[Path]) -> Path | None:
    for path in tbox_paths:
        if Path(path).name.lower() in {"om2.ttl", "om2_mock.ttl"}:
            return Path(path)
        sibling = Path(path).with_name("om2.ttl")
        if sibling.is_file():
            return sibling
    default = _REPO_ROOT / "data" / "ontologies" / "om2.ttl"
    return default if default.is_file() else None


def _skip_note(report: HealthReport, findings: list[Finding]) -> list[Finding]:
    kept: list[Finding] = []
    for finding in findings:
        if finding.severity != "error" and finding.code.endswith("_SKIPPED"):
            report.skipped_checks.append(finding.check or finding.code)
            report.findings.append(finding)
            continue
        kept.append(finding)
    return kept


def check_abox(
    graph: Graph,
    *,
    tbox_paths: Sequence[Path],
    shacl_path: Path | None = None,
    roots: Sequence[str] | None = None,
    source: str = "",
    run_shacl: bool = True,
    run_owlrl: bool = True,
    run_owl_dl: bool = True,
) -> HealthReport:
    """Run connectivity + T-Box + SHACL/OWL gates on one instance graph."""
    tbox = load_tbox(tbox_paths)
    primary = Path(tbox_paths[0]) if tbox_paths else None
    primary_tbox = load_tbox([primary]) if primary is not None else tbox
    vocab = vocabulary_iris(tbox)
    abox = instance_graph(graph)
    closure = subclass_superclasses(tbox)
    primary_closure = subclass_superclasses(primary_tbox)
    instances = typed_instance_nodes(abox, vocab)
    report = HealthReport(ok=True, source=source, instance_count=len(instances))

    resolved_roots = [str(item).strip() for item in (roots or []) if str(item).strip()]
    if not resolved_roots:
        resolved_roots = infer_root_iris(
            abox, tbox=primary_tbox, vocab=vocab, closure=primary_closure
        )
    report.roots = list(resolved_roots)

    for finding in check_connectivity(abox, roots=resolved_roots, vocab=vocab):
        report.add(finding)

    for finding in check_domain_range(
        abox, tbox=tbox, vocab=vocab, closure=closure
    ):
        report.add(finding)
    for finding in check_owl_restrictions(
        abox, tbox=tbox, vocab=vocab, closure=closure
    ):
        report.add(finding)
    for finding in check_disjoint_types(
        abox, tbox=tbox, vocab=vocab, closure=closure
    ):
        report.add(finding)
    for finding in check_functional_properties(abox, tbox=tbox, vocab=vocab):
        report.add(finding)
    primary = Path(tbox_paths[0]) if tbox_paths else None
    if primary is not None:
        for finding in check_ordered_members(
            abox,
            tbox_path=primary,
            vocab=vocab,
            closure=closure,
            tbox=tbox,
        ):
            report.add(finding)
    for finding in check_om2_quantities(
        abox, tbox=tbox, vocab=vocab, closure=closure
    ):
        report.add(finding)

    if run_shacl:
        resolved_shacl = Path(shacl_path) if shacl_path else (
            default_shacl_for_tbox(primary) if primary is not None else None
        )
        if resolved_shacl is None:
            report.skipped_checks.append("shacl")
            report.findings.append(
                Finding(
                    code="SHACL_SKIPPED",
                    check="shacl",
                    severity="warning",
                    message="No SHACL shapes were supplied or inferred from the T-Box stem.",
                )
            )
        else:
            for finding in _skip_note(
                report,
                check_shacl(
                    abox,
                    tbox=tbox,
                    shacl_path=resolved_shacl,
                    om2_path=_om2_path(tbox_paths),
                ),
            ):
                report.add(finding)
    else:
        report.skipped_checks.append("shacl")

    if run_owlrl:
        for finding in _skip_note(report, check_owlrl(abox, tbox=tbox)):
            report.add(finding)
    else:
        report.skipped_checks.append("owlrl")

    if run_owl_dl:
        for finding in _skip_note(
            report, check_owl_dl(abox, tbox_paths=list(tbox_paths))
        ):
            report.add(finding)
    else:
        report.skipped_checks.append("owl_dl")

    return report


def check_ttl_file(
    ttl_path: Path,
    *,
    tbox_paths: Sequence[Path],
    shacl_path: Path | None = None,
    roots: Sequence[str] | None = None,
    run_shacl: bool = True,
    run_owlrl: bool = True,
    run_owl_dl: bool = True,
) -> HealthReport:
    path = Path(ttl_path)
    graph = load_ttl(path)
    resolved_roots = [str(item).strip() for item in (roots or []) if str(item).strip()]
    identity_root = load_identity_root(path)
    if identity_root and identity_root not in resolved_roots:
        resolved_roots = [identity_root, *resolved_roots]
    return check_abox(
        graph,
        tbox_paths=tbox_paths,
        shacl_path=shacl_path,
        roots=resolved_roots,
        source=str(path),
        run_shacl=run_shacl,
        run_owlrl=run_owlrl,
        run_owl_dl=run_owl_dl,
    )


def discover_run_ttl(run_dir: Path) -> list[Path]:
    root = Path(run_dir)
    found: list[Path] = []
    seen: set[Path] = set()
    for pattern in RUN_TTL_GLOBS:
        for path in sorted(root.glob(pattern)):
            resolved = path.resolve()
            if resolved in seen or not path.is_file():
                continue
            if path.name.casefold() == "link.ttl":
                continue
            seen.add(resolved)
            found.append(path)
    return found


def check_ttl_files(
    ttl_paths: Iterable[Path],
    *,
    tbox_paths: Sequence[Path],
    shacl_path: Path | None = None,
    roots: Sequence[str] | None = None,
    run_shacl: bool = True,
    run_owlrl: bool = True,
    run_owl_dl: bool = True,
) -> list[HealthReport]:
    return [
        check_ttl_file(
            path,
            tbox_paths=tbox_paths,
            shacl_path=shacl_path,
            roots=roots,
            run_shacl=run_shacl,
            run_owlrl=run_owlrl,
            run_owl_dl=run_owl_dl,
        )
        for path in ttl_paths
    ]
