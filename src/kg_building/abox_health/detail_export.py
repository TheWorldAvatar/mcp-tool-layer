"""Export per-triple A-Box health detail (missing / extra / orphans) and zip it."""

from __future__ import annotations

import argparse
import csv
import json
import zipfile
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterable

from rdflib import Literal, URIRef
from rdflib.namespace import OWL, RDF, RDFS

from .campaign import (
    MOPS_RUNS,
    discover_pack_ttl,
    locked_packs,
    missing_cells,
    paper_hash,
)
from .check import check_ttl_file, resolve_ontology_tboxes
from .connectivity import _bfs, _neighbors, _undirected_edges
from .graph_index import (
    instance_graph,
    is_vocabulary_iri,
    load_tbox,
    load_ttl,
    typed_instance_nodes,
    vocabulary_iris,
)

_WORKER_CACHE: dict[str, tuple[list[Path], Path | None]] = {}

CONN_CODES = {
    "DIRECTED_UNREACHABLE",
    "WEAKLY_DISCONNECTED",
    "ISOLATED_TYPED_NODE",
    "INCOMING_ONLY",
    "DANGLING_IRI",
    "DANGLING_OBJECT_IRI",
    "BOUND_ROOT_MISSING",
    "BOUND_ROOT_NOT_MATERIALIZED",
}

DEFINITIONS = """\
A-Box health detail dump
========================

Scope
-----
Locked 30-case Pipeline / OntoLogX packs (s1-s4 x three modes x extension
where a 30-pack exists) plus OntoMed generic-strict 30-case packs.
OWL-RL and OWL-DL are skipped. SHACL uses rdfs inference.

What "missing" means
--------------------
A required fact is absent relative to the T-Box or SHACL:
- OWL minCardinality / someValuesFrom (OWL_RESTRICTION with min/lacks)
- SHACL sh:minCount ("Less than N values")
- OM-2 quantity missing numerical value or unit

What "extra" means
------------------
A fact is present that the T-Box/SHACL forbids, or that prune would drop:
- extra_orphan: typed instance not reachable from the bound top entity
  by outgoing edges (Pipeline export prune set), including isolated
  islands (typical extension Element/WeightPercentage; OX OM-2 Duration)
- extra_cardinality: OWL/SHACL maxCount or functional-property overflow
- extra_predicate: A-Box predicate not declared as object/datatype
  property in the loaded T-Box (excluding rdf/rdfs/owl)

What "mismatch" means
---------------------
The triple is present but typed/shaped wrong:
- DOMAIN_VIOLATION / RANGE_VIOLATION / DISJOINT_TYPES
- SHACL class / SPARQL (Cooling/Filter) / datatype
- OWL allValuesFrom

Orphan triple counting
----------------------
orphan_nodes: typed instances not in the directed BFS from bound roots
orphan_subject_triples: A-Box triples whose subject is an orphan node
  (this is the subgraph Pipeline prune would delete)
orphan_bridge_triples: triples from a reachable instance to an orphan
orphan_internal_triples: triples whose subject and object are both orphans
isolated_nodes: typed instances with no instance edges at all

Paper vs file
-------------
Mops packs: one merged TTL per paper (30 files = 30 papers).
OntoMed: one TTL per extracted case; a paper is healthy iff every case is.
"""


def _local(iri: str) -> str:
    text = str(iri)
    if "#" in text:
        return text.rsplit("#", 1)[-1]
    return text.rsplit("/", 1)[-1]


def _tboxes(ontology: str) -> tuple[list[Path], Path | None]:
    cached = _WORKER_CACHE.get(ontology)
    if cached is None:
        cached = resolve_ontology_tboxes(ontology)
        _WORKER_CACHE[ontology] = cached
    return cached


