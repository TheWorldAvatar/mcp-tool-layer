#!/usr/bin/env python3
"""
Basic comparison and completeness report per hash directory under data/.

Outputs:
- evaluation/basic_comparison.csv: one row per hash with requested metrics
- evaluation/basic_comparison_summary.md: concise summary of completed hashes

Requested metrics per hash H (example: 1b9180ec):
1) top_entities_count (n): number of top entities in mcp_run/iter1_top_entities.json
2) iterX_txt_count: for each iteration X, number of .txt files in mcp_run/ matching prefix iterX_
3) root_ttl_count: number of .ttl files in the hash root (excluding output_top.ttl)
4) ontomops_ttl_count, ontospecies_ttl_count: number of .ttl files in ontomops_output/ and ontospecies_output/
   - ontospecies_ccdc_valid_count: among ontospecies_output .ttl, count files that contain a valid CCDC number
     (heuristic: file contains 'hasCCDCNumberValue' with a value not equal to 'N/A')
5) cbu_integrated_json_count: number of integrated JSON files in cbu_derivation/integrated/
   - cbu_empty_metal_count: count of integrated JSONs where metal_cbu == ""
   - cbu_empty_organic_count: count of integrated JSONs where organic_cbu == ""

Completion criterion (must also satisfy n > 0):
 - iter1_txt_count == 1
 - root_ttl_count == n
 - cbu_integrated_json_count == n
 - ontomops_ttl_count <= n
 - ontospecies_ttl_count <= n
CCDC validity and empty CBU checks do not affect completion.
"""

from __future__ import annotations

import csv
import json
import os
import re
from pathlib import Path
from typing import Dict, List, Tuple


REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = REPO_ROOT / "data"
OUT_DIR = REPO_ROOT / "evaluation"
CSV_PATH = OUT_DIR / "basic_comparison.csv"
MD_PATH = OUT_DIR / "basic_comparison_summary.md"


def load_top_entities_count(hash_dir: Path) -> int:
    iter1_path = hash_dir / "mcp_run" / "iter1_top_entities.json"
    if not iter1_path.exists():
        return 0
    try:
        with iter1_path.open("r", encoding="utf-8") as f:
            arr = json.load(f)
        return len(arr) if isinstance(arr, list) else 0
    except Exception:
        return 0


def count_mcp_run_txt_by_iter(hash_dir: Path) -> Dict[str, int]:
    """Return mapping like {"iter1": 9, "iter2": 7}.
    We look for files under mcp_run/ whose filename starts with e.g. iter1_ and ends with .txt
    """
    result: Dict[str, int] = {}
    mcp_dir = hash_dir / "mcp_run"
    if not mcp_dir.exists():
        return result
    for fp in mcp_dir.glob("*.txt"):
        name = fp.name
        m = re.match(r"^(iter\d+)_", name, flags=re.IGNORECASE)
        if m:
            key = m.group(1).lower()
            result[key] = result.get(key, 0) + 1
    return result


def count_root_ttl(hash_dir: Path) -> int:
    count = 0
    for fp in hash_dir.glob("*.ttl"):
        if fp.name.lower() == "output_top.ttl":
            continue
        count += 1
    return count


def count_ttl_in_folder(folder: Path) -> int:
    if not folder.exists():
        return 0
    return sum(1 for _ in folder.glob("*.ttl"))


def count_ccdc_valid_in_species(folder: Path) -> int:
    """Legacy helper, not used now."""
    if not folder.exists():
        return 0
    valid = 0
    for fp in folder.glob("*.ttl"):
        try:
            text = fp.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        # Find any line like ontospecies:hasCCDCNumberValue "..."
        for m in re.finditer(r"hasCCDCNumberValue\s+\"([^\"]+)\"", text, flags=re.IGNORECASE):
            val = m.group(1).strip()
            if val and val.upper() != "N/A":
                valid += 1
                break
    return valid


def count_ccdc_valid_in_ontomops(folder: Path) -> int:
    """A TTL has valid CCDC in OntoMOPs if it has ontomops:hasCCDCNumber with a non-empty value not equal to 'N/A'."""
    if not folder.exists():
        return 0
    valid = 0
    for fp in folder.glob("*.ttl"):
        try:
            text = fp.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for m in re.finditer(r"ontomops:hasCCDCNumber\s+\"([^\"]+)\"", text, flags=re.IGNORECASE):
            val = m.group(1).strip()
            if val and val.upper() != "N/A":
                valid += 1
                break
    return valid


