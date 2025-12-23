#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Aggregate Scoring Runner

Runs all evaluation scoring scripts (CBU, characterisation, steps, chemicals) with appropriate
arguments and generates a comprehensive overall report aggregating F1 scores across all categories.

Usage:
    python -m evaluation.scoring_all                    # Score current work against earlier GT
    python -m evaluation.scoring_all --full             # Score current work against full GT
    python -m evaluation.scoring_all --previous         # Score previous work against earlier GT
    python -m evaluation.scoring_all --full --previous  # Score previous work against full GT
"""

import argparse
import subprocess
import sys
import io
import re
import json
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any
from datetime import datetime

# Set stdout to UTF-8 encoding for Windows compatibility
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')


def run_scoring_script(script_name: str, args: List[str]) -> bool:
    """
    Run a scoring script as a module with given arguments.
    
    Args:
        script_name: Name of the scoring module (e.g., 'evaluation.scoring_cbu')
        args: List of command-line arguments
        
    Returns:
        True if successful, False otherwise
    """
    cmd = [sys.executable, "-m", script_name] + args
    print(f"\n{'='*80}")
    print(f"Running: {' '.join(cmd)}")
    print(f"{'='*80}\n")
    
    try:
        result = subprocess.run(cmd, check=True, capture_output=False, text=True)
        print(f"\n✓ Completed: {script_name}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"\n✗ Failed: {script_name} (exit code: {e.returncode})")
        return False
    except Exception as e:
        print(f"\n✗ Error running {script_name}: {e}")
        return False


def extract_overall_metrics(overall_md_path: Path) -> Optional[Tuple[int, int, int, float, float, float]]:
    """
    Extract aggregate metrics from an _overall.md file.
    
    Looks for a summary line like:
    "| **TOTAL** | 123 | 45 | 67 | 0.732 | 0.648 | 0.687 |"
    
    Returns:
        Tuple of (TP, FP, FN, Precision, Recall, F1) or None if not found
    """
    if not overall_md_path.exists():
        return None
    
    content = overall_md_path.read_text(encoding="utf-8")
    
    # Look for Overall/TOTAL row in markdown table
    # Format: | **Overall** | **84** | **18** | **30** | **0.824** | **0.737** | **0.778** |
    pattern = r'\|\s*\*\*(?:Overall|TOTAL)\*\*\s*\|\s*\*\*(\d+)\*\*\s*\|\s*\*\*(\d+)\*\*\s*\|\s*\*\*(\d+)\*\*\s*\|\s*\*\*([\d.]+)\*\*\s*\|\s*\*\*([\d.]+)\*\*\s*\|\s*\*\*([\d.]+)\*\*\s*\|'
    match = re.search(pattern, content)
    
    if match:
        tp = int(match.group(1))
        fp = int(match.group(2))
        fn = int(match.group(3))
        prec = float(match.group(4))
        rec = float(match.group(5))
        f1 = float(match.group(6))
        return (tp, fp, fn, prec, rec, f1)
    
    return None


def extract_cbu_formula_only_overall_metrics(overall_md_path: Path) -> Optional[Tuple[int, int, int, float, float, float]]:
    """
    Extract formula-only aggregate CBU metrics from the CBU _overall.md file.
    
    Uses the 'Formula-only Scoring Summary' line written by evaluation.scoring_cbu.evaluate_full, e.g.:
      **Formula-only Scoring Summary:** TP=128 FP=40 FN=36 | P=0.762 R=0.780 F1=0.771
    """
    if not overall_md_path.exists():
        return None
    
    content = overall_md_path.read_text(encoding="utf-8")
    pattern = r'\*\*Formula-only Scoring Summary:\*\*\s*TP=(\d+)\s+FP=(\d+)\s+FN=(\d+)\s*\|\s*P=([\d.]+)\s+R=([\d.]+)\s+F1=([\d.]+)'
    match = re.search(pattern, content)
    if not match:
        return None
    
    tp = int(match.group(1))
    fp = int(match.group(2))
    fn = int(match.group(3))
    prec = float(match.group(4))
    rec = float(match.group(5))
    f1 = float(match.group(6))
    return (tp, fp, fn, prec, rec, f1)


def extract_per_document_metrics(overall_md_path: Path, category: str = "") -> Dict[str, Dict[str, int]]:
    """
    Extract per-document metrics from an _overall.md file.

    Parses markdown table to extract TP, FP, FN for each document/hash.

    For CBU category, extracts from the "Formula-only Scoring" section.
    For other categories, extracts from the main table.

    Returns:
        Dict mapping document identifier to {tp, fp, fn, precision, recall, f1}
    """
    if not overall_md_path.exists():
        return {}

    content = overall_md_path.read_text(encoding="utf-8")
    per_doc_metrics = {}

    # Pattern for document rows: [hash/doi] | TP | FP | FN | Precision | Recall | F1 |
    # Handle both formats: with and without leading | (CBU uses no leading |, others use leading |)
    # Exclude rows with ** (Overall/TOTAL) and header rows
    pattern = r'(?:\|\s*)?([a-f0-9]{8}|10\.\S+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)\s*(?:\|)?'

    # For CBU, extract only from "Formula-only Scoring" section
    if category.lower() == "cbu":
        # Split content by sections and find the Formula-only Scoring section
        sections = re.split(r'(?=^## )', content, flags=re.MULTILINE)
        for section in sections:
            if section.startswith('## Formula-only Scoring'):
                # Extract just the table content (skip the header and separator)
                lines = section.split('\n')
                # Find the table content (skip header and separator lines)
                table_start = -1
                for i, line in enumerate(lines):
                    if line.startswith('|------|'):  # Table separator
                        table_start = i + 1
                        break
                if table_start > 0:
                    # Extract table rows until we hit **Overall**
                    table_lines = []
                    for line in lines[table_start:]:
                        line = line.strip()
                        if not line or line.startswith('**Overall**'):
                            break
                        table_lines.append(line)
                    content = '\n'.join(table_lines)
                break
        else:
            # Fallback to extracting everything if pattern doesn't match
            print(f"Warning: Could not find Combined Scoring section for CBU, using full content")
            pass

    for match in re.finditer(pattern, content):
        doc_id = match.group(1)
        tp = int(match.group(2))
        fp = int(match.group(3))
        fn = int(match.group(4))
        precision = float(match.group(5))
        recall = float(match.group(6))
        f1 = float(match.group(7))

        per_doc_metrics[doc_id] = {
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "precision": precision,
            "recall": recall,
            "f1": f1
        }

    return per_doc_metrics


def load_doi_mapping() -> Dict[str, str]:
    """Load DOI to hash mapping and return hash to DOI mapping."""
    doi_mapping_path = Path("data/doi_to_hash.json")
    hash_to_doi = {}

    if doi_mapping_path.exists():
        try:
            import json
            with open(doi_mapping_path, 'r', encoding='utf-8') as f:
                doi_to_hash = json.load(f)
            # Create reverse mapping
            hash_to_doi = {hash_val: doi.replace("_", "/") for doi, hash_val in doi_to_hash.items()}
        except Exception as e:
            print(f"Warning: Could not load DOI mapping: {e}")

    return hash_to_doi


def generate_aggregate_report(output_dir: Path, use_full: bool, use_previous: bool) -> None:
    """
    Generate aggregate overall report combining metrics from all categories.

    Args:
        output_dir: Base output directory (e.g., evaluation/data/full_result)
        use_full: Whether full GT was used
        use_previous: Whether previous work was scored
    """
    print(f"\n{'='*80}")
    print("Generating Aggregate Overall Report")
    print(f"{'='*80}\n")

    # Load DOI mapping for hash to DOI conversion
    hash_to_doi = load_doi_mapping()
    print(f"Loaded DOI mapping for {len(hash_to_doi)} documents")

    # Determine subdirectories based on flags
    suffix = "_previous" if use_previous else ""

    categories = {
        "CBU": output_dir / f"cbu{suffix}" / "_overall.md",
        "Characterisation": output_dir / f"characterisation{suffix}" / "_overall.md",
        "Steps": output_dir / f"steps{suffix}" / "_overall.md",
        "Chemicals": output_dir / f"chemicals{suffix}" / "_overall.md",
    }
    
    # Extract metrics from each category
    metrics: Dict[str, Optional[Tuple[int, int, int, float, float, float]]] = {}
    per_doc_data: Dict[str, Dict[str, Dict[str, Any]]] = {}
    
    for category, path in categories.items():
        print(f"Reading {category}: {path}")
        # For CBU, use formula-only metrics instead of combined+names
        if category == "CBU":
            metrics[category] = extract_cbu_formula_only_overall_metrics(path) or extract_overall_metrics(path)
        else:
            metrics[category] = extract_overall_metrics(path)
        per_doc_data[category] = extract_per_document_metrics(path, category)
        
        if metrics[category]:
            tp, fp, fn, prec, rec, f1 = metrics[category]
            print(f"  ✓ Found: TP={tp}, FP={fp}, FN={fn}, F1={f1:.3f}")
            print(f"    Per-doc entries: {len(per_doc_data[category])}")
        else:
            print(f"  ✗ No metrics found")
    
    # Calculate aggregate totals
    total_tp = sum(m[0] for m in metrics.values() if m is not None)
    total_fp = sum(m[1] for m in metrics.values() if m is not None)
    total_fn = sum(m[2] for m in metrics.values() if m is not None)
    
    # Calculate aggregate precision, recall, F1
    agg_precision = total_tp / (total_tp + total_fp) if (total_tp + total_fp) > 0 else 0.0
    agg_recall = total_tp / (total_tp + total_fn) if (total_tp + total_fn) > 0 else 0.0
    agg_f1 = 2 * agg_precision * agg_recall / (agg_precision + agg_recall) if (agg_precision + agg_recall) > 0 else 0.0
    
    # Generate report
    lines = []
    
    # Header
    gt_type = "Full Ground Truth" if use_full else "Earlier Ground Truth"
    work_type = "Previous Work" if use_previous else "Current Work"
    lines.append(f"# Overall Evaluation Report: {work_type} vs {gt_type}\n")
    lines.append(f"**Generated**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    lines.append("---\n")
    
    # Summary table
    lines.append("## Summary by Category\n")
    lines.append("| Category | TP | FP | FN | Precision | Recall | F1 |\n")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: |\n")
    
    for category in ["CBU", "Characterisation", "Steps", "Chemicals"]:
        m = metrics.get(category)
        if m:
            tp, fp, fn, prec, rec, f1 = m
            lines.append(f"| {category} | {tp} | {fp} | {fn} | {prec:.3f} | {rec:.3f} | {f1:.3f} |\n")
        else:
            lines.append(f"| {category} | - | - | - | - | - | - |\n")
    
    lines.append(f"| **AGGREGATE** | **{total_tp}** | **{total_fp}** | **{total_fn}** | **{agg_precision:.3f}** | **{agg_recall:.3f}** | **{agg_f1:.3f}** |\n")
    lines.append("\n")
    
    # Detailed breakdown
    lines.append("## Aggregate Metrics\n")
    lines.append(f"- **Total True Positives (TP)**: {total_tp}\n")
    lines.append(f"- **Total False Positives (FP)**: {total_fp}\n")
    lines.append(f"- **Total False Negatives (FN)**: {total_fn}\n")
    lines.append(f"- **Aggregate Precision**: {agg_precision:.4f}\n")
    lines.append(f"- **Aggregate Recall**: {agg_recall:.4f}\n")
    lines.append(f"- **Aggregate F1 Score**: {agg_f1:.4f}\n")
    lines.append("\n")
    
    # Category performance
    lines.append("## Category Performance\n")
    for category in ["CBU", "Characterisation", "Steps", "Chemicals"]:
        m = metrics.get(category)
        if m:
            tp, fp, fn, prec, rec, f1 = m
            lines.append(f"### {category}\n")
            lines.append(f"- Precision: {prec:.4f}\n")
            lines.append(f"- Recall: {rec:.4f}\n")
            lines.append(f"- F1 Score: {f1:.4f}\n")
            lines.append(f"- Details: TP={tp}, FP={fp}, FN={fn}\n")
            lines.append("\n")

            # Add per-DOI table for this category
            category_docs = per_doc_data.get(category, {})
            if category_docs:
                lines.append(f"#### {category} - Per DOI Results\n")
                lines.append("| # | DOI | TP | FP | FN | Precision | Recall | F1 |\n")
                lines.append("| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |\n")

                # Convert hashes to DOIs and sort for consistent ordering
                doi_results = []
                for hash_key, doc_metrics in category_docs.items():
                    # Use DOI mapping if available, otherwise format the hash as DOI
                    if hash_key in hash_to_doi:
                        doi_formatted = hash_to_doi[hash_key]
                    else:
                        # Fallback: assume it's already a DOI or format as one
                        doi_formatted = hash_key.replace("_", "/")

                    doi_results.append((doi_formatted, doc_metrics))

                # Sort by DOI for consistent ordering
                doi_results.sort(key=lambda x: x[0])

                for idx, (doi_formatted, doc_metrics) in enumerate(doi_results, 1):
                    lines.append(f"| {idx} | {doi_formatted} | {doc_metrics['tp']} | {doc_metrics['fp']} | {doc_metrics['fn']} | {doc_metrics['precision']:.3f} | {doc_metrics['recall']:.3f} | {doc_metrics['f1']:.3f} |\n")

                lines.append("\n")

    # Configuration info
    lines.append("---\n")
    lines.append("## Configuration\n")
    lines.append(f"- **Ground Truth**: {gt_type}\n")
    lines.append(f"- **Work Type**: {work_type}\n")
    lines.append(f"- **Output Directory**: `{output_dir}`\n")
    
    # Write markdown report with appropriate filename
    filename = "_overall_previous.md" if use_previous else "_overall.md"
    output_file = output_dir / filename
    output_file.write_text("".join(lines), encoding="utf-8")
    print(f"\n✓ Aggregate report written to: {output_file}")
    
    # Write structured JSON for significance testing
    json_filename = "_overall_previous.json" if use_previous else "_overall.json"
    json_output_file = output_dir / json_filename
    
    structured_data = {
        "metadata": {
            "generated": datetime.now().isoformat(),
            "ground_truth": gt_type,
            "work_type": work_type,
            "output_directory": str(output_dir)
        },
        "aggregate": {
            "tp": total_tp,
            "fp": total_fp,
            "fn": total_fn,
            "precision": round(agg_precision, 4),
            "recall": round(agg_recall, 4),
            "f1": round(agg_f1, 4)
        },
        "by_category": {},
        "per_document": {}
    }
    
    # Add category-level metrics
    for category in ["CBU", "Characterisation", "Steps", "Chemicals"]:
        m = metrics.get(category)
        if m:
            tp, fp, fn, prec, rec, f1 = m
            structured_data["by_category"][category.lower()] = {
                "tp": tp,
                "fp": fp,
                "fn": fn,
                "precision": round(prec, 4),
                "recall": round(rec, 4),
                "f1": round(f1, 4)
            }
    
    # Add per-document metrics (organized by category)
    for category, doc_metrics in per_doc_data.items():
        structured_data["per_document"][category.lower()] = doc_metrics
    
    json_output_file.write_text(json.dumps(structured_data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"✓ Structured JSON written to: {json_output_file}")
    
    print(f"\n{'='*80}")
    print(f"AGGREGATE F1 SCORE: {agg_f1:.4f}")
    print(f"{'='*80}\n")


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Run all evaluation scoring scripts and generate aggregate report"
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Use full_ground_truth instead of earlier_ground_truth"
    )
    parser.add_argument(
        "--previous",
        action="store_true",
        help="Score previous_work instead of current results"
    )
    
    args = parser.parse_args()
    
    # Prepare base arguments
    base_args = []
    if args.full:
        base_args.append("--full")
    if args.previous:
        base_args.append("--previous")
    
    # Determine output directory
    base_dir = "full_result" if args.full else "result"
    output_dir = Path("evaluation/data") / base_dir
    
    print("\n" + "="*80)
    print("AGGREGATE EVALUATION RUNNER")
    print("="*80)
    print(f"Ground Truth: {'Full' if args.full else 'Earlier'}")
    print(f"Work Type: {'Previous' if args.previous else 'Current'}")
    print(f"Output Directory: {output_dir}")
    print("="*80)
    
    # Run all scoring scripts with appropriate arguments
    # For previous work:
    # - CBU and characterisation need --anchor flag
    # - Chemicals needs --fuzzy and --anchor flags
    # - Steps always uses --skip-order and --ignore flags
    scripts_config = [
        ("evaluation.scoring_cbu", base_args + (["--anchor"] if args.previous else [])),
        ("evaluation.scoring_characterisation", base_args + (["--anchor"] if args.previous else [])),
        ("evaluation.scoring_steps", base_args + ["--skip-order", "--ignore"]),
        ("evaluation.scoring_chemicals", base_args + (["--fuzzy", "--anchor"] if args.previous else [])),
    ]
    
    success_count = 0
    for script_name, script_args in scripts_config:
        if run_scoring_script(script_name, script_args):
            success_count += 1
    
    print(f"\n{'='*80}")
    print(f"Completed: {success_count}/{len(scripts_config)} scripts succeeded")
    print(f"{'='*80}\n")
    
    # Generate aggregate report
    if success_count > 0:
        generate_aggregate_report(output_dir, args.full, args.previous)
    else:
        print("✗ No scoring scripts succeeded, skipping aggregate report generation")
        sys.exit(1)


if __name__ == "__main__":
    main()