def classify_finding(code: str, message: str) -> str:
    msg = (message or "").casefold()
    if code in CONN_CODES:
        return "extra_orphan"
    if code in {"DOMAIN_VIOLATION", "RANGE_VIOLATION", "DISJOINT_TYPES"}:
        return "mismatch"
    if code in {"FUNCTIONAL_PROPERTY", "INVERSE_FUNCTIONAL_PROPERTY"}:
        return "extra_cardinality"
    if code == "OM2_QUANTITY_INCOMPLETE":
        return "missing"
    if code in {"ORDER_INVALID", "ORDER_DUPLICATE"}:
        return "mismatch"
    if code == "OWL_RESTRICTION":
        if "lacks" in msg or "somevaluesfrom" in msg or "(min " in msg:
            return "missing"
        if "(max " in msg or "exactly " in msg:
            return "extra_cardinality"
        if "allvaluesfrom" in msg:
            return "mismatch"
        return "missing"
    if code == "SHACL_VIOLATION":
        if "mincount" in msg or "less than" in msg or "at least" in msg:
            return "missing"
        if "maxcount" in msg or "more than" in msg:
            return "extra_cardinality"
        return "mismatch"
    return "other"


def inventory_graph(ttl_path: Path, tbox_paths: list[Path], roots: list[str]) -> dict[str, Any]:
    raw = load_ttl(ttl_path)
    abox = instance_graph(raw)
    tbox = load_tbox(tbox_paths)
    vocab = vocabulary_iris(tbox)
    instances = typed_instance_nodes(abox, vocab)
    object_props = {str(item) for item in tbox.subjects(RDF.type, OWL.ObjectProperty)}
    datatype_props = {str(item) for item in tbox.subjects(RDF.type, OWL.DatatypeProperty)}
    known_pred = object_props | datatype_props | {str(RDF.type), str(RDFS.label), str(RDFS.comment)}

    extra_pred: Counter[str] = Counter()
    for _, predicate, _ in abox:
        if not isinstance(predicate, URIRef):
            continue
        iri = str(predicate)
        if iri in known_pred or is_vocabulary_iri(iri, vocab):
            continue
        extra_pred[iri] += 1

    directed = _neighbors(abox, instances)
    undirected = _undirected_edges(directed)
    root_nodes = [URIRef(item) for item in roots if str(item).strip()]
    present_roots = [node for node in root_nodes if node in instances]
    directed_reachable = _bfs(present_roots, directed) if present_roots else set()
    weak_reachable = _bfs(present_roots, undirected) if present_roots else set()

    isolated = {
        node
        for node in instances
        if node not in present_roots and not directed.get(node) and not undirected.get(node)
    }
    weakly_disconnected = {
        node for node in instances if node not in weak_reachable and node not in isolated
    }
    incoming_only = {
        node
        for node in instances
        if node in weak_reachable and node not in directed_reachable
    }
    orphan_nodes = {node for node in instances if node not in directed_reachable}

    orphan_subject = 0
    orphan_bridge = 0
    orphan_internal = 0
    reachable_triples = 0
    for subject, _, obj in abox:
        subj_orphan = isinstance(subject, URIRef) and subject in orphan_nodes
        obj_orphan = isinstance(obj, URIRef) and obj in orphan_nodes
        subj_reach = isinstance(subject, URIRef) and subject in directed_reachable
        if subj_orphan:
            orphan_subject += 1
            if obj_orphan:
                orphan_internal += 1
        elif subj_reach:
            reachable_triples += 1
            if obj_orphan:
                orphan_bridge += 1

    nodes: list[dict[str, Any]] = []
    for node in sorted(orphan_nodes, key=str):
        types = [
            _local(str(item))
            for item in abox.objects(node, RDF.type)
            if isinstance(item, URIRef)
        ]
        labels = [str(item) for item in abox.objects(node, RDFS.label)]
        outgoing = sum(1 for _ in abox.predicate_objects(node))
        incoming = sum(1 for _ in abox.subject_predicates(node))
        kind = "directed_unreachable"
        if node in isolated:
            kind = "isolated"
        elif node in weakly_disconnected:
            kind = "weakly_disconnected"
        elif node in incoming_only:
            kind = "incoming_only"
        nodes.append(
            {
                "iri": str(node),
                "kind": kind,
                "types": types,
                "labels": labels[:5],
                "outgoing_triples": outgoing,
                "incoming_triples": incoming,
            }
        )

    type_counts: Counter[str] = Counter()
    for node in nodes:
        for type_name in node["types"] or ["(untyped)"]:
            type_counts[type_name] += 1

    return {
        "raw_triples": len(raw),
        "abox_triples": len(abox),
        "instance_nodes": len(instances),
        "reachable_nodes": len(directed_reachable),
        "orphan_nodes": len(orphan_nodes),
        "isolated_nodes": len(isolated),
        "weakly_disconnected_nodes": len(weakly_disconnected),
        "incoming_only_nodes": len(incoming_only),
        "orphan_subject_triples": orphan_subject,
        "orphan_internal_triples": orphan_internal,
        "orphan_bridge_triples": orphan_bridge,
        "reachable_triples": reachable_triples,
        "extra_predicates": dict(extra_pred),
        "orphan_type_counts": dict(type_counts),
        "orphan_nodes_detail": nodes,
    }


