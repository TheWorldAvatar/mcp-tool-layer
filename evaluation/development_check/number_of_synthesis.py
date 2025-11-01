#!/usr/bin/env python3
"""
Script to compare the number of synthesis entities extracted by iter1 
with the ground truth number of synthesis procedures in steps files.

Default mode:
  - Reads mcp_run/iter1_top_entities.json per hash

Test mode (--test):
  - Iterates data/<hash>/iter1_test_results/iter1_top_entities_*.json per hash
  - Produces per-test scores for all available numbered results

Both modes:
  - Print console summary and write a markdown report alongside this script
"""

import os
import argparse
import json
from pathlib import Path
from collections import defaultdict


def load_doi_mapping():
    """Load the DOI to hash mapping."""
    mapping_path = 'data/doi_to_hash.json'
    if not os.path.exists(mapping_path):
        print(f"Error: DOI mapping file not found: {mapping_path}")
        return {}
    
    with open(mapping_path, 'r') as f:
        doi_mapping = json.load(f)
    
    # Create reverse mapping (hash -> doi)
    hash_to_doi = {v: k for k, v in doi_mapping.items()}
    return hash_to_doi


def find_steps_file(doi):
    """Find the corresponding steps JSON file for a given DOI."""
    # The DOI is already in the correct format (with underscores)
    filename = doi + ".json"
    
    # Look in earlier_ground_truth/steps/
    steps_dir = Path("earlier_ground_truth/steps")
    if steps_dir.exists():
        steps_file = steps_dir / filename
        if steps_file.exists():
            return steps_file
    
    # Look in newer_ground_truth_lu/steps/
    steps_dir = Path("newer_ground_truth_lu/steps")
    if steps_dir.exists():
        steps_file = steps_dir / filename
        if steps_file.exists():
            return steps_file
    
    # Look in newer_ground_truth_sun/prepared/steps/
    steps_dir = Path("newer_ground_truth_sun/prepared/steps")
    if steps_dir.exists():
        steps_file = steps_dir / filename
        if steps_file.exists():
            return steps_file
    
    return None


