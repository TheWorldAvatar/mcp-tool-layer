#!/usr/bin/env python3
"""
Fuzzy search functionality for OntoMOP entities.
"""

import json
from pathlib import Path
from typing import List, Dict, Any
from fuzzywuzzy import fuzz, process

def load_formula_data() -> List[Dict[str, Any]]:
    """Load formula data from the extracted JSON file."""
    formula_file = Path("data/ontomop_extraction/all_formulas.json")
    
    if not formula_file.exists():
        raise FileNotFoundError(f"Formula data file not found: {formula_file}")
    
    with open(formula_file, 'r', encoding='utf-8') as f:
        return json.load(f)

def entity_fuzzy_search(search_string: str, limit: int = 5) -> List[Dict[str, Any]]:
    """
    Perform fuzzy search on OntoMOP entities by formula similarity.
    
    Args:
        search_string (str): The string to search for (e.g., chemical formula)
        limit (int): Maximum number of results to return (default: 5)
        
    Returns:
        List[Dict[str, Any]]: List of matching entities with similarity scores
    """
    try:
        # Load formula data
        formula_data = load_formula_data()
        
        if not formula_data:
            return []
        
        # Extract formulas and create a mapping
        formula_texts = []
        formula_mapping = {}
        
        for item in formula_data:
            formula = item.get('formula', '')
            if formula:
                formula_texts.append(formula)
                formula_mapping[formula] = item
        
        if not formula_texts:
            return []
        
        # Perform fuzzy matching using process.extract
        # Using fuzz.ratio for exact character matching
        matches = process.extract(
            search_string, 
            formula_texts, 
            scorer=fuzz.ratio,
            limit=limit
        )
        
        # Convert matches to result format
        results = []
        for formula, score in matches:
            if formula in formula_mapping:
                entity = formula_mapping[formula].copy()
                entity['similarity_score'] = score
                entity['matched_formula'] = formula
                results.append(entity)
        
        return results
        
    except Exception as e:
        print(f"Error in fuzzy search: {e}")
        return []

def entity_fuzzy_search_partial(search_string: str, limit: int = 5) -> List[Dict[str, Any]]:
    """
    Perform fuzzy search using partial matching (better for partial formulas).
    
    Args:
        search_string (str): The string to search for
        limit (int): Maximum number of results to return (default: 5)
        
    Returns:
        List[Dict[str, Any]]: List of matching entities with similarity scores
    """
    try:
        # Load formula data
        formula_data = load_formula_data()
        
        if not formula_data:
            return []
        
        # Extract formulas and create a mapping
        formula_texts = []
        formula_mapping = {}
        
        for item in formula_data:
            formula = item.get('formula', '')
            if formula:
                formula_texts.append(formula)
                formula_mapping[formula] = item
        
        if not formula_texts:
            return []
        
        # Perform fuzzy matching using partial ratio
        matches = process.extract(
            search_string, 
            formula_texts, 
            scorer=fuzz.partial_ratio,
            limit=limit
        )
        
        # Convert matches to result format
        results = []
        for formula, score in matches:
            if formula in formula_mapping:
                entity = formula_mapping[formula].copy()
                entity['similarity_score'] = score
                entity['matched_formula'] = formula
                entity['match_type'] = 'partial'
                results.append(entity)
        
        return results
        
    except Exception as e:
        print(f"Error in partial fuzzy search: {e}")
        return []

def entity_fuzzy_search_token_sort(search_string: str, limit: int = 5) -> List[Dict[str, Any]]:
    """
    Perform fuzzy search using token sort ratio (good for different word orders).
    
    Args:
        search_string (str): The string to search for
        limit (int): Maximum number of results to return (default: 5)
        
    Returns:
        List[Dict[str, Any]]: List of matching entities with similarity scores
    """
    try:
        # Load formula data
        formula_data = load_formula_data()
        
        if not formula_data:
            return []
        
        # Extract formulas and create a mapping
        formula_texts = []
        formula_mapping = {}
        
        for item in formula_data:
            formula = item.get('formula', '')
            if formula:
                formula_texts.append(formula)
                formula_mapping[formula] = item
        
        if not formula_texts:
            return []
        
        # Perform fuzzy matching using token sort ratio
        matches = process.extract(
            search_string, 
            formula_texts, 
            scorer=fuzz.token_sort_ratio,
            limit=limit
        )
        
        # Convert matches to result format
        results = []
        for formula, score in matches:
            if formula in formula_mapping:
                entity = formula_mapping[formula].copy()
                entity['similarity_score'] = score
                entity['matched_formula'] = formula
                entity['match_type'] = 'token_sort'
                results.append(entity)
        
        return results
        
    except Exception as e:
        print(f"Error in token sort fuzzy search: {e}")
        return []

def get_best_fuzzy_matches(search_string: str, limit: int = 5) -> List[Dict[str, Any]]:
    """
    Get the best fuzzy matches using multiple scoring methods and return the top results.
    
    Args:
        search_string (str): The string to search for
        limit (int): Maximum number of results to return (default: 5)
        
    Returns:
        List[Dict[str, Any]]: List of best matching entities with similarity scores
    """
    try:
        # Get results from different fuzzy matching methods
        exact_matches = entity_fuzzy_search(search_string, limit * 2)
        partial_matches = entity_fuzzy_search_partial(search_string, limit * 2)
        token_matches = entity_fuzzy_search_token_sort(search_string, limit * 2)
        
        # Combine all results and deduplicate by subject
        all_matches = {}
        
        for match in exact_matches + partial_matches + token_matches:
            subject = match.get('subject', '')
            if subject not in all_matches or match['similarity_score'] > all_matches[subject]['similarity_score']:
                all_matches[subject] = match
        
        # Sort by similarity score and return top results
        sorted_matches = sorted(all_matches.values(), key=lambda x: x['similarity_score'], reverse=True)
        return sorted_matches[:limit]
        
    except Exception as e:
        print(f"Error in best fuzzy matches: {e}")
        return []

if __name__ == "__main__":
    # Test the fuzzy search
    test_query = "[Me2NH2]5[V6O6(OCH3)9(SO4)4]"
    print(f"Testing fuzzy search with: {test_query}")
    
    results = get_best_fuzzy_matches(test_query, 5)
    print(f"Found {len(results)} results:")
    
    for i, result in enumerate(results, 1):
        print(f"{i}. {result.get('matched_formula', 'N/A')} (Score: {result.get('similarity_score', 0)})")
        print(f"   Subject: {result.get('subject', 'N/A')}")
        print(f"   Type: {result.get('subjectType', 'N/A')}")
        
        print()