def detail_one(ttl: str, ontology: str, pack_id: str) -> dict[str, Any]:
    path = Path(ttl)
    tboxes, shacl = _tboxes(ontology)
    report = check_ttl_file(
        path,
        tbox_paths=tboxes,
        shacl_path=shacl,
        run_shacl=True,
        run_owlrl=False,
        run_owl_dl=False,
    )
    stats = inventory_graph(path, tboxes, report.roots)
    findings: list[dict[str, Any]] = []
    class_counts: Counter[str] = Counter()
    code_counts: Counter[str] = Counter()
    missing_by_path: Counter[str] = Counter()
    extra_card_by_path: Counter[str] = Counter()
    for finding in report.errors():
        bucket = classify_finding(finding.code, finding.message)
        class_counts[bucket] += 1
        code_counts[finding.code] += 1
        path_local = ""
        if len(finding.iris) >= 2:
            path_local = _local(finding.iris[1])
        elif finding.iris:
            path_local = _local(finding.iris[0])
        if bucket == "missing" and path_local:
            missing_by_path[path_local] += 1
        if bucket == "extra_cardinality" and path_local:
            extra_card_by_path[path_local] += 1
        findings.append(
            {
                "pack_id": pack_id,
                "hash": paper_hash(path),
                "file": path.name,
                "source": str(path),
                "code": finding.code,
                "bucket": bucket,
                "check": finding.check,
                "message": finding.message,
                "iris": list(finding.iris),
                "path": path_local,
            }
        )
    paper = {
        "pack_id": pack_id,
        "ontology": ontology,
        "file": path.name,
        "hash": paper_hash(path),
        "source": str(path),
        "ok": report.ok,
        "roots": list(report.roots),
        "root_count": len(report.roots),
        "error_count": len(report.errors()),
        "error_codes": dict(code_counts),
        "missing_findings": class_counts.get("missing", 0),
        "extra_orphan_findings": class_counts.get("extra_orphan", 0),
        "extra_cardinality_findings": class_counts.get("extra_cardinality", 0),
        "mismatch_findings": class_counts.get("mismatch", 0),
        "other_findings": class_counts.get("other", 0),
        "missing_by_path": dict(missing_by_path),
        "extra_cardinality_by_path": dict(extra_card_by_path),
        **{
            key: stats[key]
            for key in (
                "raw_triples",
                "abox_triples",
                "instance_nodes",
                "reachable_nodes",
                "orphan_nodes",
                "isolated_nodes",
                "weakly_disconnected_nodes",
                "incoming_only_nodes",
                "orphan_subject_triples",
                "orphan_internal_triples",
                "orphan_bridge_triples",
                "reachable_triples",
            )
        },
        "extra_predicate_count": sum(stats["extra_predicates"].values()),
        "extra_predicates": stats["extra_predicates"],
        "orphan_type_counts": stats["orphan_type_counts"],
    }
    orphans = [
        {
            "pack_id": pack_id,
            "hash": paper["hash"],
            "file": paper["file"],
            "source": paper["source"],
            **node,
        }
        for node in stats["orphan_nodes_detail"]
    ]
    extras = [
        {
            "pack_id": pack_id,
            "hash": paper["hash"],
            "file": paper["file"],
            "predicate": pred,
            "predicate_local": _local(pred),
            "triple_count": count,
        }
        for pred, count in sorted(stats["extra_predicates"].items())
    ]
    return {"paper": paper, "findings": findings, "orphans": orphans, "extras": extras}


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            payload = {}
            for key in fieldnames:
                value = row.get(key, "")
                if isinstance(value, (dict, list)):
                    payload[key] = json.dumps(value, ensure_ascii=False)
                else:
                    payload[key] = value
            writer.writerow(payload)