def count_synthesis_procedures(steps_file):
    """Count the number of synthesis procedures in a steps JSON file."""
    try:
        with open(steps_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # Look for "Synthesis" array
        if "Synthesis" in data and isinstance(data["Synthesis"], list):
            return len(data["Synthesis"])
        else:
            print(f"Warning: No 'Synthesis' array found in {steps_file}")
            return 0
    except Exception as e:
        print(f"Error reading {steps_file}: {e}")
        return 0


def count_iter1_entities(hash_dir):
    """Count the number of entities in iter1_top_entities.json."""
    iter1_entities_file = hash_dir / "mcp_run" / "iter1_top_entities.json"
    
    if not iter1_entities_file.exists():
        return 0
    
    try:
        with open(iter1_entities_file, 'r', encoding='utf-8') as f:
            entities = json.load(f)
        
        if isinstance(entities, list):
            return len(entities)
        else:
            print(f"Warning: iter1_top_entities.json is not a list in {hash_dir}")
            return 0
    except Exception as e:
        print(f"Error reading {iter1_entities_file}: {e}")
        return 0


def _load_iter1_model_name() -> str:
    """Load model used for iter1_hints from configs/extraction_models.json.
    Returns 'unknown' if mapping missing/unreadable.
    """
    try:
        cfg_path = Path("configs") / "extraction_models.json"
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        name = str(data.get("iter1_hints", "")).strip()
        return name if name else "unknown"
    except Exception:
        return "unknown"


def _write_markdown_default(results, totals, out_path: Path):
    lines = []
    model = _load_iter1_model_name()
    lines.append(f"# Iter1 vs Ground Truth — Default Mode")
    lines.append("")
    lines.append(f"- Model (iter1_hints): {model}")
    lines.append("")
    lines.append("| Hash | DOI | Iter1 Entities | GT Procedures | Difference | Status |")
    lines.append("|------|-----|----------------:|--------------:|-----------:|--------|")
    for r in results:
        lines.append(f"| {r['hash']} | {r['doi']} | {r['iter1_count']} | {r['gt_count']} | {r['difference']} | {r['status']} |")
    lines.append("")
    lines.append(f"Total Iter1: {totals['iter1']}  ")
    lines.append(f"Total GT: {totals['gt']}  ")
    lines.append(f"Overall Diff: {totals['diff']}")
    out_path.write_text("\n".join(lines), encoding="utf-8")


def _write_markdown_test(results_by_test, totals_by_test, out_path: Path):
    lines = []
    model = _load_iter1_model_name()
    lines.append(f"# Iter1 vs Ground Truth — Test Mode")
    lines.append("")
    lines.append(f"- Model (iter1_hints): {model}")
    lines.append("")
    for test_num in sorted(results_by_test.keys(), key=lambda x: int(x)):
        results = results_by_test[test_num]
        totals = totals_by_test[test_num]
        lines.append(f"## Test {test_num}")
        lines.append("")
        lines.append("| Hash | DOI | Iter1 Entities | GT Procedures | Difference | Status |")
        lines.append("|------|-----|----------------:|--------------:|-----------:|--------|")
        for r in results:
            lines.append(f"| {r['hash']} | {r['doi']} | {r['iter1_count']} | {r['gt_count']} | {r['difference']} | {r['status']} |")
        lines.append("")
        lines.append(f"Total Iter1: {totals['iter1']}  ")
        lines.append(f"Total GT: {totals['gt']}  ")
        lines.append(f"Overall Diff: {totals['diff']}")
        lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")


def analyze_synthesis_counts(test_mode: bool = False):
    """Analyze synthesis counts for all hash folders."""
    data_dir = Path("data")
    hash_to_doi = load_doi_mapping()
    
    if not hash_to_doi:
        print("No DOI mapping found. Exiting.")
        return
    
    print("Synthesis Count Analysis")
    print("=" * 80)
    print(f"{'Hash':<12} {'DOI':<25} {'Iter1 Entities':<15} {'GT Procedures':<15} {'Difference':<12} {'Status'}")
    print("-" * 80)
    
    # Default accumulators
    results = []
    total_iter1_entities = 0
    total_gt_procedures = 0
    # Test accumulators
    results_by_test = {}
    totals_by_test = {}
    
    # Get all hash folders
    hash_folders = [item for item in data_dir.iterdir() if item.is_dir() and not item.name.startswith('.')]
    hash_folders = sorted(hash_folders, key=lambda x: x.name)
    
    for hash_folder in hash_folders:
        hash_name = hash_folder.name
        
        # Skip if not a valid hash (not in mapping)
        if hash_name not in hash_to_doi:
            continue
        
        doi = hash_to_doi[hash_name]
        
        # Count iter1 entities (default or test)
        if not test_mode:
            iter1_count = count_iter1_entities(hash_folder)
            iter1_counts_by_test = None
        else:
            # Collect per-test counts from data/<hash>/iter1_test_results/iter1_top_entities_*.json
            iter1_counts_by_test = {}
            test_dir = hash_folder / "iter1_test_results"
            if test_dir.exists() and test_dir.is_dir():
                for p in sorted(test_dir.glob("iter1_top_entities_*.json")):
                    test_num = p.stem.replace("iter1_top_entities_", "", 1)
                    try:
                        entities = json.loads(p.read_text(encoding='utf-8'))
                        cnt = len(entities) if isinstance(entities, list) else 0
                    except Exception:
                        cnt = 0
                    iter1_counts_by_test[test_num] = cnt
            iter1_count = None
        
        # Find and count ground truth procedures
        steps_file = find_steps_file(doi)
        gt_count = 0
        status = "No GT file"
        
        if steps_file:
            gt_count = count_synthesis_procedures(steps_file)
            status = "Found"
        else:
            # Try to find in other possible locations
            possible_dois = [doi, doi.replace(".", "_"), doi.replace("/", "_")]
            for alt_doi in possible_dois:
                alt_steps_file = find_steps_file(alt_doi)
                if alt_steps_file:
                    gt_count = count_synthesis_procedures(alt_steps_file)
                    status = "Found (alt)"
                    break
        
        if not test_mode:
            # Calculate difference
            difference = iter1_count - gt_count
            # Determine status
            if status == "No GT file":
                status = "X No GT"
            elif difference == 0:
                status = "OK Match"
            elif difference > 0:
                status = f"W +{difference} extra"
            else:
                status = f"W {difference} missing"
            # Store results
            results.append({
                'hash': hash_name,
                'doi': doi,
                'iter1_count': iter1_count,
                'gt_count': gt_count,
                'difference': difference,
                'status': status
            })
            total_iter1_entities += iter1_count
            total_gt_procedures += gt_count
            print(f"{hash_name:<12} {doi:<25} {iter1_count:<15} {gt_count:<15} {difference:<12} {status}")
        else:
            # For each test, compute and store results
            if not iter1_counts_by_test:
                continue
            for test_num, iter1_count in iter1_counts_by_test.items():
                difference = iter1_count - gt_count
                st = status
                if st == "No GT file":
                    st = "X No GT"
                elif difference == 0:
                    st = "OK Match"
                elif difference > 0:
                    st = f"W +{difference} extra"
                else:
                    st = f"W {difference} missing"
                results_by_test.setdefault(test_num, []).append({
                    'hash': hash_name,
                    'doi': doi,
                    'iter1_count': iter1_count,
                    'gt_count': gt_count,
                    'difference': difference,
                    'status': st
                })
    
    if not test_mode:
        # Print summary
        print("-" * 80)
        total_difference = total_iter1_entities - total_gt_procedures
        print(f"{'TOTAL':<12} {'':<25} {total_iter1_entities:<15} {total_gt_procedures:<15} {total_difference:<12}")
        # Statistics
        print("\nSummary Statistics:")
        print(f"Total hash folders analyzed: {len(results)}")
        print(f"Total iter1 entities: {total_iter1_entities}")
        print(f"Total ground truth procedures: {total_gt_procedures}")
        print(f"Overall difference: {total_difference}")
        # PRF
        tp = fp = fn = 0
        considered = 0
        for result in results:
            if result['status'] == 'X No GT':
                continue
            considered += 1
            p = int(result['iter1_count'])
            g = int(result['gt_count'])
            tp += min(p, g)
            fp += max(p - g, 0)
            fn += max(g - p, 0)
        precision = (tp / (tp + fp)) if (tp + fp) > 0 else 0.0
        recall = (tp / (tp + fn)) if (tp + fn) > 0 else 0.0
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0
        print("\nPRF (count-aligned, excluding no-GT):")
        print(f"  TP={tp} FP={fp} FN={fn} | Precision={precision:.3f} Recall={recall:.3f} F1={f1:.3f}")
        # Status breakdown
        status_counts = defaultdict(int)
        for result in results:
            if "Match" in result['status']:
                status_counts['matches'] += 1
            elif "extra" in result['status']:
                status_counts['over-extracted'] += 1
            elif "missing" in result['status']:
                status_counts['under-extracted'] += 1
            else:
                status_counts['no_gt'] += 1
        print(f"\nStatus breakdown:")
        print(f"  Perfect matches: {status_counts['matches']}")
        print(f"  Over-extracted: {status_counts['over-extracted']}")
        print(f"  Under-extracted: {status_counts['under-extracted']}")
        print(f"  No ground truth: {status_counts['no_gt']}")
        # Detailed analysis
        print(f"\nDetailed Mismatch Analysis:")
        print("-" * 50)
        for result in results:
            if result['status'] not in ["OK Match", "X No GT"]:
                print(f"{result['hash']} ({result['doi']}): {result['iter1_count']} entities vs {result['gt_count']} procedures ({result['status']})")
        # Write markdown
        out_md = Path(__file__).with_name("number_of_synthesis.md")
        _write_markdown_default(results, {'iter1': total_iter1_entities, 'gt': total_gt_procedures, 'diff': total_difference}, out_md)
        try:
            print(f"Markdown report written to: {out_md.resolve()}")
        except Exception:
            print(f"Markdown report written to: {out_md}")
    else:
        # Aggregate totals per test
        for test_num, rlist in results_by_test.items():
            t_iter1 = sum(int(r['iter1_count']) for r in rlist)
            t_gt = sum(int(r['gt_count']) for r in rlist)
            totals_by_test[test_num] = {'iter1': t_iter1, 'gt': t_gt, 'diff': t_iter1 - t_gt}
        # Print brief summary
        print("\nPer-test totals:")
        for test_num in sorted(totals_by_test.keys(), key=lambda x: int(x)):
            t = totals_by_test[test_num]
            print(f"  Test {test_num}: Iter1={t['iter1']} GT={t['gt']} Diff={t['diff']}")
        # Write markdown
        out_md = Path(__file__).with_name("number_of_synthesis_test.md")
        _write_markdown_test(results_by_test, totals_by_test, out_md)
        try:
            print(f"Markdown report written to: {out_md.resolve()}")
        except Exception:
            print(f"Markdown report written to: {out_md}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Analyze iter1 entity counts vs GT procedures")
    parser.add_argument('--test', action='store_true', help='Use iter1_test_results per-hash (iter1_top_entities_*.json) and report per-test scores')
    args = parser.parse_args()
    analyze_synthesis_counts(test_mode=args.test)
