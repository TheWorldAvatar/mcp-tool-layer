import json
from pathlib import Path
from typing import Dict, List, Tuple
from evaluation.utils.scoring_common import score_lists, precision_recall_f1, render_report, hash_map_reverse


def main() -> None:
    GT_ROOT = Path("earlier_ground_truth/characterisation")
    RES_ROOT = Path("evaluation/data/merged_tll")
    OUT_ROOT = Path("evaluation/data/result/characterisation")
    OUT_ROOT.mkdir(parents=True, exist_ok=True)

    hash_to_doi = hash_map_reverse(Path("data/doi_to_hash.json"))
    hashes = sorted([p.name for p in RES_ROOT.iterdir() if p.is_dir()])

    rows: List[Tuple[str, Tuple[int, int, int, float, float, float]]] = []
    for hv in hashes:
        doi = hash_to_doi.get(hv)
        res_path = RES_ROOT / hv / "characterisation.json"
        if not doi or not res_path.exists():
            continue
        gt_path = GT_ROOT / f"{doi}.json"
        if not gt_path.exists():
            continue
        gt = json.loads(gt_path.read_text(encoding="utf-8"))
        res = json.loads(res_path.read_text(encoding="utf-8"))

        gt_list: List[str] = []
        for device in gt.get("Devices", []):
            for char in device.get("Characterisation", []):
                gt_list.append(char.get("productCCDCNumber") or "")

        res_list: List[str] = []
        for ch in res.get("characterisations", []):
            res_list.append(ch.get("productCCDCNumber") or "")

        tp, fp, fn = score_lists(gt_list, res_list)
        prec, rec, f1 = precision_recall_f1(tp, fp, fn)
        rows.append((hv, (tp, fp, fn, prec, rec, f1)))

        report = render_report(f"Characterisation Scoring - {hv}", [(hv, (tp, fp, fn, prec, rec, f1))])
        (OUT_ROOT / f"{hv}.md").write_text(report, encoding="utf-8")

    overall = render_report("Characterisation Scoring - Overall", rows)
    (OUT_ROOT / "_overall.md").write_text(overall, encoding="utf-8")
    print((OUT_ROOT / "_overall.md").resolve())


if __name__ == "__main__":
    main()


