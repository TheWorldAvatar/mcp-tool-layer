#!/usr/bin/env python3
"""
Compare iter3 step counts with ground truth step counts.

For each hash directory under data/:
- Collect iter3 hint files: data/<hash>/mcp_run/iter3_hints_*.txt
- For each file, count steps by matching semicolon-delimited step lines
  (e.g., "Add; 1; ...", "HeatChill; 7; ...").
- This produces a list of step counts for the DOI (predicted).

Ground truth:
- earlier_ground_truth/steps/<doi>.json contains a "Synthesis" array.
- For each synthesis, count len(steps) to produce GT counts list.

Output:
- For each hash: print DOI, sorted predicted counts, sorted GT counts,
  and whether they match.

Usage:
  python -m evaluation.extraction_performance_evaluation.step_comparison
  python -m evaluation.extraction_performance_evaluation.step_comparison --file <doi_or_hash>
"""

import re
import json
import argparse
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

DATA_DIR = Path("data")
STEPS_GT_DIR = Path("earlier_ground_truth/steps")
DOI_HASH_MAP_PATH = DATA_DIR / "doi_to_hash.json"
OUT_MD_DIR = Path("evaluation/development_check/steps")
MERGED_TLL_DIR = Path("evaluation/data/merged_tll")


def _load_doi_to_hash() -> Dict[str, str]:
    if not DOI_HASH_MAP_PATH.exists():
        return {}
    try:
        return json.loads(DOI_HASH_MAP_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _invert(d2h: Dict[str, str]) -> Dict[str, str]:
    return {h: d for d, h in d2h.items()}


def _resolve_pairs(target: Optional[str], d2h: Dict[str, str]) -> List[Tuple[str, str]]:
    pairs: List[Tuple[str, str]] = []
    h2d = _invert(d2h)
    if not target:
        for entry in DATA_DIR.iterdir():
            if not entry.is_dir():
                continue
            if entry.name.startswith('.') or entry.name in ["log", "ontologies", "third_party_repos", "__pycache__"]:
                continue
            doi = h2d.get(entry.name)
            if doi:
                pairs.append((entry.name, doi))
        return pairs
    # explicit hash
    p = DATA_DIR / target
    if p.exists() and p.is_dir():
        doi = h2d.get(target, "")
        if doi:
            pairs.append((target, doi))
        return pairs
    # explicit doi
    h = d2h.get(target)
    if h:
        pairs.append((h, target))
    return pairs


def _count_steps_in_iter3_file(path: Path) -> int:
    try:
        txt = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return 0
    # Count lines that look like: "Action; 1; ...", "Action; order: 1; ...", or "Action; order=1; ..."
    # Also handle numeric bullets like: "1. Action; order: 1; ..." and "step_type: Action; order: 1; ..."
    # Ignore bracketed commentary lines like "[Branching: ...]"
    pattern = r"(?m)^\s*(?:step_type\s*:\s*)?(?:\d+[\.)]\s*)?[A-Za-z][A-Za-z]*\s*;\s*(?:order\s*[:=]\s*)?\d+\s*;"
    return len(re.findall(pattern, txt))


def _predicted_step_counts(hash_dir: Path) -> List[int]:
    mcp_dir = hash_dir / "mcp_run"
    if not mcp_dir.exists():
        return []
    counts: List[int] = []
    for f in sorted(mcp_dir.glob("iter3_hints_*.txt")):
        n = _count_steps_in_iter3_file(f)
        if n > 0:
            counts.append(n)
    return counts


def _iter3_hint_files(hash_dir: Path) -> List[Path]:
    mcp_dir = hash_dir / "mcp_run"
    if not mcp_dir.exists():
        return []
    return sorted(mcp_dir.glob("iter3_hints_*.txt"))


# -------- CCDC-anchored step mapping and order-sensitive scoring --------

def _map_steps_by_ccdc_from_gt_like(data: Any) -> Dict[str, List[str]]:
    m: Dict[str, List[str]] = {}
    for syn in (data or {}).get("Synthesis", []) or []:
        ccdc = str((syn or {}).get("productCCDCNumber") or "").strip()
        if not ccdc:
            continue
        ops: List[str] = []
        for st in (syn or {}).get("steps", []) or []:
            if isinstance(st, dict) and len(st) == 1:
                ops.append(next(iter(st.keys())))
        m[ccdc] = ops
    return m


def _map_steps_by_ccdc_from_pred_result(data: Any) -> Dict[str, List[str]]:
    m: Dict[str, List[str]] = {}
    def walk(x: Any):
        if isinstance(x, dict):
            if "productCCDCNumber" in x and "steps" in x:
                ccdc = str(x.get("productCCDCNumber") or "").strip()
                ops: List[str] = []
                for st in x.get("steps") or []:
                    if isinstance(st, dict) and len(st) == 1:
                        ops.append(next(iter(st.keys())))
                if ccdc:
                    m[ccdc] = ops
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(data)
    return m


def _score_ordered_maps(gt_map: Dict[str, List[str]], pred_map: Dict[str, List[str]]) -> Tuple[int, int, int]:
    tp = fp = fn = 0
    keys = set(gt_map.keys()) | set(pred_map.keys())
    for k in keys:
        g = gt_map.get(k, [])
        p = pred_map.get(k, [])
        n = min(len(g), len(p))
        eq = sum(1 for i in range(n) if g[i] == p[i])
        tp += eq
        fp += (len(p) - eq)
        fn += (len(g) - eq)
    return tp, fp, fn


def _gt_step_counts_for_doi(doi: str) -> Optional[List[int]]:
    gt_path = STEPS_GT_DIR / f"{doi}.json"
    if not gt_path.exists():
        return None
    try:
        data = json.loads(gt_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    syn = data.get("Synthesis") or []
    out: List[int] = []
    for s in syn:
        steps = s.get("steps") or []
        out.append(len(steps))
    return out


def _compare(a: List[int], b: List[int]) -> Tuple[bool, List[int], List[int]]:
    sa = sorted(a)
    sb = sorted(b)
    return (sa == sb, sa, sb)


def run(target: Optional[str], previous: bool) -> int:
    d2h = _load_doi_to_hash()
    pairs = _resolve_pairs(target, d2h)
    # In previous mode, support running by DOI list if mapping is missing
    if previous:
        if target and not pairs:
            doi = target if "/" in target else target.replace("_", "/")
            if doi:
                pairs = [("", doi)]
        if not target:
            prev_dir = Path("previous_work/steps")
            if prev_dir.exists():
                pairs = [("", p.stem) for p in sorted(prev_dir.glob("*.json"))]
    if not pairs:
        print("No matching targets.")
        return 1

    matches = mismatches = 0
    for h, doi in pairs:
        print("\n" + "=" * 80)
        print(f"{h} (DOI: {doi})")
        gt_counts = _gt_step_counts_for_doi(doi)
        if gt_counts is None:
            print(f"  Error: Ground truth steps JSON not found for DOI {doi}")
            continue

        # CCDC-anchored order/type scoring
        gt_json_path = STEPS_GT_DIR / f"{doi}.json"
        try:
            gt_obj = json.loads(gt_json_path.read_text(encoding="utf-8"))
        except Exception:
            gt_obj = {}

        if previous:
            prev_path = Path("previous_work/steps") / f"{doi}.json"
            if not prev_path.exists():
                print(f"  Error: Previous work steps JSON not found for DOI {doi}")
                continue
            try:
                pred_obj = json.loads(prev_path.read_text(encoding="utf-8"))
            except Exception:
                pred_obj = {}
            pred_map = _map_steps_by_ccdc_from_gt_like(pred_obj)
            gt_map = _map_steps_by_ccdc_from_gt_like(gt_obj)
        else:
            res_dir = MERGED_TLL_DIR / h if h else MERGED_TLL_DIR / (d2h.get(doi, ""))
            res_path = res_dir / "steps.json"
            pred_map: Dict[str, List[str]] = {}
            if res_path.exists():
                try:
                    pred_obj = json.loads(res_path.read_text(encoding="utf-8"))
                    pred_map = _map_steps_by_ccdc_from_pred_result(pred_obj)
                except Exception:
                    pred_map = {}
            gt_map = _map_steps_by_ccdc_from_gt_like(gt_obj)

        tp, fp, fn = _score_ordered_maps(gt_map, pred_map)
        prec = tp / (tp + fp) * 100.0 if (tp + fp) else 0.0
        rec = tp / (tp + fn) * 100.0 if (tp + fn) else 0.0
        f1 = (2 * prec * rec / (prec + rec)) if (prec + rec) else 0.0

        ok = (fp == 0 and fn == 0)

        # Also retain simple count comparison using iter3 hints when not in previous mode
        if not previous and h:
            hash_dir = DATA_DIR / h
            pred_counts = _predicted_step_counts(hash_dir)
            ok_counts, sa, sb = _compare(pred_counts, gt_counts)
            print(f"  Predicted (sorted): {sa}")
            print(f"  Ground truth (sorted): {sb}")
            print(f"  Result: {'OK MATCH' if ok_counts else 'X MISMATCH'}")

        print(f"  CCDC-anchored step-type-by-order scoring: TP={tp} FP={fp} FN={fn} | P={prec:.1f}% R={rec:.1f}% F1={f1:.1f}%")

        # Write per-DOI markdown report with GT and all iter3 hints contents
        try:
            OUT_MD_DIR.mkdir(parents=True, exist_ok=True)
            md_path = OUT_MD_DIR / f"{doi}.md"
            lines: List[str] = []
            lines.append(f"# {doi} ({h})\n")
            lines.append("\n")
            lines.append("## Summary\n")
            if not previous and h:
                lines.append(f"- Iter3 Predicted (sorted): {sa}\n")
                lines.append(f"- Ground truth (sorted): {sb}\n")
                lines.append(f"- Counts Result: {'OK MATCH' if ok else 'X MISMATCH'}\n")
            lines.append(f"- CCDC-anchored order scoring: TP={tp} FP={fp} FN={fn} | P={prec:.1f}% R={rec:.1f}% F1={f1:.1f}%\n")
            lines.append("\n")

            # Ground truth JSON content
            gt_json_path = STEPS_GT_DIR / f"{doi}.json"
            lines.append(f"## Ground truth ({gt_json_path.as_posix()})\n")
            try:
                gt_text = gt_json_path.read_text(encoding="utf-8")
            except Exception:
                gt_text = ""
            lines.append("```json\n")
            lines.append(gt_text)
            if not gt_text.endswith("\n"):
                lines.append("\n")
            lines.append("```\n\n")

            # iter3 hints contents (only in default mode when hash present)
            if not previous and h:
                hash_dir = DATA_DIR / h
                hint_files = _iter3_hint_files(hash_dir)
                lines.append(f"## iter3 hints files ({len(hint_files)}) in { (hash_dir / 'mcp_run').as_posix() }\n\n")
                for fpath in hint_files:
                    try:
                        content = fpath.read_text(encoding="utf-8", errors="replace")
                    except Exception:
                        content = ""
                    steps_in_file = _count_steps_in_iter3_file(fpath)
                    lines.append(f"### {fpath.name} (steps: {steps_in_file})\n")
                    lines.append("```text\n")
                    lines.append(content)
                    if not content.endswith("\n"):
                        lines.append("\n")
                    lines.append("```\n\n")

            md_path.write_text("".join(lines), encoding="utf-8")
        except Exception:
            # Continue even if report writing fails
            pass
        if ok:
            matches += 1
        else:
            mismatches += 1

    print("\nSummary:")
    print(f"  matches={matches} mismatches={mismatches} total={matches + mismatches}")
    return 0


def main():
    p = argparse.ArgumentParser(description="Development check: steps vs ground truth (counts and CCDC-anchored order scoring)")
    p.add_argument("--file", dest="file", type=str, required=False, help="Target by DOI or hash; default all")
    p.add_argument("--previous", action="store_true", help="Compare previous_work/steps/<doi>.json against ground truth using CCDC anchoring and order/type scoring")
    args = p.parse_args()
    raise SystemExit(run(args.file, args.previous))


if __name__ == "__main__":
    main()
