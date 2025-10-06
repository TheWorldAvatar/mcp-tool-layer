"""
Canonical SMILES Search Operations

This module provides fuzzy search functionality for canonical SMILES in the CBU database.
The search operates strictly on canonical SMILES inputs without any conversion.
"""

import csv
import json
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import List, Dict


def _norm(s: str) -> str:
    """Normalize for robust string equality: strip, collapse spaces, NFC."""
    if s is None:
        return ""
    s = unicodedata.normalize("NFC", s)
    s = s.replace("\u00A0", " ")  # no-break space → space
    s = " ".join(s.strip().split())  # collapse internal whitespace
    return s


def load_cbu_database_for_search() -> List[Dict[str, str]]:
    """
    Load CBU database from CSV for canonical SMILES search.

    Returns:
        List of CBU entries with canonical SMILES data
    """
    cbu_data: List[Dict[str, str]] = []
    try:
        csv_path = Path("scripts/cbu_alignment/data/full_cbus_with_canonical_smiles_updated.csv")
        with open(csv_path, "r", encoding="utf-8") as file:
            reader = csv.DictReader(file)
            for row in reader:
                canon = _norm(row.get("canonical_smiles", ""))
                formula = _norm(row.get("formula", ""))
                if not formula:
                    continue
                if not canon or canon.upper() == "N/A":
                    continue
                cbu_data.append({
                    "formula": formula,
                    "category": row.get("category", "") or "",
                    "canonical_smiles": canon,
                    "original_smiles": row.get("smiles", "") or "",
                })
    except Exception as e:
        print(f"Error loading CBU database: {e}")
    return cbu_data


def fuzzy_search_canonical_smiles(canonical_smiles: str, top_n: int = 5) -> str:
    """
    Find similar canonical SMILES in the CBU database using fuzzy string matching.

    IMPORTANT: This function accepts ONLY canonical SMILES as input.
    No SMILES conversion is performed within this function.

    Args:
        canonical_smiles: Input canonical SMILES string (must already be canonicalized)
        top_n: Number of top matches to return (default: 5)

    Returns:
        JSON string containing top N similar CBU entries with similarity scores
    """
    try:
        cbu_database = load_cbu_database_for_search()

        input_norm = _norm(canonical_smiles)
        if not cbu_database:
            return json.dumps({
                "error": "CBU database not loaded or empty",
                "input_canonical_smiles": input_norm,
                "search_successful": False
            }, indent=2, ensure_ascii=False)

        similarities = []
        # Use SequenceMatcher with autojunk disabled for stability
        for cbu_entry in cbu_database:
            db_canon = cbu_entry["canonical_smiles"]

            # Exact string equality after normalization -> score 1.0
            if input_norm == db_canon:
                score = 1.0
            else:
                score = SequenceMatcher(None, input_norm, db_canon, autojunk=False).ratio()

            similarities.append({
                "formula": cbu_entry["formula"],
                "category": cbu_entry["category"],
                "canonical_smiles": db_canon,
                "original_smiles": cbu_entry["original_smiles"],
                "similarity_score": round(float(score), 4),
            })

        similarities.sort(key=lambda x: x["similarity_score"], reverse=True)
        top_matches = similarities[:max(1, int(top_n))]

        result = {
            "input_canonical_smiles": input_norm,
            "total_database_entries": len(cbu_database),
            "search_method": "canonical_smiles_fuzzy_match",
            "top_matches_count": len(top_matches),
            "top_matches": top_matches,
            "search_successful": True
        }
        return json.dumps(result, ensure_ascii=False, indent=2)

    except Exception as e:
        error_result = {
            "input_canonical_smiles": _norm(canonical_smiles),
            "search_successful": False,
            "error": str(e),
            "total_database_entries": 0
        }
        return json.dumps(error_result, ensure_ascii=False, indent=2)


def example_usage():
    print("=== Canonical SMILES Fuzzy Search Example ===\n")

    # Example 1: exact match should be 1.0 now
    canonical_smiles_1 = "O=C([O-])c1cc(C(=O)[O-])cc(-c2ccc(-c3cc(C(=O)[O-])cc(C(=O)[O-])c3)cc2)c1"
    print("Example 1: Exact match case")
    print(f"Input canonical SMILES: {canonical_smiles_1}")
    result_1 = fuzzy_search_canonical_smiles(canonical_smiles_1, top_n=3)
    print("Results:")
    print(result_1)
    print("\n" + "=" * 60 + "\n")


if __name__ == "__main__":
    example_usage()