def _append_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _pack_rollup(pack: dict[str, Any], papers: list[dict[str, Any]]) -> dict[str, Any]:
    by_hash: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for paper in papers:
        by_hash[paper["hash"]].append(paper)
    papers_ok = sum(1 for group in by_hash.values() if all(item["ok"] for item in group))
    type_counts: Counter[str] = Counter()
    missing_path: Counter[str] = Counter()
    extra_card_path: Counter[str] = Counter()
    extra_pred: Counter[str] = Counter()
    for paper in papers:
        for type_name, count in (paper.get("orphan_type_counts") or {}).items():
            type_counts[type_name] += count
        for path, count in (paper.get("missing_by_path") or {}).items():
            missing_path[path] += count
        for path, count in (paper.get("extra_cardinality_by_path") or {}).items():
            extra_card_path[path] += count
        for pred, count in (paper.get("extra_predicates") or {}).items():
            extra_pred[_local(pred)] += count
    orphan_papers = sorted(
        {paper["hash"] for paper in papers if paper.get("orphan_nodes", 0) > 0}
    )
    return {
        "id": pack["id"],
        "seed": pack["seed"],
        "mode": pack["mode"],
        "builder": pack["builder"],
        "kind": pack["kind"],
        "ontology": pack["ontology"],
        "note": pack.get("note") or "",
        "kg_model": pack.get("kg_model") or "",
        "files": len(papers),
        "unique_papers": len(by_hash),
        "files_ok": sum(1 for item in papers if item["ok"]),
        "papers_ok": papers_ok,
        "papers_fail": len(by_hash) - papers_ok,
        "raw_triples": sum(item["raw_triples"] for item in papers),
        "abox_triples": sum(item["abox_triples"] for item in papers),
        "instance_nodes": sum(item["instance_nodes"] for item in papers),
        "reachable_nodes": sum(item["reachable_nodes"] for item in papers),
        "orphan_nodes": sum(item["orphan_nodes"] for item in papers),
        "isolated_nodes": sum(item["isolated_nodes"] for item in papers),
        "weakly_disconnected_nodes": sum(item["weakly_disconnected_nodes"] for item in papers),
        "incoming_only_nodes": sum(item["incoming_only_nodes"] for item in papers),
        "orphan_subject_triples": sum(item["orphan_subject_triples"] for item in papers),
        "orphan_internal_triples": sum(item["orphan_internal_triples"] for item in papers),
        "orphan_bridge_triples": sum(item["orphan_bridge_triples"] for item in papers),
        "reachable_triples": sum(item["reachable_triples"] for item in papers),
        "missing_findings": sum(item["missing_findings"] for item in papers),
        "extra_orphan_findings": sum(item["extra_orphan_findings"] for item in papers),
        "extra_cardinality_findings": sum(item["extra_cardinality_findings"] for item in papers),
        "mismatch_findings": sum(item["mismatch_findings"] for item in papers),
        "extra_predicate_triples": sum(item["extra_predicate_count"] for item in papers),
        "orphan_papers": len(orphan_papers),
        "orphan_hashes": orphan_papers,
        "orphan_types": dict(type_counts),
        "missing_by_path": dict(missing_path),
        "extra_cardinality_by_path": dict(extra_card_path),
        "extra_predicates": dict(extra_pred),
    }


