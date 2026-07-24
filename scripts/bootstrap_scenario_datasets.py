#!/usr/bin/env python3
"""Bootstrap scenarios/*/datasets from legacy raw_data* folders (copy, never overwrite)."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Known MOP eval pairs currently on disk (main stem -> hash)
KNOWN_MOP_PAIRS: list[dict[str, str]] = [
    {
        "doi": "10.1002/anie.201811027",
        "stem": "10.1002_anie.201811027",
        "hash": "a014d993",
    },
    {
        "doi": "10.1002/anie.202010824",
        "stem": "10.1002_anie.202010824",
        "hash": "88c21a74",
    },
    {
        "doi": "10.1021/acsami.7b18836",
        "stem": "10.1021_acsami.7b18836",
        "hash": "0c57bac8",
    },
]


def _copy_file(src: Path, dst: Path) -> str:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        return "exists"
    shutil.copy2(src, dst)
    return "copied"


def bootstrap_medical_eval30() -> dict:
    src_dir = ROOT / "raw_data_new_medical"
    dst_dir = ROOT / "scenarios" / "medical" / "datasets" / "eval30"
    dst_dir.mkdir(parents=True, exist_ok=True)
    mapping_path = ROOT / "data_medical_new_cases" / "doi_to_hash.json"
    doi_to_hash = {}
    if mapping_path.exists():
        doi_to_hash = json.loads(mapping_path.read_text(encoding="utf-8"))

    cases = []
    copied = 0
    for pdf in sorted(src_dir.glob("*.pdf")):
        status = _copy_file(pdf, dst_dir / pdf.name)
        if status == "copied":
            copied += 1
        case_id = pdf.stem
        cases.append(
            {
                "case_id": case_id,
                "filename": pdf.name,
                "hash": doi_to_hash.get(case_id, ""),
                "split": "eval30",
                "status": "present",
            }
        )

    gt_src = src_dir / "Ground truth.xlsx"
    if gt_src.exists():
        _copy_file(gt_src, dst_dir / gt_src.name)

    manifest = {
        "domain": "medical",
        "dataset": "eval30",
        "description": "30 operative-report evaluation cases",
        "n_expected": 30,
        "n_present": len(cases),
        "cases": cases,
    }
    (ROOT / "scenarios" / "medical" / "datasets" / "manifest_eval30.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return {"copied_pdfs": copied, "n_cases": len(cases)}


def bootstrap_medical_dev5() -> dict:
    dst_dir = ROOT / "scenarios" / "medical" / "datasets" / "dev5"
    dst_dir.mkdir(parents=True, exist_ok=True)
    sources = [
        ROOT / "medical_case",
        ROOT / "raw_data",
    ]
    mapping_path = ROOT / "data" / "doi_to_hash.json"
    doi_to_hash = {}
    if mapping_path.exists():
        doi_to_hash = json.loads(mapping_path.read_text(encoding="utf-8"))

    cases = []
    copied = 0
    for i in range(1, 6):
        name = f"OP Bericht {i}.pdf"
        src = None
        for base in sources:
            cand = base / name
            if cand.is_file():
                src = cand
                break
        if src is None:
            cases.append(
                {
                    "case_id": f"OP Bericht {i}",
                    "filename": name,
                    "hash": doi_to_hash.get(f"OP Bericht {i}", ""),
                    "split": "dev5",
                    "status": "missing",
                }
            )
            continue
        status = _copy_file(src, dst_dir / name)
        if status == "copied":
            copied += 1
        cases.append(
            {
                "case_id": f"OP Bericht {i}",
                "filename": name,
                "hash": doi_to_hash.get(f"OP Bericht {i}", ""),
                "split": "dev5",
                "status": "present",
            }
        )

    manifest = {
        "domain": "medical",
        "dataset": "dev5",
        "description": "5 development operative reports",
        "n_expected": 5,
        "n_present": sum(1 for c in cases if c["status"] == "present"),
        "cases": cases,
    }
    (ROOT / "scenarios" / "medical" / "datasets" / "manifest_dev5.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    # Combined medical manifest
    eval30_path = ROOT / "scenarios" / "medical" / "datasets" / "manifest_eval30.json"
    combined = {"domain": "medical", "datasets": {}}
    if eval30_path.exists():
        combined["datasets"]["eval30"] = json.loads(eval30_path.read_text(encoding="utf-8"))
    combined["datasets"]["dev5"] = manifest
    (ROOT / "scenarios" / "medical" / "datasets" / "manifest.json").write_text(
        json.dumps(combined, indent=2) + "\n", encoding="utf-8"
    )
    return {"copied_pdfs": copied, "n_present": manifest["n_present"]}


def bootstrap_mops_eval30() -> dict:
    src_dirs = [ROOT / "raw_data_mop", ROOT / "raw_data_mops", ROOT / "raw_data"]
    dst_dir = ROOT / "scenarios" / "mops" / "datasets" / "eval30"
    dst_dir.mkdir(parents=True, exist_ok=True)

    pairs: list[dict] = []
    copied = 0

    for known in KNOWN_MOP_PAIRS:
        stem = known["stem"]
        main_name = f"{stem}.pdf"
        si_name = f"{stem}_si.pdf"
        main_src = next((d / main_name for d in src_dirs if (d / main_name).is_file()), None)
        si_src = next((d / si_name for d in src_dirs if (d / si_name).is_file()), None)
        status = "present" if main_src else "missing"
        if main_src:
            if _copy_file(main_src, dst_dir / main_name) == "copied":
                copied += 1
        if si_src:
            if _copy_file(si_src, dst_dir / si_name) == "copied":
                copied += 1
        elif status == "present":
            status = "present_main_only"
        pairs.append(
            {
                "slot": len(pairs) + 1,
                "doi": known["doi"],
                "stem": stem,
                "main_pdf": main_name,
                "si_pdf": si_name,
                "hash": known["hash"],
                "status": status,
            }
        )

    # Pad to 30 slots with missing placeholders
    while len(pairs) < 30:
        slot = len(pairs) + 1
        pairs.append(
            {
                "slot": slot,
                "doi": "",
                "stem": "",
                "main_pdf": "",
                "si_pdf": "",
                "hash": "",
                "status": "missing",
                "note": "Fill DOI/stem and drop main+SI PDFs into eval30/, then update this slot.",
            }
        )

    manifest = {
        "domain": "mops",
        "dataset": "eval30",
        "description": "30 chemistry paper pairs (main PDF + SI PDF)",
        "n_expected": 30,
        "n_present": sum(1 for p in pairs if p["status"] in ("present", "present_main_only")),
        "n_missing": sum(1 for p in pairs if p["status"] == "missing"),
        "pairs": pairs,
    }
    out = ROOT / "scenarios" / "mops" / "datasets" / "manifest.json"
    out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return {
        "copied_files": copied,
        "n_present": manifest["n_present"],
        "n_missing": manifest["n_missing"],
    }


def main() -> int:
    print("[INFO] Bootstrapping scenario datasets (copy-if-missing)...")
    med30 = bootstrap_medical_eval30()
    print(f"  medical/eval30: {med30}")
    med5 = bootstrap_medical_dev5()
    print(f"  medical/dev5: {med5}")
    mop = bootstrap_mops_eval30()
    print(f"  mops/eval30: {mop}")
    print("[OK] Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
