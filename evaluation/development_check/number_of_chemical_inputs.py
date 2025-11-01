#!/usr/bin/env python3
"""
Script to count chemical inputs in iter2 hint files and compare with ground truth data.

Default mode:
  - Reads mcp_run/iter2_hints_*.txt per hash

Test mode (--test):
  - Reads data/<hash>/iter2_test_results/iter2_hints_<entity>_<n>.txt per hash
  - Produces per-test scores across available numbered runs

Both modes:
  - Prints console summary and writes a markdown report next to this script
  - Includes the model (iter2_hints) from configs/extraction_models.json
"""

import os
import argparse
import json
import re
from pathlib import Path
from collections import defaultdict

def count_chemical_inputs_in_file(file_path):
    """Count chemical inputs in an iter2 hints file.

    We only count occurrences of '- Name:' that appear under a 'ChemicalInput' block,
    to avoid counting names from other sections (e.g., ChemicalOutput).
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.read().splitlines()

        in_input_block = False
        count = 0
        for raw in lines:
            line = raw.rstrip('\n')
            stripped = line.strip()

            # Enter input block
            if re.match(r'^\s*ChemicalInputs?:\s*$', line):
                in_input_block = True
                continue

            # Detect start of a new top-level section; exit input block
            if in_input_block and re.match(r'^\s*[A-Za-z][A-Za-z0-9_\- ]*:\s*$', line) and not re.match(r'^\s*\-', line):
                # e.g., 'ChemicalOutput:' or 'Context:'
                in_input_block = False
                # fall through (do not count on this line)

            # Count only within input block
            if in_input_block and re.match(r'^\s*\-\s+Name:', line):
                count += 1

        return count
    except Exception as e:
        print(f"Error reading {file_path}: {e}")
        return 0

def get_ground_truth_chemical_counts(doi):
    """Get chemical input counts from ground truth data for a given DOI."""
    # Convert DOI to filename format
    doi_filename = doi.replace("/", "_") + ".json"
    gt_file = Path("earlier_ground_truth/chemicals1") / doi_filename
    
    if not gt_file.exists():
        return None
    
    try:
        with open(gt_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # Extract chemical input counts for each procedure
        procedure_counts = []
        for procedure in data.get("synthesisProcedures", []):
            for step in procedure.get("steps", []):
                input_chemicals = step.get("inputChemicals", [])
                procedure_counts.append(len(input_chemicals))
        
        return procedure_counts
    except Exception as e:
        print(f"Error reading ground truth file {gt_file}: {e}")
        return None

def get_doi_from_hash(hash_value):
    """Get DOI from hash using doi_to_hash.json mapping."""
    try:
        with open("data/doi_to_hash.json", 'r') as f:
            doi_to_hash = json.load(f)
        
        # Find DOI for this hash
        for doi, h in doi_to_hash.items():
            if h == hash_value:
                return doi
        return None
    except Exception as e:
        print(f"Error reading doi_to_hash.json: {e}")
        return None

def _load_iter2_model_name() -> str:
    try:
        cfg_path = Path("configs") / "extraction_models.json"
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        name = str(data.get("iter2_hints", "")).strip()
        return name if name else "unknown"
    except Exception:
        return "unknown"


def _write_markdown_default(rows: list[dict], totals: dict, out_path: Path):
    lines = []
    lines.append("# Iter2 Chemical Inputs — Default Mode")
    lines.append("")
    lines.append(f"- Model (iter2_hints): {_load_iter2_model_name()}")
    lines.append("")
    lines.append("| Hash | DOI | Extracted counts | GT counts | Match |")
    lines.append("|------|-----|------------------|-----------|-------|")
    for r in rows:
        lines.append(f"| {r['hash']} | {r['doi']} | {r['extracted']} | {r['gt']} | {r['match']} |")
    lines.append("")
    lines.append(f"Total files analyzed: {totals['files']}")
    lines.append(f"Perfect matches: {totals['matches']}")
    lines.append(f"Mismatches: {totals['mismatches']}")
    lines.append(f"Match rate: {totals['rate']:.1f}%")
    out_path.write_text("\n".join(lines), encoding="utf-8")


def _write_markdown_test(rows_by_test: dict[str, list[dict]], totals_by_test: dict[str, dict], out_path: Path):
    lines = []
    lines.append("# Iter2 Chemical Inputs — Test Mode")
    lines.append("")
    lines.append(f"- Model (iter2_hints): {_load_iter2_model_name()}")
    lines.append("")
    for test_num in sorted(rows_by_test.keys(), key=lambda x: int(x)):
        rows = rows_by_test[test_num]
        totals = totals_by_test[test_num]
        lines.append(f"## Test {test_num}")
        lines.append("")
        lines.append("| Hash | DOI | Extracted counts | GT counts | Match |")
        lines.append("|------|-----|------------------|-----------|-------|")
        for r in rows:
            lines.append(f"| {r['hash']} | {r['doi']} | {r['extracted']} | {r['gt']} | {r['match']} |")
        lines.append("")
        lines.append(f"Total files analyzed: {totals['files']}")
        lines.append(f"Perfect matches: {totals['matches']}")
        lines.append(f"Mismatches: {totals['mismatches']}")
        lines.append(f"Match rate: {totals['rate']:.1f}%")
        lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")


def analyze_chemical_inputs(test_mode: bool = False):
    """Main analysis function."""
    data_dir = Path("data")
    
    if not data_dir.exists():
        print("Error: data directory not found")
        return
    
    # Get all hash folders
    hash_folders = [item for item in data_dir.iterdir() if item.is_dir() and not item.name.startswith('.')]
    
    print(f"Found {len(hash_folders)} hash folders to analyze")
    print("=" * 80)
    
    total_files = 0
    total_matches = 0
    total_mismatches = 0
    rows_default: list[dict] = []
    rows_by_test: dict[str, list[dict]] = {}
    
    for hash_folder in hash_folders:
        mcp_run_path = hash_folder / "mcp_run"
        
        if not mcp_run_path.exists():
            print(f"Skipping {hash_folder.name}: No mcp_run folder")
            continue
        
        # Get DOI for this hash
        doi = get_doi_from_hash(hash_folder.name)
        if not doi:
            print(f"Skipping {hash_folder.name}: DOI not found")
            continue
        
        print(f"\nAnalyzing {hash_folder.name} (DOI: {doi})")
        print("-" * 60)
        
        # Get ground truth counts
        gt_counts = get_ground_truth_chemical_counts(doi)
        if gt_counts is None:
            print(f"  No ground truth data found")
            continue
        
        print(f"  Ground truth chemical input counts: {gt_counts}")
        
        if not test_mode:
            # Default mode: read from mcp_run
            iter2_files = list(mcp_run_path.glob("iter2_hints_*.txt"))
            if not iter2_files:
                print(f"  No iter2 hint files found")
                continue
            extracted_counts = []
            for hint_file in sorted(iter2_files):
                count = count_chemical_inputs_in_file(hint_file)
                extracted_counts.append(count)
                try:
                    print(f"  {hint_file.name}: {count} chemical inputs")
                except UnicodeEncodeError:
                    safe_name = hint_file.name.encode('ascii', 'replace').decode('ascii')
                    print(f"  {safe_name}: {count} chemical inputs")
            print(f"  Extracted chemical input counts: {extracted_counts}")
            # Compare
            match = False
            if len(extracted_counts) == len(gt_counts):
                extracted_sorted = sorted(extracted_counts)
                gt_sorted = sorted(gt_counts)
                if extracted_sorted == gt_sorted:
                    print(f"  OK MATCH: Counts match perfectly")
                    total_matches += 1
                    match = True
                else:
                    print(f"  X MISMATCH: Counts don't match")
                    print(f"    Extracted (sorted): {extracted_sorted}")
                    print(f"    Ground truth (sorted): {gt_sorted}")
                    total_mismatches += 1
            else:
                print(f"  X MISMATCH: Different number of procedures")
                print(f"    Extracted: {len(extracted_counts)} procedures")
                print(f"    Ground truth: {len(gt_counts)} procedures")
                total_mismatches += 1
            total_files += 1
            rows_default.append({
                "hash": hash_folder.name,
                "doi": doi,
                "extracted": extracted_counts,
                "gt": gt_counts,
                "match": "Yes" if match else "No"
            })
        else:
            # Test mode: read from iter2_test_results per test number
            test_dir = hash_folder / "iter2_test_results"
            if not test_dir.exists():
                print(f"  No iter2_test_results directory found")
                continue
            # Group files by test number suffix
            grouped: dict[str, list[Path]] = defaultdict(list)
            for f in sorted(test_dir.glob("iter2_hints_*_*.txt")):
                # Filename pattern: iter2_hints_<entity>_<n>.txt → extract n
                stem = f.stem
                try:
                    n = stem.split("_")[-1]
                    grouped[n].append(f)
                except Exception:
                    continue
            if not grouped:
                print(f"  No iter2 test files found")
                continue
            for n, files in grouped.items():
                extracted_counts = []
                for hint_file in sorted(files):
                    count = count_chemical_inputs_in_file(hint_file)
                    extracted_counts.append(count)
                match = False
                if len(extracted_counts) == len(gt_counts):
                    if sorted(extracted_counts) == sorted(gt_counts):
                        match = True
                rows_by_test.setdefault(n, []).append({
                    "hash": hash_folder.name,
                    "doi": doi,
                    "extracted": extracted_counts,
                    "gt": gt_counts,
                    "match": "Yes" if match else "No"
                })
    
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    if not test_mode:
        print(f"Total files analyzed: {total_files}")
        print(f"Perfect matches: {total_matches}")
        print(f"Mismatches: {total_mismatches}")
        if total_files > 0:
            print(f"Match rate: {total_matches/total_files*100:.1f}%")
        # Write markdown
        out_md = Path(__file__).with_name("number_of_chemical_inputs.md")
        rate = (total_matches/total_files*100.0) if total_files > 0 else 0.0
        _write_markdown_default(rows_default, {"files": total_files, "matches": total_matches, "mismatches": total_mismatches, "rate": rate}, out_md)
        try:
            print(f"Markdown report written to: {out_md.resolve()}")
        except Exception:
            print(f"Markdown report written to: {out_md}")
    else:
        # Aggregate per-test totals
        totals_by_test: dict[str, dict] = {}
        for n, rows in rows_by_test.items():
            files = len(rows)
            matches = sum(1 for r in rows if r["match"] == "Yes")
            mismatches = files - matches
            rate = (matches/files*100.0) if files > 0 else 0.0
            totals_by_test[n] = {"files": files, "matches": matches, "mismatches": mismatches, "rate": rate}
        for n in sorted(totals_by_test.keys(), key=lambda x: int(x)):
            t = totals_by_test[n]
            print(f"Test {n}: files={t['files']} matches={t['matches']} mismatches={t['mismatches']} rate={t['rate']:.1f}%")
        out_md = Path(__file__).with_name("number_of_chemical_inputs_test.md")
        _write_markdown_test(rows_by_test, totals_by_test, out_md)
        try:
            print(f"Markdown report written to: {out_md.resolve()}")
        except Exception:
            print(f"Markdown report written to: {out_md}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Analyze iter2 chemical inputs vs ground truth")
    parser.add_argument('--test', action='store_true', help='Use iter2_test_results per-hash and report per-test scores')
    args = parser.parse_args()
    analyze_chemical_inputs(test_mode=args.test)
