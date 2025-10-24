import json
import argparse
import hashlib
from pathlib import Path
from collections import Counter
from typing import Dict, Tuple, List, Optional, Set
import re


def load_ground_truth(path: Path) -> Dict[str, dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    gt: Dict[str, dict] = {}
    for item in data:
        ccdc = str(item.get("ccdc_number") or "").strip()
        if ccdc:
            gt[ccdc] = item
    return gt


def extract_assembly(obj: dict) -> Tuple[str, str, str]:
    am = obj.get("assembly_model") or {}
    return (
        str(am.get("am_label") or ""),
        str(am.get("polyhedral_shape_symbol") or ""),
        str(am.get("symmetry_point_group") or ""),
    )


def extract_gbus(obj: dict) -> Counter:
    # Prefer top-level 'gbus' else fallback to assembly_model.gbus
    gbus = obj.get("gbus")
    if not isinstance(gbus, list):
        gbus = (obj.get("assembly_model") or {}).get("gbus")
    pairs: List[Tuple[str, str]] = []
    if isinstance(gbus, list):
        for g in gbus:
            td = (g or {}).get("gbu_type_detail") or {}
            plan = str(td.get("has_planarity") or "")
            mod = str(td.get("has_modularity") or "")
            if plan or mod:
                pairs.append((plan, mod))
    return Counter(pairs)


def extract_cbus(obj: dict) -> Counter:
    cbus = obj.get("cbus")
    items: List[str] = []
    if isinstance(cbus, list):
        for c in cbus:
            v = str((c or {}).get("cbu_formula") or "").strip()
            items.append(v)
    return Counter(items)


_AM_LABEL_PATTERN = re.compile(r"\((\d+)\s*-\s*(planar|bent|linear|pyramidal)\)\s*x\s*(\d+)")


def normalize_am_label(label: str) -> str:
    text = str(label or "").strip()
    tokens = _AM_LABEL_PATTERN.findall(text)
    if not tokens:
        return text
    # tokens: List[Tuple[modularity, planarity, count]]
    # sort deterministically: by planarity, then modularity int, then count int
    order = {"linear": 0, "bent": 1, "planar": 2, "pyramidal": 3}
    tokens_sorted = sorted(tokens, key=lambda t: (order.get(t[1], 99), int(t[0]), int(t[2])))
    parts = [f"({m}-{p})x{c}" for m, p, c in tokens_sorted]
    return "".join(parts)


def normalize_formula(s: str) -> str:
    return str(s or "").strip()


def compute_metrics(files: List[Path], gt: Dict[str, dict]) -> Tuple[Counter, Counter, List[Path], Counter, Set[str]]:
    totals = Counter()
    missing_ccdc_files: List[Path] = []
    unknown_ccdc = Counter()
    field_hits = Counter(
        {
            "mop_formula": 0,
            "am_label": 0,
            "polyhedral_shape_symbol": 0,
            "symmetry_point_group": 0,
            "gbus": 0,
            "cbus": 0,
            "all_fields": 0,
        }
    )

    comparable_ccdcs: Set[str] = set()

    for f in files:
        totals["files_scanned"] += 1
        try:
            pred = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            totals["read_errors"] += 1
            continue

        ccdc = str(pred.get("ccdc_number") or "").strip()
        if not ccdc:
            totals["missing_ccdc"] += 1
            missing_ccdc_files.append(f)
            continue

        totals["have_ccdc"] += 1
        if ccdc not in gt:
            totals["ccdc_not_in_ground_truth"] += 1
            unknown_ccdc[ccdc] += 1
            continue

        g = gt[ccdc]
        totals["comparable"] += 1
        comparable_ccdcs.add(ccdc)

        mop_hit = (normalize_formula(pred.get("mop_formula")) == normalize_formula(g.get("mop_formula")))
        am_p = extract_assembly(pred)
        am_g = extract_assembly(g)
        am_label_hit = (normalize_am_label(am_p[0]) == normalize_am_label(am_g[0]))
        poly_hit = (am_p[1] == am_g[1])
        sym_hit = (am_p[2] == am_g[2])
        gbus_hit = (extract_gbus(pred) == extract_gbus(g))
        cbus_hit = (extract_cbus(pred) == extract_cbus(g))

        field_hits["mop_formula"] += int(mop_hit)
        field_hits["am_label"] += int(am_label_hit)
        field_hits["polyhedral_shape_symbol"] += int(poly_hit)
        field_hits["symmetry_point_group"] += int(sym_hit)
        field_hits["gbus"] += int(gbus_hit)
        field_hits["cbus"] += int(cbus_hit)

        if all([mop_hit, am_label_hit, poly_hit, sym_hit, gbus_hit, cbus_hit]):
            field_hits["all_fields"] += 1

    return totals, field_hits, missing_ccdc_files, unknown_ccdc, comparable_ccdcs


def render_markdown(totals: Counter, field_hits: Counter, missing_ccdc_files: List[Path], unknown_ccdc: Counter, title: str) -> str:
    md: List[str] = []
    md.append(f"### {title}")
    md.append("")
    md.append("| Metric | Count |")
    md.append("| --- | ---: |")
    md.append(f"| Files scanned | {totals['files_scanned']} |")
    md.append(f"| Readable outputs | {totals['files_scanned'] - totals['read_errors']} |")
    md.append(f"| Missing CCDC in output | {totals['missing_ccdc']} |")
    md.append(f"| Outputs with CCDC | {totals['have_ccdc']} |")
    md.append(f"| Outputs with unknown CCDC (not in GT) | {totals['ccdc_not_in_ground_truth']} |")
    md.append(f"| Comparable (CCDC in GT) | {totals['comparable']} |")

    md.append("")
    md.append("### Field Matches")
    md.append("")
    md.append("| Field | Matches | Total | Mismatches | Accuracy |")
    md.append("| --- | ---: | ---: | ---: | ---: |")
    comp = totals["comparable"] or 0
    for label, key in [
        ("mop_formula", "mop_formula"),
        ("assembly_model.am_label", "am_label"),
        ("assembly_model.polyhedral_shape_symbol", "polyhedral_shape_symbol"),
        ("assembly_model.symmetry_point_group", "symmetry_point_group"),
        ("gbus (multiset of (planarity, modularity))", "gbus"),
        ("cbus (multiset of formulas)", "cbus"),
    ]:
        hit = field_hits[key]
        miss = max(0, comp - hit)
        acc = (hit / comp * 100.0) if comp else 0.0
        md.append(f"| {label} | {hit} | {comp} | {miss} | {acc:.1f}% |")

    # Exact all-fields accuracy
    all_hit = field_hits["all_fields"]
    all_miss = max(0, comp - all_hit)
    all_acc = (all_hit / comp * 100.0) if comp else 0.0
    md.append(f"| All fields exact | {all_hit} | {comp} | {all_miss} | {all_acc:.1f}% |")

    if missing_ccdc_files:
        md.append("")
        md.append("### Files Missing CCDC Number")
        for p in missing_ccdc_files:
            md.append(f"- `{p}`")

    if unknown_ccdc:
        md.append("")
        md.append("### Unknown CCDC Numbers (not in Ground Truth)")
        md.append("")
        md.append("| CCDC | Count |")
        md.append("| --- | ---: |")
        for ccdc, cnt in sorted(unknown_ccdc.items()):
            md.append(f"| {ccdc} | {cnt} |")

    return "\n".join(md)


def evaluate(outputs_root: Path, gt_path: Path, only_hash: Optional[str] = None, out_dir: Optional[Path] = None) -> None:
    gt = load_ground_truth(gt_path)

    # collect hashes to evaluate
    hashes: List[str] = []
    for d in outputs_root.iterdir():
        if not d.is_dir() or len(d.name) != 8:
            continue
        if only_hash and d.name != only_hash:
            continue
        hashes.append(d.name)
    hashes.sort()

    if out_dir is None:
        out_dir = Path("evaluation/ontomops_derivation_evaluation/reports")
    out_dir.mkdir(parents=True, exist_ok=True)

    # data subset output directory
    data_out_dir = Path("evaluation/data")
    data_out_dir.mkdir(parents=True, exist_ok=True)

    # overall aggregates
    overall_totals = Counter()
    overall_field_hits = Counter(
        {
            "mop_formula": 0,
            "am_label": 0,
            "polyhedral_shape_symbol": 0,
            "symmetry_point_group": 0,
            "gbus": 0,
            "cbus": 0,
            "all_fields": 0,
        }
    )
    overall_missing: List[Path] = []
    overall_unknown = Counter()

    # per-hash reports
    for hv in hashes:
        full_dir = outputs_root / hv / "cbu_derivation" / "full"
        files = sorted(full_dir.glob("*.json")) if full_dir.exists() else []
        totals, field_hits, missing_ccdc_files, unknown_ccdc, comparable_ccdcs = compute_metrics(files, gt)

        # accumulate overall
        overall_totals += totals
        overall_field_hits += field_hits
        overall_missing.extend(missing_ccdc_files)
        overall_unknown += unknown_ccdc

        md_hash = render_markdown(totals, field_hits, missing_ccdc_files, unknown_ccdc, title=f"Hash `{hv}` Evaluation (Count-wise)")
        hash_md_path = out_dir / f"{hv}.md"
        hash_md_path.write_text(md_hash, encoding="utf-8")

        # Write subset of ground truth used for this hash
        subset_gt = [gt[c] for c in sorted(comparable_ccdcs)]
        hash_json_path = data_out_dir / f"{hv}.json"
        hash_json_path.write_text(json.dumps(subset_gt, ensure_ascii=False, indent=2), encoding="utf-8")

        # Print generated file paths
        print(f"Wrote per-hash JSON: {hash_json_path}")
        print(f"Wrote per-hash MD:   {hash_md_path}")

    # overall report
    md_overall = render_markdown(overall_totals, overall_field_hits, overall_missing, overall_unknown, title="Overall Evaluation (Count-wise)")
    overall_md_path = out_dir / "_overall.md"
    overall_md_path.write_text(md_overall, encoding="utf-8")

    # also print overall to stdout for convenience
    print(md_overall)
    print(f"Wrote overall MD:   {overall_md_path}")


def main():
    ap = argparse.ArgumentParser(description="Evaluate AM/GBU agent outputs against concise ground truth")
    ap.add_argument("--outputs-root", type=Path, default=Path("data"), help="Root data directory containing hash folders")
    ap.add_argument(
        "--ground-truth",
        type=Path,
        default=Path("data/ontologies/full_mop_expanded_concise.json"),
        help="Path to concise ground-truth JSON",
    )
    ap.add_argument("--file", type=str, help="Specific 8-char hash or arbitrary id (hashed)")
    ap.add_argument("--out-dir", type=Path, default=Path("evaluation/ontomops_derivation_evaluation/reports"), help="Directory to write markdown reports")
    args = ap.parse_args()
    hv = None
    if args.file:
        v = args.file.strip()
        if len(v) == 8 and (args.outputs_root / v).exists():
            hv = v
        else:
            try:
                hv = hashlib.sha256(v.encode()).hexdigest()[:8]
            except Exception:
                hv = None
    evaluate(args.outputs_root, args.ground_truth, only_hash=hv, out_dir=args.out_dir)


if __name__ == "__main__":
    main()

