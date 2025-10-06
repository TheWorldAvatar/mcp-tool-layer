#!/usr/bin/env python3
"""
Label listing functionality for OntoMOP entities.
Provides exact label matching and listing capabilities.
"""

import json
from pathlib import Path
from typing import List, Dict, Any, Optional

def load_complete_label_data() -> Dict[str, Any]:
    """Load complete label index from the extracted JSON file."""
    index_file = Path("data/ontomop_extraction/complete_label_index.json")
    
    if not index_file.exists():
        # Fallback to old formula data
        formula_file = Path("data/ontomop_extraction/all_formulas.json")
        if formula_file.exists():
            with open(formula_file, 'r', encoding='utf-8') as f:
                formula_data = json.load(f)
            return {
                "all_entities": formula_data,
                "label_to_entity_map": {},
                "entities_by_type": {},
                "statistics": {"total_entities": len(formula_data)}
            }
        else:
            raise FileNotFoundError(f"Label data files not found: {index_file} or {formula_file}")
    
    with open(index_file, 'r', encoding='utf-8') as f:
        return json.load(f)

def load_formula_data() -> List[Dict[str, Any]]:
    """Load formula data (backward compatibility)."""
    complete_data = load_complete_label_data()
    return complete_data.get("all_entities", [])

def list_all_labels(limit: Optional[int] = None) -> List[str]:
    """
    List all available labels (formulas) in the OntoMOP dataset.
    
    Args:
        limit (Optional[int]): Maximum number of labels to return. If None, return all.
        
    Returns:
        List[str]: List of available labels/formulas
    """
    try:
        formula_data = load_formula_data()
        
        if not formula_data:
            return []
        
        # Extract unique formulas
        labels = []
        seen = set()
        
        for item in formula_data:
            formula = item.get('formula', '')
            if formula and formula not in seen:
                labels.append(formula)
                seen.add(formula)
        
        # Sort labels alphabetically
        labels.sort()
        
        # Apply limit if specified
        if limit is not None:
            labels = labels[:limit]
            
        return labels
        
    except Exception as e:
        print(f"Error listing labels: {e}")
        return []

def search_labels_by_pattern(pattern: str, case_sensitive: bool = False, limit: Optional[int] = None) -> List[str]:
    """
    Search for labels that contain a specific pattern.
    
    Args:
        pattern (str): Pattern to search for in labels
        case_sensitive (bool): Whether to perform case-sensitive search (default: False)
        limit (Optional[int]): Maximum number of results to return
        
    Returns:
        List[str]: List of matching labels
    """
    try:
        all_labels = list_all_labels()
        
        if not all_labels:
            return []
        
        # Prepare pattern for matching
        search_pattern = pattern if case_sensitive else pattern.lower()
        
        # Filter labels containing the pattern
        matching_labels = []
        for label in all_labels:
            search_label = label if case_sensitive else label.lower()
            if search_pattern in search_label:
                matching_labels.append(label)
        
        # Apply limit if specified
        if limit is not None:
            matching_labels = matching_labels[:limit]
            
        return matching_labels
        
    except Exception as e:
        print(f"Error searching labels by pattern: {e}")
        return []

def get_entity_by_label(label: str, exact_match: bool = True) -> List[Dict[str, Any]]:
    """
    Get entity details by exact label match.
    
    Args:
        label (str): The exact label to search for
        exact_match (bool): Whether to require exact match or allow case-insensitive partial match
        
    Returns:
        List[Dict[str, Any]]: List of entities with the specified label
    """
    try:
        formula_data = load_formula_data()
        
        if not formula_data:
            return []
        
        matching_entities = []
        
        for item in formula_data:
            formula = item.get('formula', '')
            
            if exact_match:
                # Exact match
                if formula == label:
                    entity = item.copy()
                    entity['match_type'] = 'exact'
                    matching_entities.append(entity)
            else:
                # Case-insensitive partial match
                if label.lower() in formula.lower():
                    entity = item.copy()
                    entity['match_type'] = 'partial'
                    matching_entities.append(entity)
        
        return matching_entities
        
    except Exception as e:
        print(f"Error getting entity by label: {e}")
        return []

def get_label_statistics() -> Dict[str, Any]:
    """
    Get statistics about the available labels.
    
    Returns:
        Dict[str, Any]: Statistics including total count, sample labels, etc.
    """
    try:
        formula_data = load_formula_data()
        
        if not formula_data:
            return {
                "total_entities": 0,
                "unique_labels": 0,
                "sample_labels": [],
                "label_length_stats": {}
            }
        
        # Get unique labels
        unique_labels = set()
        label_lengths = []
        
        for item in formula_data:
            formula = item.get('formula', '')
            if formula:
                unique_labels.add(formula)
                label_lengths.append(len(formula))
        
        unique_labels_list = sorted(list(unique_labels))
        
        # Calculate length statistics
        if label_lengths:
            length_stats = {
                "min_length": min(label_lengths),
                "max_length": max(label_lengths),
                "avg_length": sum(label_lengths) / len(label_lengths)
            }
        else:
            length_stats = {"min_length": 0, "max_length": 0, "avg_length": 0}
        
        return {
            "total_entities": len(formula_data),
            "unique_labels": len(unique_labels_list),
            "sample_labels": unique_labels_list[:10],  # First 10 labels as samples
            "label_length_stats": length_stats
        }
        
    except Exception as e:
        print(f"Error getting label statistics: {e}")
        return {
            "error": str(e),
            "total_entities": 0,
            "unique_labels": 0,
            "sample_labels": [],
            "label_length_stats": {}
        }

