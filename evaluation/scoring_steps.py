import json
from pathlib import Path
from typing import Dict, List, Tuple
from evaluation.utils.scoring_common import score_lists, precision_recall_f1, render_report, hash_map_reverse


def main() -> None:
    GT_ROOT = Path("earlier_ground_truth/steps")
    RES_ROOT = Path("evaluation/data/merged_tll")
    OUT_ROOT = Path("evaluation/data/result/steps")
    OUT_ROOT.mkdir(parents=True, exist_ok=True)

    hash_to_doi = hash_map_reverse(Path("data/doi_to_hash.json"))
    hashes = sorted([p.name for p in RES_ROOT.iterdir() if p.is_dir()])

    rows: List[Tuple[str, Tuple[int, int, int, float, float, float]]] = []
    for hv in hashes:
        doi = hash_to_doi.get(hv)
        res_path = RES_ROOT / hv / "steps.json"
        if not doi or not res_path.exists():
            continue
        gt_path = GT_ROOT / f"{doi}.json"
        if not gt_path.exists():
            continue
        gt = json.loads(gt_path.read_text(encoding="utf-8"))
        res = json.loads(res_path.read_text(encoding="utf-8"))

        gt_list: List[str] = []
        for synth in gt.get("Synthesis", []):
            for step in synth.get("steps", []):
                if "Add" in step: gt_list.append("Add")
                if "HeatChill" in step: gt_list.append("HeatChill")
                if "Filter" in step: gt_list.append("Filter")
                if "Sonicate" in step: gt_list.append("Sonicate")

        res_list: List[str] = []
        for st in res.get("steps", []):
            res_list.append(st.get("stepName") or "")

        tp, fp, fn = score_lists(gt_list, res_list)
        prec, rec, f1 = precision_recall_f1(tp, fp, fn)
        rows.append((hv, (tp, fp, fn, prec, rec, f1)))

        report = render_report(f"Steps Scoring - {hv}", [(hv, (tp, fp, fn, prec, rec, f1))])
        (OUT_ROOT / f"{hv}.md").write_text(report, encoding="utf-8")

    overall = render_report("Steps Scoring - Overall", rows)
    (OUT_ROOT / "_overall.md").write_text(overall, encoding="utf-8")
    print((OUT_ROOT / "_overall.md").resolve())


if __name__ == "__main__":
    main()


