"""Run A-Box health over locked 30-case Pipeline/OX packs (no OWL-RL/DL)."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterable

from .check import check_ttl_file, resolve_ontology_tboxes, tboxes_without_om2

_HERE = Path(__file__).resolve()
_REPO_ROOT = _HERE.parents[3]
MOPS_RUNS = _REPO_ROOT / "scenarios" / "mops" / "runs"
MED_RUNS = _REPO_ROOT / "scenarios" / "medical" / "runs"

CONN_CODES = {
    "DIRECTED_UNREACHABLE",
    "WEAKLY_DISCONNECTED",
    "ISOLATED_TYPED_NODE",
    "INCOMING_ONLY",
    "DANGLING_IRI",
    "BOUND_ROOT_MISSING",
    "BOUND_ROOT_NOT_MATERIALIZED",
}
SHACL_CODES = {"SHACL_VIOLATION"}
TBOX_CODES = {
    "DOMAIN_VIOLATION",
    "RANGE_VIOLATION",
    "OWL_RESTRICTION",
    "DISJOINT_TYPES",
    "FUNCTIONAL_VIOLATION",
    "OM2_SHAPE",
    "OM2_QUANTITY_INCOMPLETE",
    "ORDERED_MEMBERS",
}

_WORKER_CACHE: dict[str, tuple[list[Path], Path | None]] = {}


def _pack(
    pack_id: str,
    *,
    seed: str,
    mode: str,
    builder: str,
    kind: str,
    ontology: str,
    paths: list[Path] | None = None,
    shards: str | None = None,
    note: str = "",
    kg_model: str = "",
) -> dict[str, Any]:
    return {
        "id": pack_id,
        "seed": seed,
        "mode": mode,
        "builder": builder,
        "kind": kind,
        "ontology": ontology,
        "paths": paths or [],
        "shards": shards,
        "note": note,
        "kg_model": kg_model,
    }


def locked_packs() -> list[dict[str, Any]]:
    m = MOPS_RUNS
    med = MED_RUNS
    return [
        _pack(
            "s1-generic-strict-pipeline",
            seed="s1",
            mode="generic-strict",
            builder="pipeline",
            kind="main",
            ontology="ontosynthesis",
            paths=[m / "20260908_220850_s1p30"],
        ),
        _pack(
            "s2-generic-strict-pipeline",
            seed="s2",
            mode="generic-strict",
            builder="pipeline",
            kind="main",
            ontology="ontosynthesis",
            paths=[m / "20260908_221215_s2p30"],
        ),
        _pack(
            "s3-generic-strict-pipeline",
            seed="s3",
            mode="generic-strict",
            builder="pipeline",
            kind="main",
            ontology="ontosynthesis",
            paths=[m / "20260908_232520_s3p30"],
        ),
        _pack(
            "s1-generic-strict-ox",
            seed="s1",
            mode="generic-strict",
            builder="ox",
            kind="main",
            ontology="ontosynthesis",
            paths=[m / "20260908_223401_s1x30"],
        ),
        _pack(
            "s2-generic-strict-ox",
            seed="s2",
            mode="generic-strict",
            builder="ox",
            kind="main",
            ontology="ontosynthesis",
            paths=[m / "20260908_223652_s2x30"],
        ),
        _pack(
            "s3-generic-strict-ox",
            seed="s3",
            mode="generic-strict",
            builder="ox",
            kind="main",
            ontology="ontosynthesis",
            paths=[m / "20260908_232536_s3x30"],
        ),
        _pack(
            "s1-generic-noprompt-v2-pipeline",
            seed="s1",
            mode="generic-noprompt",
            builder="pipeline",
            kind="main",
            ontology="ontosynthesis",
            paths=[m / "20260909_114425_s1nv230"],
        ),
        _pack(
            "s2-generic-noprompt-v2-pipeline",
            seed="s2",
            mode="generic-noprompt",
            builder="pipeline",
            kind="main",
            ontology="ontosynthesis",
            paths=[m / "20260909_160339_s2nv230"],
        ),
        _pack(
            "s3-generic-noprompt-v2-pipeline",
            seed="s3",
            mode="generic-noprompt",
            builder="pipeline",
            kind="main",
            ontology="ontosynthesis",
            paths=[m / "20260909_160900_s3nv230"],
        ),
        _pack(
            "s1-generic-noprompt-v2-ox",
            seed="s1",
            mode="generic-noprompt",
            builder="ox",
            kind="main",
            ontology="ontosynthesis",
            paths=[m / "20260909_114653_s1nv2x30"],
        ),
        _pack(
            "s2-generic-noprompt-v2-ox",
            seed="s2",
            mode="generic-noprompt",
            builder="ox",
            kind="main",
            ontology="ontosynthesis",
            paths=[m / "20260909_160544_s2nv2x30"],
        ),
        _pack(
            "s3-generic-noprompt-v2-ox",
            seed="s3",
            mode="generic-noprompt",
            builder="ox",
            kind="main",
            ontology="ontosynthesis",
            paths=[m / "20260909_161102_s3nv2x30"],
        ),
        _pack(
            "s1-with-prompt-pipeline",
            seed="s1",
            mode="with-prompt",
            builder="pipeline",
            kind="main",
            ontology="ontosynthesis",
            paths=[m / "20260909_224421_s1wp30c"],
            note="Pooled 30 (s1wp5 + s1wp25). hops_retest scored pack.",
        ),
        _pack(
            "s2-with-prompt-pipeline",
            seed="s2",
            mode="with-prompt",
            builder="pipeline",
            kind="main",
            ontology="ontosynthesis",
            paths=[m / "20260909_200701_s2wp30c"],
            note="Canonical scored 30 (rescore of s2wp30r).",
        ),
        _pack(
            "s3-with-prompt-pipeline",
            seed="s3",
            mode="with-prompt",
            builder="pipeline",
            kind="main",
            ontology="ontosynthesis",
            paths=[m / "20260909_224606_s3wp30"],
        ),
        _pack(
            "s4-with-prompt-pipeline",
            seed="s4",
            mode="with-prompt",
            builder="pipeline",
            kind="main",
            ontology="ontosynthesis",
            paths=[m / "20260909_194723_s4wp30"],
        ),
        _pack(
            "s1-with-prompt-ox",
            seed="s1",
            mode="with-prompt",
            builder="ox",
            kind="main",
            ontology="ontosynthesis",
            paths=[
                m / "20260909_132321_ox_s1wp5sc",
                m / "20260909_152150_ox_s1wp25sc",
            ],
            note=(
                "No ox_s1wp30 pack. Pooled official-5 + clean-25 graphs. "
                "Official-5 OX scoring was prompt-contaminated; graphs are still with-prompt OX output."
            ),
        ),
        _pack(
            "s2-with-prompt-ox",
            seed="s2",
            mode="with-prompt",
            builder="ox",
            kind="main",
            ontology="ontosynthesis",
            paths=[m / "20260909_200855_ox_s2wp30c"],
        ),
        _pack(
            "s4-with-prompt-ox",
            seed="s4",
            mode="with-prompt",
            builder="ox",
            kind="main",
            ontology="ontosynthesis",
            paths=[m / "20260909_195204_ox_s4wp30"],
        ),
        _pack(
            "s1-generic-strict-pipeline-extension",
            seed="s1",
            mode="generic-strict",
            builder="pipeline",
            kind="extension",
            ontology="ontosynthesis",
            paths=[m / "20260909_001948_s1xs30"],
        ),
        _pack(
            "s2-generic-strict-pipeline-extension",
            seed="s2",
            mode="generic-strict",
            builder="pipeline",
            kind="extension",
            ontology="ontosynthesis",
            paths=[m / "20260909_002334_s2xs30"],
        ),
        _pack(
            "s3-generic-strict-pipeline-extension",
            seed="s3",
            mode="generic-strict",
            builder="pipeline",
            kind="extension",
            ontology="ontosynthesis",
            paths=[m / "20260909_000427_s3xs30"],
        ),
        _pack(
            "s1-generic-noprompt-pipeline-extension",
            seed="s1",
            mode="generic-noprompt",
            builder="pipeline",
            kind="extension",
            ontology="ontosynthesis",
            paths=[m / "20260909_002143_s1xn30"],
        ),
        _pack(
            "s2-generic-noprompt-pipeline-extension",
            seed="s2",
            mode="generic-noprompt",
            builder="pipeline",
            kind="extension",
            ontology="ontosynthesis",
            paths=[m / "20260909_002530_s2xn30"],
        ),
        _pack(
            "s3-generic-noprompt-pipeline-extension",
            seed="s3",
            mode="generic-noprompt",
            builder="pipeline",
            kind="extension",
            ontology="ontosynthesis",
            paths=[m / "20260909_002725_s3xn30"],
        ),
        _pack(
            "s1-generic-strict-ox-extension",
            seed="s1",
            mode="generic-strict",
            builder="ox",
            kind="extension",
            ontology="ontosynthesis",
            shards="ox_s1xs?",
            note="Assembled from ox_s1xsa–o live hash.ttl (no merged 30-pack).",
        ),
        _pack(
            "s3-generic-strict-ox-extension",
            seed="s3",
            mode="generic-strict",
            builder="ox",
            kind="extension",
            ontology="ontosynthesis",
            paths=[m / "20260909_000711_s3xsx30"],
        ),
        _pack(
            "s4-generic-strict-pipeline-extension-kimi",
            seed="s4",
            mode="generic-strict",
            builder="pipeline",
            kind="extension",
            ontology="ontosynthesis",
            paths=[m / "20260910_234302_s4ys30"],
            kg_model="moonshotai/kimi-k3",
            note="Only s4 extension 30-pack; KG is kimi-k3, not gpt-4o. mop_derivation=false.",
        ),
        _pack(
            "s4-generic-strict-pipeline-extension-kimi-mopderivation",
            seed="s4",
            mode="generic-strict",
            builder="pipeline",
            kind="extension",
            ontology="ontosynthesis",
            paths=[m / "20260911_131918_s4yd30"],
            kg_model="moonshotai/kimi-k3",
            note="s4 extension 30 with mop_derivation=true; KG is kimi-k3.",
        ),
        _pack(
            "ms1-generic-strict-pipeline",
            seed="ms1",
            mode="generic-strict",
            builder="pipeline",
            kind="main",
            ontology="medical",
            paths=[med / "20260910_111857_ms1s30"],
        ),
        _pack(
            "ms2-generic-strict-pipeline",
            seed="ms2",
            mode="generic-strict",
            builder="pipeline",
            kind="main",
            ontology="medical",
            paths=[med / "20260910_191350_ms2s30"],
        ),
        _pack(
            "ms3-generic-strict-pipeline",
            seed="ms3",
            mode="generic-strict",
            builder="pipeline",
            kind="main",
            ontology="medical",
            paths=[med / "20260910_191350_ms3s30"],
        ),
        _pack(
            "ms1-generic-strict-ox",
            seed="ms1",
            mode="generic-strict",
            builder="ox",
            kind="main",
            ontology="medical",
            paths=[med / "ox_ms1s30"],
        ),
        _pack(
            "ms2-generic-strict-ox",
            seed="ms2",
            mode="generic-strict",
            builder="ox",
            kind="main",
            ontology="medical",
            paths=[med / "ox_ms2s30"],
        ),
        _pack(
            "ms3-generic-strict-ox",
            seed="ms3",
            mode="generic-strict",
            builder="ox",
            kind="main",
            ontology="medical",
            paths=[med / "ox_ms3s30"],
        ),
        _pack(
            "mk4-generic-strict-pipeline",
            seed="mk4",
            mode="generic-strict",
            builder="pipeline",
            kind="main",
            ontology="medical",
            paths=[med / "20260910_175211_mk4s30"],
            note="OntoMed 30-case hybrid (kimi extract + gpt-4o KG). Extra to ms1–ms3.",
        ),
        _pack(
            "mk4-generic-strict-ox",
            seed="mk4",
            mode="generic-strict",
            builder="ox",
            kind="main",
            ontology="medical",
            paths=[med / "ox_mk4s30"],
            note="OntoMed 30-case hybrid OX counterpart.",
        ),
    ]


def missing_cells() -> list[dict[str, str]]:
    return [
        {
            "seed": "s4",
            "mode": "generic-strict",
            "builder": "pipeline",
            "kind": "main",
            "note": "No gpt-4o s4p30 merged 30-pack.",
        },
        {
            "seed": "s4",
            "mode": "generic-strict",
            "builder": "ox",
            "kind": "main",
            "note": "No gpt-4o s4x30 merged 30-pack.",
        },
        {
            "seed": "s4",
            "mode": "generic-noprompt",
            "builder": "pipeline",
            "kind": "main",
            "note": "No s4nv230 pack. noprompt v1 packs were not used.",
        },
        {
            "seed": "s4",
            "mode": "generic-noprompt",
            "builder": "ox",
            "kind": "main",
            "note": "No s4nv2x30 pack.",
        },
        {
            "seed": "s3",
            "mode": "with-prompt",
            "builder": "ox",
            "kind": "main",
            "note": "No ox_s3wp30. ox_s3wh* is hops overlay, skipped.",
        },
        {
            "seed": "s2",
            "mode": "generic-strict",
            "builder": "ox",
            "kind": "extension",
            "note": "No ox_s2xs 30-pack or shards.",
        },
        {
            "seed": "s4",
            "mode": "generic-strict",
            "builder": "pipeline",
            "kind": "extension-gpt4o",
            "note": "s4exa–o only have top.ttl. Closest 30-packs are kimi s4ys30 / s4yd30.",
        },
        {
            "seed": "s1-s4",
            "mode": "generic-noprompt",
            "builder": "ox",
            "kind": "extension",
            "note": "Pipeline xn30 exists for s1–s3; no OX extension noprompt 30-packs.",
        },
        {
            "seed": "ms1-ms3",
            "mode": "generic-noprompt / with-prompt",
            "builder": "pipeline/ox",
            "kind": "main",
            "note": "OntoMed locked 30-packs are generic-strict only.",
        },
    ]


def paper_hash(path: Path) -> str:
    parts = path.parts
    if "merged" in parts:
        idx = parts.index("merged")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    if path.parent.name == "medical_output":
        parent = path.parent.parent
        return parent.name
    if path.parent.name.startswith("cs") and len(path.parent.parent.name) == 8:
        return path.parent.parent.name
    if len(path.parent.name) == 8 and path.stem == path.parent.name:
        return path.parent.name
    return path.stem


def _skip_runtime_noise(path: Path) -> bool:
    parts = {part.casefold() for part in path.parts}
    if "score_runtime" in parts:
        return True
    if path.name.casefold() == "link.ttl":
        return True
    if path.name.casefold().endswith("_main.ttl"):
        return True
    return False


def discover_paper_ttl(run_dir: Path, *, ontology: str) -> list[Path]:
    root = Path(run_dir)
    if not root.is_dir():
        return []
    merged: list[Path] = []
    merged_root = root / "merged"
    if merged_root.is_dir():
        for folder in sorted(merged_root.iterdir()):
            if not folder.is_dir():
                continue
            preferred = folder / f"{folder.name}.ttl"
            if preferred.is_file():
                merged.append(preferred)
                continue
            merged.extend(
                path
                for path in sorted(folder.glob("*.ttl"))
                if path.name.casefold() != "link.ttl"
            )
        if merged:
            return merged
    if ontology == "medical":
        entity = [
            path
            for path in sorted(root.glob("runtime/*/medical_output/*.ttl"))
            if not _skip_runtime_noise(path)
        ]
        if entity:
            return entity
        cases = [
            path
            for path in sorted(root.glob("????????/cs*/cs*.ttl"))
            if path.stem == path.parent.name and not _skip_runtime_noise(path)
        ]
        if cases:
            return cases
        papers = [
            path
            for path in sorted(root.glob("????????/????????.ttl"))
            if path.stem == path.parent.name and not _skip_runtime_noise(path)
        ]
        if papers:
            return papers
        return [
            path
            for path in sorted(root.glob("**/medical_output/*.ttl"))
            if not _skip_runtime_noise(path)
        ]
    live = [
        path
        for path in sorted(root.glob("????????/????????.ttl"))
        if path.stem == path.parent.name and not _skip_runtime_noise(path)
    ]
    return live


def discover_shard_ttl(pattern: str) -> list[Path]:
    found: dict[str, Path] = {}
    for folder in sorted(MOPS_RUNS.glob(pattern)):
        if not folder.is_dir():
            continue
        for path in discover_paper_ttl(folder, ontology="ontosynthesis"):
            found[paper_hash(path)] = path
    return [found[key] for key in sorted(found)]


def discover_pack_ttl(pack: dict[str, Any]) -> list[Path]:
    ontology = str(pack["ontology"])
    found: dict[str, Path] = {}
    if pack.get("shards"):
        for path in discover_shard_ttl(str(pack["shards"])):
            found[str(path.resolve())] = path
    for raw in pack.get("paths") or []:
        for path in discover_paper_ttl(Path(raw), ontology=ontology):
            found[str(path.resolve())] = path
    return [found[key] for key in sorted(found)]


def _tboxes(ontology: str) -> tuple[list[Path], Path | None]:
    cached = _WORKER_CACHE.get(ontology)
    if cached is None:
        cached = resolve_ontology_tboxes(ontology)
        _WORKER_CACHE[ontology] = cached
    return cached


def check_one(ttl: str, ontology: str) -> dict[str, Any]:
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
    errors: dict[str, int] = {}
    shacl_by_path: dict[str, int] = {}
    conn_iris: list[str] = []
    for finding in report.errors():
        errors[finding.code] = errors.get(finding.code, 0) + 1
        if finding.code in CONN_CODES:
            conn_iris.extend(finding.iris)
        if finding.code in SHACL_CODES and len(finding.iris) >= 2:
            local = finding.iris[1].rsplit("/", 1)[-1].rsplit("#", 1)[-1]
            shacl_by_path[local] = shacl_by_path.get(local, 0) + 1
        elif finding.code in SHACL_CODES:
            key = finding.message.split()[0] if finding.message else "unknown"
            shacl_by_path[key] = shacl_by_path.get(key, 0) + 1
    codes = set(errors)
    return {
        "file": path.name,
        "hash": paper_hash(path),
        "source": str(path),
        "ok": report.ok,
        "instances": report.instance_count,
        "roots": len(report.roots),
        "errors": errors,
        "connectivity": bool(codes & CONN_CODES),
        "shacl": bool(codes & SHACL_CODES),
        "tbox": bool(codes & TBOX_CODES),
        "shacl_by_path": shacl_by_path,
        "orphan_iris": sorted(set(conn_iris))[:40],
    }


def summarize_papers(papers: list[dict[str, Any]]) -> dict[str, Any]:
    error_codes: Counter[str] = Counter()
    shacl_by_path: Counter[str] = Counter()
    by_paper_conn: dict[str, list[str]] = defaultdict(list)
    for paper in papers:
        for code, count in paper.get("errors", {}).items():
            error_codes[code] += count
        for path, count in paper.get("shacl_by_path", {}).items():
            shacl_by_path[path] += count
        if paper.get("connectivity"):
            by_paper_conn[paper["hash"]].extend(paper.get("orphan_iris") or [])
    papers_by_hash: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for paper in papers:
        papers_by_hash[paper["hash"]].append(paper)
    paper_ok = sum(
        1 for group in papers_by_hash.values() if all(item["ok"] for item in group)
    )
    return {
        "files": len(papers),
        "unique_papers": len(papers_by_hash),
        "ok": sum(1 for item in papers if item["ok"]),
        "fail": sum(1 for item in papers if not item["ok"]),
        "papers_ok": paper_ok,
        "papers_fail": len(papers_by_hash) - paper_ok,
        "connectivity_fail": sum(1 for item in papers if item["connectivity"]),
        "shacl_fail": sum(1 for item in papers if item["shacl"]),
        "tbox_fail": sum(1 for item in papers if item["tbox"]),
        "error_codes": dict(error_codes),
        "shacl_by_path": dict(shacl_by_path),
        "orphan_hashes": sorted(by_paper_conn),
        "orphan_iris_by_hash": {
            key: sorted(set(values))[:20] for key, values in sorted(by_paper_conn.items())
        },
        "papers": [
            {
                "file": item["file"],
                "hash": item["hash"],
                "ok": item["ok"],
                "instances": item["instances"],
                "roots": item["roots"],
                "errors": item["errors"],
            }
            for item in papers
        ],
    }


def _json_pack(pack: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "id": pack["id"],
        "seed": pack["seed"],
        "mode": pack["mode"],
        "builder": pack["builder"],
        "kind": pack["kind"],
        "ontology": pack["ontology"],
        "note": pack.get("note") or "",
        "kg_model": pack.get("kg_model") or "",
        "paths": [str(path) for path in pack.get("paths") or []],
        "shards": pack.get("shards"),
    }
    return payload


def run_campaign(*, jobs: int, out_path: Path, redo: list[str] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "skip_owlrl": True,
        "skip_owl_dl": True,
        "note": (
            "Locked 30-case A-Box health. OWL-RL/DL skipped (OM-2). "
            "SHACL uses current T-Box-faithful ontosynthesis shapes. "
            "noprompt v1, hops overlays, and official-5-only OX packs are excluded "
            "except the pooled s1 with-prompt OX 5+25 graphs."
        ),
        "missing": missing_cells(),
        "packs": {},
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.is_file():
        try:
            existing = json.loads(out_path.read_text(encoding="utf-8"))
            if isinstance(existing, dict) and isinstance(existing.get("packs"), dict):
                payload["packs"] = existing["packs"]
        except json.JSONDecodeError:
            pass
    packs = locked_packs()
    for index, pack in enumerate(packs, start=1):
        previous = payload["packs"].get(pack["id"])
        redo_hit = any(token in pack["id"] for token in (redo or []) if token)
        if (
            not redo_hit
            and isinstance(previous, dict)
            and previous.get("status") == "ok"
            and previous.get("files")
        ):
            print(
                f"[{index}/{len(packs)}] {pack['id']} resume skip "
                f"files={previous.get('files')}",
                flush=True,
            )
            continue
        ttl_paths = discover_pack_ttl(pack)
        print(
            f"[{index}/{len(packs)}] {pack['id']} files={len(ttl_paths)}",
            flush=True,
        )
        entry = _json_pack(pack)
        if not ttl_paths:
            entry["status"] = "missing_ttl"
            entry["files"] = 0
            payload["packs"][pack["id"]] = entry
            out_path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            continue
        papers: list[dict[str, Any]] = []
        if jobs <= 1:
            for path in ttl_paths:
                papers.append(check_one(str(path), pack["ontology"]))
        else:
            with ProcessPoolExecutor(max_workers=jobs) as pool:
                futures = [
                    pool.submit(check_one, str(path), pack["ontology"])
                    for path in ttl_paths
                ]
                for future in as_completed(futures):
                    papers.append(future.result())
        papers.sort(key=lambda item: (item["hash"], item["file"]))
        entry.update(summarize_papers(papers))
        entry["status"] = "ok"
        payload["packs"][pack["id"]] = entry
        out_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(
            f"    papers_ok={entry['papers_ok']}/{entry['unique_papers']} "
            f"files_ok={entry['ok']}/{entry['files']} "
            f"conn={entry['connectivity_fail']} shacl={entry['shacl_fail']} "
            f"tbox={entry['tbox_fail']}",
            flush=True,
        )
    return payload


def check_one_hermit(ttl: str, ontology: str) -> dict[str, Any]:
    """HermiT-only check: no SHACL, no OWL-RL, domain T-Box without OM-2."""
    from .sparql_coverage import hermit_worker, primary_tbox_no_om2

    tboxes = primary_tbox_no_om2(ontology)
    return hermit_worker(
        {
            "ttl": [ttl],
            "tboxes": [str(path) for path in tboxes],
            "ontology": ontology,
        }
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=MOPS_RUNS / "_abox_health_campaign.json",
    )
    parser.add_argument("--jobs", type=int, default=6)
    parser.add_argument(
        "--redo",
        action="append",
        default=[],
        help="Re-run packs whose id contains this substring (repeatable).",
    )
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    run_campaign(
        jobs=max(1, int(args.jobs)),
        out_path=Path(args.out),
        redo=list(args.redo or []),
    )
    print(f"Wrote {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