def count_cbu_integrated(hash_dir: Path) -> Tuple[int, int, int]:
    """Return (m_cbu, empty_metal_cbu_count, empty_organic_cbu_count)."""
    integrated = hash_dir / "cbu_derivation" / "integrated"
    if not integrated.exists():
        return (0, 0, 0)
    m_cbu = 0
    empty_metal = 0
    empty_organic = 0
    for fp in integrated.glob("*.json"):
        m_cbu += 1
        try:
            data = json.loads(fp.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            continue
        # JSONs can be dicts or arrays; if array, check first item
        obj = data[0] if isinstance(data, list) and data else data
        if isinstance(obj, dict):
            if obj.get("metal_cbu", None) == "":
                empty_metal += 1
            if obj.get("organic_cbu", None) == "":
                empty_organic += 1
    return (m_cbu, empty_metal, empty_organic)


def find_hash_dirs() -> List[Path]:
    if not DATA_ROOT.exists():
        return []
    out: List[Path] = []
    for p in DATA_ROOT.iterdir():
        if not p.is_dir():
            continue
        # Consider only directories that look like a hash root (contain mcp_run or ontomops_output, etc.)
        if (p / "mcp_run").exists() or (p / "ontomops_output").exists() or (p / "ontospecies_output").exists():
            out.append(p)
    return sorted(out)


def _load_new_hashes() -> set[str]:
    """Identify hashes corresponding to DOIs listed in 3_new_papers/doi.txt using data/doi_to_hash.json.
    Returns a set of hash strings flagged as new.
    """
    new_hashes: set[str] = set()
    doi_file = REPO_ROOT / "3_new_papers" / "doi.txt"
    mapping_path = REPO_ROOT / "data" / "doi_to_hash.json"
    if not doi_file.exists() or not mapping_path.exists():
        return new_hashes
    try:
        lines = [ln.strip() for ln in doi_file.read_text(encoding="utf-8", errors="ignore").splitlines() if ln.strip()]
    except Exception:
        lines = []
    try:
        mapping = json.loads(mapping_path.read_text(encoding="utf-8", errors="ignore")) or {}
    except Exception:
        mapping = {}
    for ln in lines:
        token = ln
        # Extract DOI-like token if line contains extra text
        m = re.search(r"\b10\.[^\s]+", ln, flags=re.IGNORECASE)
        if m:
            token = m.group(0)
        # Consider both slash and underscore forms
        doi_slash = token.replace('_', '/')
        doi_us = token.replace('/', '_')
        for key in {doi_slash, doi_us}:
            if key in mapping:
                h = str(mapping.get(key, ""))
                if h:
                    new_hashes.add(h)
    return new_hashes


def _load_source_hash_sets() -> Dict[str, set[str]]:
    """Load hash id sets for curated sources: earlier, gao, lu, sun.
    Uses REPO_ROOT/earlier_ground_truth/earlier_ground_truth_list.txt and
    REPO_ROOT/newer_ground_truth_{gao,lu,sun}/prepared/cbu/*.json to infer DOIs,
    then maps DOIs to hashes via data/doi_to_hash.json.
    """
    mapping_path = REPO_ROOT / "data" / "doi_to_hash.json"
    try:
        mapping: Dict[str, str] = json.loads(mapping_path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        mapping = {}

    def map_dois_to_hashes(dois: List[str]) -> set[str]:
        out: set[str] = set()
        for d in dois:
            token = d.strip()
            if not token:
                continue
            # Normalize possible forms
            doi_us = token
            doi_slash = token.replace('_', '/')
            for key in {doi_us, doi_slash}:
                h = str(mapping.get(key, ""))
                if h:
                    out.add(h)
        return out

    # earlier: one DOI per line (filename-like), strip optional .json
    earlier_list = REPO_ROOT / "earlier_ground_truth" / "earlier_ground_truth_list.txt"
    earlier_dois: List[str] = []
    if earlier_list.exists():
        try:
            for ln in earlier_list.read_text(encoding="utf-8", errors="ignore").splitlines():
                ln = ln.strip()
                if not ln:
                    continue
                if ln.lower().endswith('.json'):
                    ln = ln[:-5]
                earlier_dois.append(ln)
        except Exception:
            pass

    # gao/lu/sun: prefer prepared/cbu/*.json filenames (stem is DOI key)
    def load_prepared_cbu(folder_name: str) -> List[str]:
        root = REPO_ROOT / folder_name / "prepared" / "cbu"
        dois: List[str] = []
        if root.exists():
            for fp in sorted(root.glob("*.json")):
                dois.append(fp.stem)
        return dois

    gao_dois = load_prepared_cbu("newer_ground_truth_gao")
    lu_dois = load_prepared_cbu("newer_ground_truth_lu")
    sun_dois = load_prepared_cbu("newer_ground_truth_sun")

    label_sets: Dict[str, set[str]] = {
        "earlier": map_dois_to_hashes(earlier_dois),
        "gao": map_dois_to_hashes(gao_dois),
        "lu": map_dois_to_hashes(lu_dois),
        "sun": map_dois_to_hashes(sun_dois),
    }
    return label_sets


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    rows: List[Dict[str, object]] = []
    all_iter_columns: set[str] = set()

    for hash_dir in find_hash_dirs():
        hash_id = hash_dir.name
        n = load_top_entities_count(hash_dir)
        iter_counts = count_mcp_run_txt_by_iter(hash_dir)
        for iter_key in iter_counts.keys():
            all_iter_columns.add(f"{iter_key}_txt_count")
        root_ttl_count = count_root_ttl(hash_dir)
        ontomops_ttl_count = count_ttl_in_folder(hash_dir / "ontomops_output")
        ontospecies_ttl_count = count_ttl_in_folder(hash_dir / "ontospecies_output")
        ontospecies_ccdc_valid_count = count_ccdc_valid_in_ontomops(hash_dir / "ontomops_output")
        cbu_integrated_json_count, cbu_empty_metal_count, cbu_empty_organic_count = count_cbu_integrated(hash_dir)

        row: Dict[str, object] = {
            "hash": hash_id,
            "top_entities_count": n,
            "root_ttl_count": root_ttl_count,
            "ontomops_ttl_count": ontomops_ttl_count,
            "ontospecies_ttl_count": ontospecies_ttl_count,
            "ontospecies_ccdc_valid_count": ontospecies_ccdc_valid_count,
            "cbu_integrated_json_count": cbu_integrated_json_count,
            "cbu_empty_metal_count": cbu_empty_metal_count,
            "cbu_empty_organic_count": cbu_empty_organic_count,
        }
        # Fill per-iter counts
        for ik, count in iter_counts.items():
            row[f"{ik}_txt_count"] = count

        rows.append(row)

    # Determine CSV columns
    base_cols = [
        "hash",
        "top_entities_count",
    ]
    iter_cols = sorted(all_iter_columns, key=lambda x: (len(x), x))
    rest_cols = [
        "root_ttl_count",
        "ontomops_ttl_count",
        "ontospecies_ttl_count",
        "ontospecies_ccdc_valid_count",
        "cbu_integrated_json_count",
        "cbu_empty_metal_count",
        "cbu_empty_organic_count",
    ]
    fieldnames = base_cols + iter_cols + rest_cols

    # Write CSV (exclude not started: top_entities_count == 0)
    with CSV_PATH.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            if int(r.get("top_entities_count", 0)) <= 0:
                continue
            for col in iter_cols:
                r.setdefault(col, 0)
            writer.writerow(r)

    # Compute completion summary
    not_started: List[Dict[str, str]] = []
    started_rows: List[Dict[str, object]] = []
    new_hashes = _load_new_hashes()
    source_label_sets = _load_source_hash_sets()
    # Force-add manual 'new' hashes
    new_hashes.add("9f13ab77")
    earlier_set = source_label_sets.get("earlier", set())
    later_set = (
        source_label_sets.get("gao", set())
        | source_label_sets.get("lu", set())
        | source_label_sets.get("sun", set())
    )
    denote_order = {"later": 0, "earlier": 1, "none": 2}
    for r in rows:
        n = int(r.get("top_entities_count", 0))
        if n == 0:
            h = str(r.get("hash", ""))
            labels: List[str] = []
            if h in new_hashes:
                labels.append("new")
            denote = "earlier" if h in earlier_set else ("later" if h in later_set else "none")
            if denote != "none":
                labels.append(denote)
            display = f"{h} ({', '.join(labels)})" if labels else h
            not_started.append({"display": display, "denote": denote, "hash_id": h})
            continue
        # Determine completion status by new rules
        iter1_ok = int(r.get("iter1_txt_count", 0)) == 1
        root_ok = int(r.get("root_ttl_count", 0)) == n
        cbu_ok = int(r.get("cbu_integrated_json_count", 0)) == n
        mop_ok = int(r.get("ontomops_ttl_count", 0)) <= n
        species_ok = int(r.get("ontospecies_ttl_count", 0)) <= n
        completed = iter1_ok and root_ok and cbu_ok and mop_ok and species_ok
        r["status"] = "Completed" if completed else "In Progress"
        # Annotate new hash
        h = str(r.get("hash", ""))
        labels: List[str] = []
        if h in new_hashes:
            labels.append("new")
        denote = "earlier" if h in earlier_set else ("later" if h in later_set else "none")
        if denote != "none":
            labels.append(denote)
        if labels:
            r["hash"] = f"{h} ({', '.join(labels)})"
        # Metadata for sorting in Markdown (added after CSV write stage)
        r["_denote"] = denote
        r["_hash_id"] = h
        started_rows.append(r)

    # Write Markdown summary
    with MD_PATH.open("w", encoding="utf-8", newline="\n") as f:
        f.write("# Basic Comparison Summary\n\n")
        # Not Started table
        f.write(f"## Not Started ({len(not_started)})\n\n")
        if not_started:
            f.write("| Hash |\n|---|\n")
            for item in sorted(not_started, key=lambda x: (denote_order.get(x.get("denote", "none"), 99), x.get("hash_id", ""))):
                f.write(f"| {item.get('display', '')} |\n")
        else:
            f.write("(none)\n")
        # Started tables with status
        f.write(f"\n## Started ({len(started_rows)})\n\n")
        if started_rows:
            # Part 1: up to ontospecies_ttl_count
            cols1 = [
                "hash",
                "status",
                "top_entities_count",
                "iter1_txt_count",
                "root_ttl_count",
                "ontomops_ttl_count",
                "ontospecies_ttl_count",
            ]
            f.write("| " + " | ".join(cols1) + " |\n")
            f.write("|" + "|".join(["---"] * len(cols1)) + "|\n")
            for r in sorted(started_rows, key=lambda x: (denote_order.get(str(x.get("_denote", "none")), 99), str(x.get("_hash_id", str(x.get("hash", "")))))):
                f.write("| " + " | ".join(str(r.get(c, "")) for c in cols1) + " |\n")

            # Part 2: from cbu_integrated_json_count onwards
            f.write("\n")
            cols2 = [
                "hash",
                "status",
                "cbu_integrated_json_count",
                "cbu_empty_metal_count",
                "cbu_empty_organic_count",
            ]
            f.write("| " + " | ".join(cols2) + " |\n")
            f.write("|" + "|".join(["---"] * len(cols2)) + "|\n")
            for r in sorted(started_rows, key=lambda x: (denote_order.get(str(x.get("_denote", "none")), 99), str(x.get("_hash_id", str(x.get("hash", "")))))):
                f.write("| " + " | ".join(str(r.get(c, "")) for c in cols2) + " |\n")

            # Part 3: CCDC validity (separate table, started only)
            f.write("\n")
            cols3 = [
                "hash",
                "status",
                "ontospecies_ccdc_valid_count",
            ]
            f.write("| " + " | ".join(cols3) + " |\n")
            f.write("|" + "|".join(["---"] * len(cols3)) + "|\n")
            for r in sorted(started_rows, key=lambda x: (denote_order.get(str(x.get("_denote", "none")), 99), str(x.get("_hash_id", str(x.get("hash", "")))))):
                f.write("| " + " | ".join(str(r.get(c, "")) for c in cols3) + " |\n")
        else:
            f.write("(none)\n")

    # Write hash -> category mapping for started rows only (earlier/later)
    category_map_path = OUT_DIR / "hash_category.json"
    category_map: Dict[str, str] = {}
    for r in started_rows:
        h = str(r.get("_hash_id", str(r.get("hash", ""))))
        denote = str(r.get("_denote", "none"))
        if denote in {"earlier", "later"} and h:
            category_map[h] = denote
    with category_map_path.open("w", encoding="utf-8", newline="\n") as fjson:
        json.dump(category_map, fjson, indent=2, sort_keys=True)

    print(f"Wrote CSV: {CSV_PATH}")
    print(f"Wrote summary: {MD_PATH}")
    print(f"Wrote categories: {category_map_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
