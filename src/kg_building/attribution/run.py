"""Load GT/Pred and run ledger-conditioned attribution for one paper or a run."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.kg_building.attribution.atoms import (
    COLLECTORS,
    MODULES,
    PRED_FILES,
    ModuleScore,
)
from src.kg_building.attribution.judge import ATTRIBUTION_MODEL, JsonInvoker, judge_atoms
from src.kg_building.attribution.ledger import load_paper_ledger
from src.kg_building.attribution.official import load_official_module
from src.kg_building.attribution.report import render_module_markdown, summarize_module
from src.kg_building.scorer_repo import find_scorer_repo

OFFICIAL_MODULES = MODULES + ("cbu_formula",)

PAPERS_EVAL30 = (
    Path(__file__).resolve().parents[1] / "ontologx" / "papers_eval30.json"
)


def doi_filename(doi: str) -> str:
    return str(doi).strip().replace("/", "_") + ".json"


def load_hash_to_doi(papers: Path | None = None) -> dict[str, str]:
    mapping: dict[str, str] = {}
    path = papers or PAPERS_EVAL30
    if path.is_file():
        payload = json.loads(path.read_text(encoding="utf-8"))
        for row in payload.get("papers") or []:
            paper_hash = str(row.get("hash") or "").strip()
            doi = str(row.get("doi") or "").strip()
            if paper_hash and doi:
                mapping[paper_hash] = doi
    return mapping


def resolve_gt_root(gt_root: Path | None = None, scorer_repo: Path | None = None) -> Path:
    if gt_root is not None:
        return Path(gt_root)
    repo = Path(__file__).resolve().parents[3]
    assets = repo / "data" / "scorer_assets" / "full_ground_truth"
    if (assets / "steps").is_dir():
        return assets
    scorer = scorer_repo or find_scorer_repo()
    if scorer is not None and (scorer / "full_ground_truth" / "steps").is_dir():
        return scorer / "full_ground_truth"
    return assets


def load_module_json(
    *,
    module: str,
    paper_hash: str,
    doi: str,
    pred_root: Path,
    gt_root: Path,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, Path | None, Path | None]:
    pred_path = Path(pred_root) / paper_hash / PRED_FILES[module]
    gt_path = Path(gt_root) / module / doi_filename(doi)
    pred_obj = None
    gt_obj = None
    if pred_path.is_file():
        pred_obj = json.loads(pred_path.read_text(encoding="utf-8"))
    if gt_path.is_file():
        gt_obj = json.loads(gt_path.read_text(encoding="utf-8"))
    return gt_obj, pred_obj, gt_path if gt_path.is_file() else None, pred_path if pred_path.is_file() else None


def attribute_paper(
    paper_hash: str,
    *,
    pred_root: Path,
    hint_runs: list[str | Path],
    gt_root: Path | None = None,
    scorer_repo: Path | None = None,
    modules: tuple[str, ...] = MODULES,
    model: str = ATTRIBUTION_MODEL,
    heuristic_only: bool = False,
    cbu_derivation: bool = False,
    invoke: JsonInvoker | None = None,
    hash_to_doi: dict[str, str] | None = None,
    scores_root: Path | None = None,
) -> dict[str, Any]:
    doi_map = hash_to_doi or load_hash_to_doi()
    doi = doi_map.get(paper_hash)
    if not doi:
        raise FileNotFoundError(f"No DOI mapping for hash {paper_hash}")
    gold_root = resolve_gt_root(gt_root, scorer_repo)
    ledger, ledger_sources = load_paper_ledger(paper_hash, hint_runs)
    module_reports: list[dict[str, Any]] = []
    collected: list[tuple[ModuleScore, Path | None, Path | None]] = []
    skipped: list[dict[str, Any]] = []
    score_root = Path(scores_root) if scores_root is not None else None
    for module in modules:
        lookup = "cbu" if module == "cbu_formula" else module
        gt_obj, pred_obj, gt_path, pred_path = load_module_json(
            module=lookup,
            paper_hash=paper_hash,
            doi=doi,
            pred_root=pred_root,
            gt_root=gold_root,
        )
        score: ModuleScore | None = None
        if score_root is not None:
            score = load_official_module(score_root, module, paper_hash)
        elif module in COLLECTORS and gt_obj is not None and pred_obj is not None:
            score = COLLECTORS[module](gt_obj, pred_obj, paper_hash=paper_hash)
        if score is None:
            skipped.append(
                {
                    "module": module,
                    "skipped": True,
                    "reason": "missing_official_or_pred",
                    "gt_path": str(gt_path) if gt_path else None,
                    "pred_path": str(pred_path) if pred_path else None,
                }
            )
            continue
        print(
            f"  [{module}] atoms={len(score.atoms)} tp={score.tp} fp={score.fp} fn={score.fn}"
            f" official={score.official}",
            flush=True,
        )
        collected.append((score, gt_path, pred_path))
    paper_atoms = [atom for score, _gt, _pred in collected for atom in score.atoms]
    judged_all = judge_atoms(
        paper_atoms,
        ledger,
        model=model,
        heuristic_only=heuristic_only,
        cbu_derivation=cbu_derivation,
        invoke=invoke,
    )
    by_id = {row["atom_id"]: row for row in judged_all}
    skipped_by_module = {row["module"]: row for row in skipped}
    for module in modules:
        if module in skipped_by_module:
            module_reports.append(skipped_by_module[module])
            continue
        match = next((item for item in collected if item[0].module == module), None)
        if match is None:
            continue
        score, gt_path, pred_path = match
        judged = [by_id[atom.atom_id] for atom in score.atoms]
        summary = summarize_module(score, judged)
        summary["gt_path"] = str(gt_path)
        summary["pred_path"] = str(pred_path)
        summary["atoms"] = judged
        module_reports.append(summary)
    payload = {
        "hash": paper_hash,
        "doi": doi,
        "model": model if not heuristic_only else "heuristic",
        "ledger_sources": ledger_sources,
        "ledger_chars": len(ledger),
        "modules": module_reports,
        "monotonicity_ok": all(
            item.get("skipped") or item.get("monotonicity_ok", True)
            for item in module_reports
        ),
    }
    return payload


def attribute_run(
    paper_hashes: list[str],
    *,
    pred_root: Path,
    hint_runs: list[str | Path],
    out_dir: Path,
    gt_root: Path | None = None,
    scorer_repo: Path | None = None,
    modules: tuple[str, ...] = MODULES,
    model: str = ATTRIBUTION_MODEL,
    heuristic_only: bool = False,
    cbu_derivation: bool = False,
    invoke: JsonInvoker | None = None,
    skip_existing: bool = False,
    scores_root: Path | None = None,
) -> dict[str, Any]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    doi_map = load_hash_to_doi()
    papers: list[dict[str, Any]] = []
    for paper_hash in paper_hashes:
        existing = out / f"{paper_hash}.json"
        if skip_existing and existing.is_file():
            payload = json.loads(existing.read_text(encoding="utf-8"))
            papers.append(payload)
            print(f"[skip] {paper_hash}", flush=True)
            continue
        print(f"[attr] {paper_hash}", flush=True)
        payload = attribute_paper(
            paper_hash,
            pred_root=pred_root,
            hint_runs=hint_runs,
            gt_root=gt_root,
            scorer_repo=scorer_repo,
            modules=modules,
            model=model,
            heuristic_only=heuristic_only,
            cbu_derivation=cbu_derivation,
            invoke=invoke,
            hash_to_doi=doi_map,
            scores_root=scores_root,
        )
        papers.append(payload)
        (out / f"{paper_hash}.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        (out / f"{paper_hash}.md").write_text(
            _paper_markdown(payload), encoding="utf-8"
        )
    overall = {
        "hashes": paper_hashes,
        "model": model if not heuristic_only else "heuristic",
        "micro": _micro_modules(papers),
        "papers": [
            {
                "hash": item["hash"],
                "doi": item["doi"],
                "monotonicity_ok": item["monotonicity_ok"],
                "modules": [
                    {
                        "module": row.get("module"),
                        "skipped": row.get("skipped", False),
                        "kg_f1": (row.get("official") or {}).get("f1"),
                        "extraction_f1": (row.get("extraction_counterfactual") or {}).get(
                            "f1"
                        ),
                    }
                    for row in item.get("modules") or []
                ],
            }
            for item in papers
        ],
        "monotonicity_ok": all(item["monotonicity_ok"] for item in papers),
    }
    (out / "_overall.json").write_text(
        json.dumps(overall, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (out / "_overall.md").write_text(_overall_markdown(overall), encoding="utf-8")
    return overall


def _f1(tp: int, fp: int, fn: int) -> float:
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    return (2 * prec * rec / (prec + rec)) if prec + rec else 0.0


def _micro_modules(papers: list[dict[str, Any]]) -> dict[str, Any]:
    buckets: dict[str, dict[str, int]] = {}
    for item in papers:
        for row in item.get("modules") or []:
            if row.get("skipped"):
                continue
            module = str(row.get("module") or "")
            official = row.get("official") or {}
            extraction = row.get("extraction_counterfactual") or {}
            attr = row.get("attribution") or {}
            bucket = buckets.setdefault(
                module,
                {
                    "tp": 0,
                    "fp": 0,
                    "fn": 0,
                    "ext_tp": 0,
                    "ext_fp": 0,
                    "ext_fn": 0,
                    "kg_fn": 0,
                    "extraction_fn": 0,
                    "derivation_fn": 0,
                    "extraction_hallucination_fp": 0,
                    "kg_invention_fp": 0,
                },
            )
            bucket["tp"] += int(official.get("tp") or 0)
            bucket["fp"] += int(official.get("fp") or 0)
            bucket["fn"] += int(official.get("fn") or 0)
            bucket["ext_tp"] += int(extraction.get("tp") or 0)
            bucket["ext_fp"] += int(extraction.get("fp") or 0)
            bucket["ext_fn"] += int(extraction.get("fn") or 0)
            bucket["kg_fn"] += int(attr.get("kg_fn") or 0)
            bucket["extraction_fn"] += int(attr.get("extraction_fn") or 0)
            bucket["derivation_fn"] += int(attr.get("derivation_fn") or 0)
            bucket["extraction_hallucination_fp"] += int(
                attr.get("extraction_hallucination_fp") or 0
            )
            bucket["kg_invention_fp"] += int(attr.get("kg_invention_fp") or 0)
    out: dict[str, Any] = {}
    for module, bucket in buckets.items():
        out[module] = {
            **bucket,
            "kg_f1": round(_f1(bucket["tp"], bucket["fp"], bucket["fn"]), 3),
            "extraction_f1": round(
                _f1(bucket["ext_tp"], bucket["ext_fp"], bucket["ext_fn"]), 3
            ),
        }
    return out


def _paper_markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# Extraction vs KG attribution — {payload['hash']}\n",
        f"- DOI: `{payload['doi']}`\n",
        f"- Judge: `{payload['model']}`\n",
        f"- Ledger files: {len(payload.get('ledger_sources') or [])}\n",
        f"- Monotonicity: {'ok' if payload['monotonicity_ok'] else 'FAILED'}\n",
    ]
    for row in payload.get("modules") or []:
        if row.get("skipped"):
            lines.append(f"## {row['module']}\n\nSkipped ({row.get('reason')}).\n")
            continue
        lines.append(render_module_markdown(row))
        fn_kg = [
            atom
            for atom in row.get("atoms") or []
            if atom.get("error_type") == "fn" and atom.get("stage") == "kg"
        ]
        fn_ext = [
            atom
            for atom in row.get("atoms") or []
            if atom.get("error_type") == "fn" and atom.get("stage") == "extraction"
        ]
        if fn_kg:
            lines.append("### KG misses (ledger had the fact)\n")
            for atom in fn_kg[:40]:
                lines.append(
                    f"- `{atom['field']}` gt={atom.get('gt_value')!r} "
                    f"({atom.get('reason') or 'no reason'})\n"
                )
        if fn_ext:
            lines.append("### Extraction misses (ledger lacked the fact)\n")
            for atom in fn_ext[:40]:
                lines.append(
                    f"- `{atom['field']}` gt={atom.get('gt_value')!r} "
                    f"({atom.get('reason') or 'no reason'})\n"
                )
    return "\n".join(lines) + "\n"


def _overall_markdown(overall: dict[str, Any]) -> str:
    lines = [
        "# Extraction vs KG attribution\n",
        f"- Judge: `{overall['model']}`\n",
        f"- Papers: {len(overall.get('papers') or [])}\n",
        f"- Monotonicity: {'ok' if overall['monotonicity_ok'] else 'FAILED'}\n",
    ]
    micro = overall.get("micro") or {}
    if micro:
        lines.extend(
            [
                "\n## Micro (all papers)\n\n",
                "| Module | KG F1 | Extraction F1 | KG FN | Extraction FN | Derivation FN |\n",
                "| --- | ---: | ---: | ---: | ---: | ---: |\n",
            ]
        )
        for module, row in micro.items():
            lines.append(
                f"| {module} | {row.get('kg_f1')} | {row.get('extraction_f1')} | "
                f"{row.get('kg_fn')} | {row.get('extraction_fn')} | "
                f"{row.get('derivation_fn')} |\n"
            )
        lines.append("\n")
    lines.extend(
        [
            "| Hash | Module | KG F1 | Extraction F1 |\n",
            "| --- | --- | ---: | ---: |\n",
        ]
    )
    for paper in overall.get("papers") or []:
        for row in paper.get("modules") or []:
            if row.get("skipped"):
                lines.append(f"| {paper['hash']} | {row['module']} | skipped | skipped |\n")
                continue
            lines.append(
                f"| {paper['hash']} | {row['module']} | {row.get('kg_f1')} | "
                f"{row.get('extraction_f1')} |\n"
            )
    return "".join(lines) + "\n"
