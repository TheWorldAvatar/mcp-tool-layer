#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Statistical Significance Testing for Evaluation Results

Performs statistical tests to determine if the difference between two systems
(e.g., current work vs previous work) is statistically significant.

Tests performed (on overall aggregated data):
- McNemar's test (for paired binary classifications)
- Paired t-test (for per-document F1 scores)
- Bootstrap resampling (for F1 score comparison)

Usage:
    python -m evaluation.significance_test RESULT1.json RESULT2.json
    
Example:
    # Compare current work vs previous work (Full GT)
    python -m evaluation.significance_test \
        evaluation/data/full_result/_overall.json \
        evaluation/data/full_result/_overall_previous.json
"""

import argparse
import json
import sys
import io
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any
import math
from collections import Counter

# Set stdout to UTF-8 encoding for Windows compatibility
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')


def load_results(json_path: Path) -> Dict[str, Any]:
    """Load evaluation results from JSON file."""
    if not json_path.exists():
        raise FileNotFoundError(f"Results file not found: {json_path}")
    
    with open(json_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_doi_hash_mapping() -> Dict[str, str]:
    """Load DOI to hash mapping from data_backup/doi_to_hash.json."""
    mapping_path = Path("data_backup/doi_to_hash.json")
    if not mapping_path.exists():
        return {}
    
    with open(mapping_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def mcnemar_test(n01: int, n10: int) -> Tuple[float, float]:
    """
    Perform McNemar's test for paired binary classifications.
    
    Args:
        n01: Number of instances where system 1 correct, system 2 incorrect
        n10: Number of instances where system 1 incorrect, system 2 correct
        
    Returns:
        Tuple of (chi_squared, p_value)
    """
    # McNemar's test statistic with continuity correction
    chi_squared = ((abs(n01 - n10) - 1) ** 2) / (n01 + n10) if (n01 + n10) > 0 else 0.0
    
    # Approximate p-value using chi-squared distribution (df=1)
    # For df=1, we can use the standard normal approximation
    if chi_squared == 0:
        p_value = 1.0
    else:
        # Use complementary error function approximation
        z = math.sqrt(chi_squared)
        # Two-tailed test
        p_value = 2 * (1 - _phi(z))
    
    return chi_squared, p_value


def _phi(x: float) -> float:
    """
    Cumulative distribution function for standard normal distribution.
    Approximation using error function.
    """
    return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0


def bootstrap_test(scores1: List[float], scores2: List[float], n_samples: int = 10000, random_seed: int = 42) -> Tuple[float, float]:
    """
    Perform bootstrap resampling test to compare mean F1 scores.
    
    Args:
        scores1: F1 scores from system 1 (per document)
        scores2: F1 scores from system 2 (per document)
        n_samples: Number of bootstrap samples
        random_seed: Random seed for reproducibility
        
    Returns:
        Tuple of (observed_diff, p_value)
    """
    import random
    random.seed(random_seed)
    
    n = len(scores1)
    if n != len(scores2):
        raise ValueError("Bootstrap test requires paired samples of equal length")
    
    # Observed difference
    observed_diff = sum(scores1) / n - sum(scores2) / n
    
    # Bootstrap sampling
    count_extreme = 0
    for _ in range(n_samples):
        # Resample with replacement
        indices = [random.randint(0, n - 1) for _ in range(n)]
        boot_scores1 = [scores1[i] for i in indices]
        boot_scores2 = [scores2[i] for i in indices]
        
        boot_diff = sum(boot_scores1) / n - sum(boot_scores2) / n
        
        # Count samples with difference as extreme as observed
        if abs(boot_diff) >= abs(observed_diff):
            count_extreme += 1
    
    p_value = count_extreme / n_samples
    return observed_diff, p_value


def paired_t_test(scores1: List[float], scores2: List[float]) -> Tuple[float, float]:
    """
    Perform paired t-test on F1 scores.
    
    Args:
        scores1: F1 scores from system 1 (per document)
        scores2: F1 scores from system 2 (per document)
        
    Returns:
        Tuple of (t_statistic, p_value)
    """
    n = len(scores1)
    if n != len(scores2):
        raise ValueError("Paired t-test requires paired samples of equal length")
    
    if n < 2:
        return 0.0, 1.0
    
    # Calculate differences
    diffs = [s1 - s2 for s1, s2 in zip(scores1, scores2)]
    
    # Mean and standard deviation of differences
    mean_diff = sum(diffs) / n
    var_diff = sum((d - mean_diff) ** 2 for d in diffs) / (n - 1)
    
    if var_diff == 0:
        return 0.0, 1.0
    
    # t-statistic
    t_stat = mean_diff / math.sqrt(var_diff / n)
    
    # Approximate p-value using normal approximation for large n
    # For small n, this is less accurate but provides a reasonable estimate
    p_value = 2 * (1 - _phi(abs(t_stat)))
    
    return t_stat, p_value


def extract_per_doc_f1_scores(results: Dict[str, Any], category: Optional[str] = None) -> Dict[str, float]:
    """
    Extract per-document F1 scores from results.
    
    Args:
        results: Results dictionary
        category: Specific category to extract (None for all categories combined)
        
    Returns:
        Dict mapping document ID to F1 score
    """
    scores = {}
    per_doc = results.get("per_document", {})
    
    if category:
        # Extract from specific category
        cat_data = per_doc.get(category.lower(), {})
        for doc_id, metrics in cat_data.items():
            scores[doc_id] = metrics.get("f1", 0.0)
    else:
        # Aggregate across all categories per document
        # First, collect all document IDs
        all_doc_ids = set()
        for cat_data in per_doc.values():
            all_doc_ids.update(cat_data.keys())
        
        # For each document, compute average F1 across categories
        for doc_id in all_doc_ids:
            doc_scores = []
            for cat_data in per_doc.values():
                if doc_id in cat_data:
                    doc_scores.append(cat_data[doc_id].get("f1", 0.0))
            if doc_scores:
                scores[doc_id] = sum(doc_scores) / len(doc_scores)
    
    return scores


def pair_documents(scores1: Dict[str, float], scores2: Dict[str, float], 
                   doi_to_hash: Dict[str, str]) -> Tuple[List[float], List[float], List[str]]:
    """
    Pair documents between two result sets using DOI-to-hash mapping.
    
    Args:
        scores1: Document ID -> F1 score mapping (can be hashes or DOIs)
        scores2: Document ID -> F1 score mapping (can be hashes or DOIs)
        doi_to_hash: DOI -> hash mapping
        
    Returns:
        Tuple of (paired_scores1, paired_scores2, paired_doc_ids)
    """
    # Create reverse mapping (hash -> DOI)
    hash_to_doi = {v: k for k, v in doi_to_hash.items()}
    
    # Determine which dataset uses hashes vs DOIs
    sample_id1 = next(iter(scores1.keys())) if scores1 else ""
    sample_id2 = next(iter(scores2.keys())) if scores2 else ""
    
    uses_hash1 = len(sample_id1) == 8 and not sample_id1.startswith("10.")
    uses_hash2 = len(sample_id2) == 8 and not sample_id2.startswith("10.")
    
    paired1 = []
    paired2 = []
    paired_ids = []
    
    # Try to pair documents
    for doc_id1, score1 in scores1.items():
        doc_id2 = None
        
        if uses_hash1 and uses_hash2:
            # Both use hashes, direct match
            doc_id2 = doc_id1 if doc_id1 in scores2 else None
        elif uses_hash1 and not uses_hash2:
            # scores1 uses hash, scores2 uses DOI
            doi = hash_to_doi.get(doc_id1)
            doc_id2 = doi if doi and doi in scores2 else None
        elif not uses_hash1 and uses_hash2:
            # scores1 uses DOI, scores2 uses hash
            hash_id = doi_to_hash.get(doc_id1)
            doc_id2 = hash_id if hash_id and hash_id in scores2 else None
        else:
            # Both use DOIs, direct match
            doc_id2 = doc_id1 if doc_id1 in scores2 else None
        
        if doc_id2 and doc_id2 in scores2:
            paired1.append(score1)
            paired2.append(scores2[doc_id2])
            paired_ids.append(doc_id1)  # Use ID from first dataset
    
    return paired1, paired2, paired_ids


def compute_mcnemar_from_metrics_paired(results1: Dict[str, Any], results2: Dict[str, Any], 
                                         category: Optional[str], doi_to_hash: Dict[str, str]) -> Tuple[int, int]:
    """
    Compute McNemar's test contingency values from paired results.
    
    For each document, classify as:
    - Both correct (F1 >= 0.5)
    - System 1 correct, System 2 incorrect
    - System 1 incorrect, System 2 correct
    - Both incorrect
    
    Args:
        results1: Results from system 1
        results2: Results from system 2
        category: Specific category (None for aggregate)
        doi_to_hash: DOI to hash mapping for pairing documents
        
    Returns:
        Tuple of (n01, n10) where:
        - n01: System 1 correct, System 2 incorrect
        - n10: System 1 incorrect, System 2 correct
    """
    # Extract F1 scores
    scores_dict1 = extract_per_doc_f1_scores(results1, category)
    scores_dict2 = extract_per_doc_f1_scores(results2, category)
    
    # Pair documents
    scores1, scores2, _ = pair_documents(scores_dict1, scores_dict2, doi_to_hash)
    
    n01 = 0  # System 1 correct, System 2 incorrect
    n10 = 0  # System 1 incorrect, System 2 correct
    
    # Compare paired documents using F1 >= 0.5 as threshold for "correct"
    for f1_1, f1_2 in zip(scores1, scores2):
        correct1 = f1_1 >= 0.5
        correct2 = f1_2 >= 0.5
        
        if correct1 and not correct2:
            n01 += 1
        elif not correct1 and correct2:
            n10 += 1
    
    return n01, n10


def print_results(results1_path: Path, results2_path: Path, results1: Dict, results2: Dict) -> None:
    """Print comprehensive significance test results for overall aggregated data."""
    
    # Fixed values
    alpha = 0.05
    bootstrap_samples = 10000
    
    print("\n" + "="*80)
    print("STATISTICAL SIGNIFICANCE TESTING (OVERALL)")
    print("="*80)
    
    print(f"\nSystem 1: {results1_path.name}")
    print(f"  {results1['metadata']['work_type']} vs {results1['metadata']['ground_truth']}")
    
    print(f"\nSystem 2: {results2_path.name}")
    print(f"  {results2['metadata']['work_type']} vs {results2['metadata']['ground_truth']}")
    
    print(f"\nSignificance Level (α): {alpha}")
    print(f"Bootstrap Samples: {bootstrap_samples:,}")
    
    # Extract aggregate metrics (always overall)
    metrics1 = results1["aggregate"]
    metrics2 = results2["aggregate"]
    
    f1_1 = metrics1.get("f1", 0.0)
    f1_2 = metrics2.get("f1", 0.0)
    
    print("\n" + "-"*80)
    print("AGGREGATE METRICS")
    print("-"*80)
    print(f"System 1 F1: {f1_1:.4f}")
    print(f"System 2 F1: {f1_2:.4f}")
    print(f"Difference:  {f1_1 - f1_2:+.4f}")
    
    # McNemar's Test (using properly paired documents)
    print("\n" + "-"*80)
    print("McNEMAR'S TEST (Paired Binary Classification)")
    print("-"*80)
    
    # Load DOI-to-hash mapping for McNemar test
    doi_to_hash = load_doi_hash_mapping()
    n01, n10 = compute_mcnemar_from_metrics_paired(results1, results2, None, doi_to_hash)  # Always aggregate
    chi_sq, p_value_mcnemar = mcnemar_test(n01, n10)
    
    print(f"n01 (Sys1 correct, Sys2 incorrect): {n01}")
    print(f"n10 (Sys1 incorrect, Sys2 correct): {n10}")
    print(f"Chi-squared statistic: {chi_sq:.4f}")
    print(f"P-value: {p_value_mcnemar:.4f}")
    
    if p_value_mcnemar < alpha:
        winner = "System 1" if n01 > n10 else "System 2"
        print(f"✓ SIGNIFICANT: {winner} is significantly better (p < {alpha})")
    else:
        print(f"✗ NOT SIGNIFICANT: No significant difference (p >= {alpha})")
    
    # Extract per-document F1 scores (aggregate across all categories)
    scores_dict1 = extract_per_doc_f1_scores(results1, None)  # None = aggregate
    scores_dict2 = extract_per_doc_f1_scores(results2, None)
    
    # Load DOI-to-hash mapping and pair documents
    doi_to_hash = load_doi_hash_mapping()
    scores1, scores2, paired_doc_ids = pair_documents(scores_dict1, scores_dict2, doi_to_hash)
    
    # Ensure we have paired samples
    if len(scores1) == 0:
        print("\n⚠ Warning: No paired documents found, cannot perform significance tests")
        return
    
    if len(scores1) != len(scores_dict1) or len(scores2) != len(scores_dict2):
        print(f"\n⚠ Warning: Only {len(scores1)} out of {max(len(scores_dict1), len(scores_dict2))} documents were paired")
    
    n_docs = len(scores1)
    
    # Paired t-test
    print("\n" + "-"*80)
    print("PAIRED T-TEST (Per-Document F1 Scores)")
    print("-"*80)
    print(f"Number of paired documents: {n_docs}")
    print(f"Mean F1 (System 1): {sum(scores1)/n_docs:.4f}")
    print(f"Mean F1 (System 2): {sum(scores2)/n_docs:.4f}")
    
    t_stat, p_value_t = paired_t_test(scores1, scores2)
    print(f"T-statistic: {t_stat:.4f}")
    print(f"P-value: {p_value_t:.4f}")
    
    if p_value_t < alpha:
        winner = "System 1" if t_stat > 0 else "System 2"
        print(f"✓ SIGNIFICANT: {winner} is significantly better (p < {alpha})")
    else:
        print(f"✗ NOT SIGNIFICANT: No significant difference (p >= {alpha})")
    
    # Bootstrap test
    print("\n" + "-"*80)
    print(f"BOOTSTRAP RESAMPLING TEST ({bootstrap_samples} samples)")
    print("-"*80)
    obs_diff, p_value_boot = bootstrap_test(scores1, scores2, bootstrap_samples)
    print(f"Observed mean difference: {obs_diff:.4f}")
    print(f"P-value: {p_value_boot:.4f}")
    
    if p_value_boot < alpha:
        winner = "System 1" if obs_diff > 0 else "System 2"
        print(f"✓ SIGNIFICANT: {winner} is significantly better (p < {alpha})")
    else:
        print(f"✗ NOT SIGNIFICANT: No significant difference (p >= {alpha})")
    
    # Summary
    print("\n" + "="*80)
    print("SUMMARY")
    print("="*80)
    
    sig_tests = {
        "McNemar's Test": p_value_mcnemar < alpha,
        "Paired T-Test": p_value_t < alpha,
        "Bootstrap Test": p_value_boot < alpha
    }
    
    sig_count = sum(sig_tests.values())
    
    if sig_count == 3:
        winner = "System 1" if f1_1 > f1_2 else "System 2"
        print(f"✓ ALL THREE TESTS SIGNIFICANT: {winner} is significantly better")
    elif sig_count >= 2:
        winner = "System 1" if f1_1 > f1_2 else "System 2"
        print(f"⚠ MAJORITY SIGNIFICANT ({sig_count}/3): {winner} is likely better")
    else:
        print(f"✗ INSUFFICIENT EVIDENCE: No significant difference detected")
    
    print("\nTest Results:")
    for test_name, is_sig in sig_tests.items():
        status = "✓ Significant" if is_sig else "✗ Not Significant"
        print(f"  {test_name}: {status}")
    
    print("="*80 + "\n")


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Perform statistical significance testing on overall evaluation results (α=0.05, 10000 bootstrap samples)"
    )
    parser.add_argument(
        "result1",
        type=Path,
        help="Path to first results JSON file (e.g., _overall.json)"
    )
    parser.add_argument(
        "result2",
        type=Path,
        help="Path to second results JSON file (e.g., _overall_previous.json)"
    )
    
    args = parser.parse_args()
    
    # Load results
    try:
        results1 = load_results(args.result1)
        results2 = load_results(args.result2)
    except Exception as e:
        print(f"✗ Error loading results: {e}", file=sys.stderr)
        sys.exit(1)
    
    # Print results (using default values: alpha=0.05, bootstrap_samples=10000)
    print_results(args.result1, args.result2, results1, results2)


if __name__ == "__main__":
    main()