def write_readme(path: Path, rollups: list[dict[str, Any]]) -> None:
    lines = [
        DEFINITIONS.strip(),
        "",
        "Pack headline",
        "-------------",
        "",
        f"{'pack':<52} {'ok':>8} {'orph_n':>7} {'orph_t':>7} {'miss':>6} {'misma':>6} {'xcard':>6}",
    ]
    for row in rollups:
        ok = f"{row['papers_ok']}/{row['unique_papers']}"
        lines.append(
            f"{row['id']:<52} {ok:>8} {row['orphan_nodes']:>7} "
            f"{row['orphan_subject_triples']:>7} {row['missing_findings']:>6} "
            f"{row['mismatch_findings']:>6} {row['extra_cardinality_findings']:>6}"
        )
    lines.extend(
        [
            "",
            "Files in this dump",
            "------------------",
            "DEFINITIONS.txt           this text",
            "missing_packs.json        locked 30-cells with no published graph",
            "pack_summary.csv          one row per pack (counts)",
            "pack_summary.json         same plus orphan type / missing-path histograms",
            "paper_summary.csv         one row per TTL",
            "findings.jsonl            every SHACL/TBox/connectivity error",
            "orphan_nodes.jsonl        every unreachable typed instance",
            "extra_predicates.jsonl    undeclared predicates (usually empty)",
            "source_campaign.json      earlier headline campaign JSON",
            "",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def zip_dir(folder: Path, zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for file_path in sorted(folder.rglob("*")):
            if file_path.is_file():
                archive.write(file_path, arcname=file_path.relative_to(folder.parent))


def run_detail(*, jobs: int, out_dir: Path, zip_path: Path) -> None:
    if out_dir.exists():
        for child in out_dir.glob("*"):
            if child.is_file():
                child.unlink()
    out_dir.mkdir(parents=True, exist_ok=True)
    campaign_src = MOPS_RUNS / "_abox_health_campaign.json"
    if campaign_src.is_file():
        (out_dir / "source_campaign.json").write_bytes(campaign_src.read_bytes())
    (out_dir / "missing_packs.json").write_text(
        json.dumps(missing_cells(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (out_dir / "DEFINITIONS.txt").write_text(DEFINITIONS, encoding="utf-8")

    findings_path = out_dir / "findings.jsonl"
    orphans_path = out_dir / "orphan_nodes.jsonl"
    extras_path = out_dir / "extra_predicates.jsonl"
    for path in (findings_path, orphans_path, extras_path):
        path.write_text("", encoding="utf-8")

    packs = locked_packs()
    all_papers: list[dict[str, Any]] = []
    rollups: list[dict[str, Any]] = []
    for index, pack in enumerate(packs, start=1):
        ttl_paths = discover_pack_ttl(pack)
        print(f"[{index}/{len(packs)}] {pack['id']} files={len(ttl_paths)}", flush=True)
        papers: list[dict[str, Any]] = []
        if not ttl_paths:
            rollups.append(
                {
                    **{
                        key: pack.get(key, "")
                        for key in ("id", "seed", "mode", "builder", "kind", "ontology", "note")
                    },
                    "files": 0,
                    "unique_papers": 0,
                    "files_ok": 0,
                    "papers_ok": 0,
                    "papers_fail": 0,
                    "raw_triples": 0,
                    "abox_triples": 0,
                    "instance_nodes": 0,
                    "reachable_nodes": 0,
                    "orphan_nodes": 0,
                    "isolated_nodes": 0,
                    "weakly_disconnected_nodes": 0,
                    "incoming_only_nodes": 0,
                    "orphan_subject_triples": 0,
                    "orphan_internal_triples": 0,
                    "orphan_bridge_triples": 0,
                    "reachable_triples": 0,
                    "missing_findings": 0,
                    "extra_orphan_findings": 0,
                    "extra_cardinality_findings": 0,
                    "mismatch_findings": 0,
                    "extra_predicate_triples": 0,
                    "orphan_papers": 0,
                    "orphan_hashes": [],
                    "orphan_types": {},
                    "missing_by_path": {},
                    "extra_cardinality_by_path": {},
                    "extra_predicates": {},
                }
            )
            continue
        if jobs <= 1:
            results = [detail_one(str(path), pack["ontology"], pack["id"]) for path in ttl_paths]
        else:
            results = []
            with ProcessPoolExecutor(max_workers=jobs) as pool:
                futures = [
                    pool.submit(detail_one, str(path), pack["ontology"], pack["id"])
                    for path in ttl_paths
                ]
                for future in as_completed(futures):
                    results.append(future.result())
        results.sort(key=lambda item: (item["paper"]["hash"], item["paper"]["file"]))
        for item in results:
            papers.append(item["paper"])
            _append_jsonl(findings_path, item["findings"])
            _append_jsonl(orphans_path, item["orphans"])
            _append_jsonl(extras_path, item["extras"])
        rollup = _pack_rollup(pack, papers)
        rollups.append(rollup)
        all_papers.extend(papers)
        print(
            f"    papers_ok={rollup['papers_ok']}/{rollup['unique_papers']} "
            f"orphan_nodes={rollup['orphan_nodes']} "
            f"orphan_triples={rollup['orphan_subject_triples']} "
            f"missing={rollup['missing_findings']} mismatch={rollup['mismatch_findings']}",
            flush=True,
        )

    pack_fields = [
        "id",
        "seed",
        "mode",
        "builder",
        "kind",
        "ontology",
        "files",
        "unique_papers",
        "files_ok",
        "papers_ok",
        "papers_fail",
        "raw_triples",
        "abox_triples",
        "instance_nodes",
        "reachable_nodes",
        "orphan_nodes",
        "isolated_nodes",
        "weakly_disconnected_nodes",
        "incoming_only_nodes",
        "orphan_subject_triples",
        "orphan_internal_triples",
        "orphan_bridge_triples",
        "reachable_triples",
        "missing_findings",
        "extra_orphan_findings",
        "extra_cardinality_findings",
        "mismatch_findings",
        "extra_predicate_triples",
        "orphan_papers",
        "orphan_hashes",
        "orphan_types",
        "missing_by_path",
        "extra_cardinality_by_path",
        "note",
        "kg_model",
    ]
    _write_csv(out_dir / "pack_summary.csv", rollups, pack_fields)
    (out_dir / "pack_summary.json").write_text(
        json.dumps(rollups, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    paper_fields = [
        "pack_id",
        "hash",
        "file",
        "ok",
        "raw_triples",
        "abox_triples",
        "instance_nodes",
        "reachable_nodes",
        "orphan_nodes",
        "isolated_nodes",
        "weakly_disconnected_nodes",
        "incoming_only_nodes",
        "orphan_subject_triples",
        "orphan_internal_triples",
        "orphan_bridge_triples",
        "reachable_triples",
        "missing_findings",
        "extra_cardinality_findings",
        "mismatch_findings",
        "extra_predicate_count",
        "orphan_type_counts",
        "missing_by_path",
        "error_codes",
        "source",
    ]
    _write_csv(out_dir / "paper_summary.csv", all_papers, paper_fields)
    write_readme(out_dir / "README.txt", rollups)
    zip_dir(out_dir, zip_path)
    print(f"Wrote {out_dir}", flush=True)
    print(f"Wrote {zip_path}", flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=MOPS_RUNS / "_abox_health_detail",
    )
    parser.add_argument(
        "--zip",
        type=Path,
        default=MOPS_RUNS / "_abox_health_campaign_detail.zip",
    )
    parser.add_argument("--jobs", type=int, default=6)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    run_detail(jobs=max(1, int(args.jobs)), out_dir=Path(args.out_dir), zip_path=Path(args.zip))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