def find_labels_by_elements(elements: List[str], exact_composition: bool = False, limit: Optional[int] = None) -> List[str]:
    """
    Find labels that contain specific chemical elements.
    
    Args:
        elements (List[str]): List of chemical element symbols (e.g., ['V', 'O', 'C'])
        exact_composition (bool): If True, labels must contain ONLY the specified elements
        
    Returns:
        List[str]: List of labels containing the specified elements
    """
    try:
        all_labels = list_all_labels()
        
        if not all_labels:
            return []
        
        matching_labels = []
        
        for label in all_labels:
            if exact_composition:
                # Check if label contains only the specified elements
                # This is a simplified check - would need more sophisticated parsing for real use
                contains_only_specified = True
                for char in label:
                    if char.isalpha() and char.upper() not in [e.upper() for e in elements]:
                        contains_only_specified = False
                        break
                if contains_only_specified and any(e.upper() in label.upper() for e in elements):
                    matching_labels.append(label)
            else:
                # Check if label contains all specified elements
                if all(e.upper() in label.upper() for e in elements):
                    matching_labels.append(label)
        
        if limit is not None:
            matching_labels = matching_labels[:limit]
        
        return matching_labels
        
    except Exception as e:
        print(f"Error finding labels by elements: {e}")
        return []

def list_entities_by_type(entity_type: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """
    List entities of a specific type.
    
    Args:
        entity_type (str): Type of entities to list (e.g., 'ChemicalBuildingUnit', 'MetalOrganicPolyhedron')
        limit (Optional[int]): Maximum number of entities to return
        
    Returns:
        List[Dict[str, Any]]: List of entities of the specified type
    """
    try:
        complete_data = load_complete_label_data()
        entities_by_type = complete_data.get("entities_by_type", {})
        
        entities = entities_by_type.get(entity_type, [])
        
        if limit is not None:
            entities = entities[:limit]
            
        return entities
        
    except Exception as e:
        print(f"Error listing entities by type: {e}")
        return []

def get_available_entity_types() -> List[str]:
    """
    Get all available entity types in the dataset.
    
    Returns:
        List[str]: List of available entity types
    """
    try:
        complete_data = load_complete_label_data()
        entities_by_type = complete_data.get("entities_by_type", {})
        
        return sorted(list(entities_by_type.keys()))
        
    except Exception as e:
        print(f"Error getting entity types: {e}")
        return []

def search_entities_by_label_and_type(label_pattern: str, entity_type: Optional[str] = None, exact_match: bool = False, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """
    Search entities by label pattern and optionally filter by type.
    
    Args:
        label_pattern (str): Pattern to search for in labels
        entity_type (Optional[str]): Optional entity type filter
        exact_match (bool): Whether to require exact label match
        limit (Optional[int]): Maximum number of results to return
        
    Returns:
        List[Dict[str, Any]]: List of matching entities
    """
    try:
        complete_data = load_complete_label_data()
        
        if entity_type:
            # Search within specific entity type
            entities = complete_data.get("entities_by_type", {}).get(entity_type, [])
        else:
            # Search all entities
            entities = complete_data.get("all_entities", [])
        
        matching_entities = []
        
        for entity in entities:
            label = entity.get('label', '')
            
            if exact_match:
                if label == label_pattern:
                    matching_entities.append(entity)
            else:
                if label_pattern.lower() in label.lower():
                    matching_entities.append(entity)
        
        if limit is not None:
            matching_entities = matching_entities[:limit]
            
        return matching_entities
        
    except Exception as e:
        print(f"Error searching entities: {e}")
        return []

def get_entity_type_statistics() -> Dict[str, Any]:
    """
    Get detailed statistics about entity types.
    
    Returns:
        Dict[str, Any]: Statistics about entity types
    """
    try:
        complete_data = load_complete_label_data()
        return complete_data.get("statistics", {})
        
    except Exception as e:
        print(f"Error getting entity type statistics: {e}")
        return {}

if __name__ == "__main__":
    # Test the label listing functionality
    print("Testing label listing functionality...")
    
    # Test 1: List first 10 labels
    print("\n1. First 10 labels:")
    labels = list_all_labels(limit=10)
    for i, label in enumerate(labels, 1):
        print(f"   {i}. {label}")
    
    # Test 2: Get statistics
    print("\n2. Label statistics:")
    stats = get_label_statistics()
    print(f"   Total entities: {stats['total_entities']}")
    print(f"   Unique labels: {stats['unique_labels']}")
    print(f"   Sample labels: {stats['sample_labels'][:5]}")
    
    # Test 3: Search by pattern
    print("\n3. Labels containing 'V6O6':")
    pattern_results = search_labels_by_pattern("V6O6", limit=5)
    for label in pattern_results:
        print(f"   - {label}")
    
    # Test 4: Get entity by exact label
    if pattern_results:
        test_label = pattern_results[0]
        print(f"\n4. Entity details for '{test_label}':")
        entities = get_entity_by_label(test_label)
        for entity in entities:
            print(f"   Subject: {entity.get('subject', 'N/A')}")
            print(f"   Type: {entity.get('subjectType', 'N/A')}")
            print(f"   Formula: {entity.get('formula', 'N/A')}")
    
    # Test 5: Find labels by elements
    print("\n5. Labels containing elements V, O, and C:")
    element_results = find_labels_by_elements(['V', 'O', 'C'], limit=5)
    for label in element_results[:5]:
        print(f"   - {label}")
