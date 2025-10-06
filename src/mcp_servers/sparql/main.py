from fastmcp import FastMCP
from typing import Any
from src.mcp_descriptions.sparql import SPARQL_QUERY_DESCRIPTION
from src.mcp_servers.sparql.ontomop_operation import query_sparql
from src.utils.global_logger import get_logger, mcp_tool_logger
from src.mcp_servers.sparql.ontology_tbox_rag import get_ontology_tbox
from src.mcp_servers.sparql.ontology_sparql_example_rag import list_sparql_example_names_and_descriptions, retrieve_sparql_example, insert_sparql_example, update_sparql_example, delete_sparql_example, repair_sparql_examples_file
from src.mcp_servers.sparql.chemical_fuzzy_search import chemical_fuzzy_search
from src.mcp_servers.sparql.fuzzy_search import get_best_fuzzy_matches
from src.mcp_servers.sparql.label_listing import list_all_labels, get_entity_by_label, get_label_statistics
from src.mcp_servers.sparql.ontology_sampling import generate_improved_sampling_report
from typing import Literal, Optional, List

# -------------------- CONFIG --------------------
mcp = FastMCP(name="sparql_operations", instructions="""This is a tool to perform SPARQL queries against knowledge graph endpoints. It provides access to various SPARQL endpoints including OntoMOP, OntoSynthesis, and OntoSpecies knowledge graphs for querying Metal-Organic Polyhedra, chemical synthesis data, and chemical species information.
""")
logger = get_logger("mcp_server", "sparql_main")


@mcp.prompt(name="instruction")
def instruction_prompt():
    return """
    You are a tool to perform SPARQL queries against knowledge graph endpoints. 

    To understand the specifc knowledge graph, you can use the `get_ontology_tbox` tool to get the T-Box of the knowledge graph.

    The T-Box is the schema of the knowledge graph and will help you to figure out the SPARQL queries to use.

    Then you can use the `query_sparql` tool to perform the SPARQL queries.

    Remeber to include namespace prefixes in the SPARQL queries, use different modes to perform the queries.

    Try different SPARQL queries until you get the desired results. 
    """


# -------------------- SPARQL TOOLS --------------------

@mcp.tool(name="list_sparql_example_names", description="List the name and description of the SPARQL examples.")
@mcp_tool_logger
def list_sparql_example_names_tool() -> Any:
    return list_sparql_example_names_and_descriptions()


@mcp.tool(name="retrieve_sparql_example", description="Retrieve the SPARQL example with the given name.")
@mcp_tool_logger
def retrieve_sparql_example_tool(example_name: str) -> Any:
    return retrieve_sparql_example(example_name=example_name)
 


@mcp.tool(name="insert_sparql_example", description="Insert the SPARQL example with the given name.")
@mcp_tool_logger
def insert_sparql_example_tool(example_name: str, description: str, example_query: str, ontology_name: Literal["ontosynthesis", "ontomops", "ontospecies"], example_result: str = "") -> Any:
    """
    Insert a new SPARQL example into the warehouse.
    
    Args:
        example_name: Name of the SPARQL example
        description: Description of the SPARQL example, especially the purpose of the SPARQL query
        example_query: The SPARQL query string
        ontology_name: The ontology this example belongs to
        example_result: Optional result of the example
        
    Returns:
        Dictionary with detailed status information
    """
    return insert_sparql_example(example_name=example_name, example_query=example_query, ontology_name=ontology_name, example_result=example_result, description=description)

@mcp.tool(name="update_sparql_example", description="Update an existing SPARQL example.")
@mcp_tool_logger
def update_sparql_example_tool(example_name: str, description: str = None, example_query: str = None, ontology_name: str = None, example_result: str = None) -> Any:
    """
    Update an existing SPARQL example.
    
    Args:
        example_name: Name of the SPARQL example to update
        description: New description (optional), especially the purpose of the SPARQL query
        example_query: New SPARQL query (optional)
        ontology_name: New ontology name (optional)
        example_result: New example result (optional)
        
    Returns:
        Dictionary with detailed status information
    """
    return update_sparql_example(example_name=example_name, description=description, example_query=example_query, ontology_name=ontology_name, example_result=example_result)

@mcp.tool(name="delete_sparql_example", description="Delete a SPARQL example by name.")
@mcp_tool_logger
def delete_sparql_example_tool(example_name: str) -> Any:
    """
    Delete a SPARQL example by name.
    
    Args:
        example_name: Name of the SPARQL example to delete
        
    Returns:
        Dictionary with detailed status information
    """
    return delete_sparql_example(example_name=example_name)

@mcp.tool(name="repair_sparql_examples_file", description="Repair corrupted SPARQL examples JSON file.")
@mcp_tool_logger
def repair_sparql_examples_file_tool() -> Any:
    """
    Repair corrupted SPARQL examples JSON file.
    This tool can be used if the examples file becomes corrupted due to file locking issues.
    """
    return repair_sparql_examples_file()
 

@mcp.tool(name="query_ontomop", description="Execute a SPARQL query against the OntoMOP knowledge graph (Metal-Organic Polyhedra data).", tags=["sparql", "ontomop"])
@mcp_tool_logger
def query_ontomop_tool(
    query: str,
    raw_json: bool = False,
    mode: str = "probe",
) -> Any:
    """
    Execute a SPARQL query against the OntoMOP knowledge graph.
    
    The OntoMOP knowledge graph contains:
    - 2,383 Metal-Organic Polyhedra (MOPs)
    - 150 Chemical Building Units (CBUs)
    - 166 MOPs with CCDC numbers
    - Comprehensive relationship data between MOPs and CBUs
    
    Args:
        query: The SPARQL 1.1 query string to execute.
        raw_json: If True, return the full SPARQL JSON document; if False, return simplified results.
        mode: Query mode - only "probe" mode is supported. Default: "probe".
        
    Returns:
        - probe mode: Simplified results limited to first 10 entries
        - SELECT queries → list of dicts (unless raw_json=True)
        - ASK queries → bool
        - Other queries → raw JSON response
        
    """
    endpoint_url = "http://68.183.227.15:3838/blazegraph/namespace/ontomops_ogm/sparql"
    return query_sparql(query=query, endpoint_url=endpoint_url, raw_json=raw_json, mode=mode)

@mcp.tool(name="query_ontosynthesis", description="Execute a SPARQL query against the OntoSynthesis knowledge graph (chemical synthesis data).", tags=["sparql", "ontosynthesis"])
@mcp_tool_logger
def query_ontosynthesis_tool(
    query: str,
    raw_json: bool = False,
    mode: str = "probe",
) -> Any:
    """
    Execute a SPARQL query against the OntoSynthesis knowledge graph.
    
    The OntoSynthesis knowledge graph contains:
    - 173 chemical synthesis processes
    - Synthesis steps and conditions
    - Chemical inputs and outputs
    - Characterization data (NMR, IR, elemental analysis)
    - DOI references for literature provenance
    
    Args:
        query: The SPARQL 1.1 query string to execute.
        raw_json: If True, return the full SPARQL JSON document; if False, return simplified results.
        mode: Query mode - only "probe" mode is supported. Default: "probe".
        
    Returns:
        - probe mode: Simplified results limited to first 10 entries
        - SELECT queries → list of dicts (unless raw_json=True)
        - ASK queries → bool
        - Other queries → raw JSON response
    """
    endpoint_url = "http://68.183.227.15:3838/blazegraph/namespace/OntoSynthesisTestEncoding2/sparql"
    return query_sparql(query=query, endpoint_url=endpoint_url, raw_json=raw_json, mode=mode)

@mcp.tool(name="query_ontospecies", description="Execute a SPARQL query against the OntoSpecies knowledge graph (chemical species data).", tags=["sparql", "ontospecies"])
@mcp_tool_logger
def query_ontospecies_tool(
    query: str,
    raw_json: bool = False,
    mode: str = "probe",
) -> Any:
    """
    Execute a SPARQL query against the OntoSpecies knowledge graph.
    
    The OntoSpecies knowledge graph contains:
    - 36,631 chemical species
    - 36,490 species with molecular formulas
    - 73,100 species with SMILES notation
    - 216,910 species with IUPAC names
    - Elemental analysis and spectroscopy data
    
    Args:
        query: The SPARQL 1.1 query string to execute.
        raw_json: If True, return the full SPARQL JSON document; if False, return simplified results.
        mode: Query mode - only "probe" mode is supported. Default: "probe".
        
    Returns:
        - probe mode: Simplified results limited to first 10 entries
        - SELECT queries → list of dicts (unless raw_json=True)
        - ASK queries → bool
        - Other queries → raw JSON response
    """
    endpoint_url = "http://178.128.105.213:3838/blazegraph/namespace/ontospecies/sparql"
    return query_sparql(query=query, endpoint_url=endpoint_url, raw_json=raw_json, mode=mode)


# @mcp.tool(name="get_ontology_tbox", description="Get the T-Box for a given ontology name. T-Box only provides schema of the knowledge graph. To get the data of the knowledge graph, you need to query the knowledge graph directly.", tags=["ontology", "tbox"])
# @mcp_tool_logger
# def get_ontology_tbox_tool(
#     ontology_name: Literal["ontosynthesis", "ontomops", "ontospecies"],
# ) -> Any:
#     """
#     Get the T-Box for a given ontology name.
#     """
#     return get_ontology_tbox(ontology_name)


@mcp.tool(name="chemical_fuzzy_search", description="Perform chemical fuzzy search on OntoMOP entities using molecular formula and weight matching. This advanced search compares chemical composition and molecular weights to find the best matching entities. Supports both fuzzy and strict matching modes.", tags=["chemical_search", "ontomop", "formula", "molecular_weight"])
@mcp_tool_logger
def chemical_fuzzy_search_tool(
    target_name: str,
    target_formula: str,
    target_mol_weight: float,
    target_smiles: Optional[str] = None,
    target_inchi: Optional[str] = None,
    target_inchikey: Optional[str] = None,
    limit: int = 100,
    strict: bool = False
) -> Any:
    """
    Perform chemical fuzzy search on OntoMOP entities using molecular formula and weight matching.
    
    This tool performs advanced chemical matching by:
    1. Parsing the target molecular formula and calculating expected molecular weight
    2. Comparing against all available KG entities (formulas, labels, identifiers)
    3. Scoring matches based on exact formula match (6 points) + molecular weight similarity (0-4 points)
    4. Returning the best matching entities sorted by total score
    
    Matching Modes:
    - Fuzzy mode (strict=False): Returns all results ranked by similarity score
    - Strict mode (strict=True): Only returns entities with exact molecular formula match
    
    Strict mode is useful when you want to find different representations of the same chemical 
    composition (e.g., "[Cu2(C10H10N2)2]" vs "C20H20Cu2N4" - same atoms, different notation).

    Default limit is 100, you can and are encouraged to set it to a larger number if you need more results.
    
    Args:
        target_name (str): Name/description of the target compound for reference
        target_formula (str): Target molecular formula in standard notation (e.g., "C16H10O4")
        target_mol_weight (float): Target molecular weight in g/mol (e.g., 266.25)
        target_smiles (str, optional): Target SMILES notation for additional reference
        target_inchi (str, optional): Target InChI string for additional reference  
        target_inchikey (str, optional): Target InChI key for additional reference
        limit (int): Maximum number of results to return (default: 100)
        strict (bool): If True, only return entities with exact molecular formula match (default: False)
        
    Returns:
        Dictionary with search results including:
        - status: "success", "no_results", or "error"
        - message: Human-readable status message
        - target_info: Information about the target compound
        - total_results: Number of matches found
        - results: List of matching entities with scores, formulas, and metadata
        
    Scoring System:
        - Formula match: 6 points for exact molecular formula match
        - Molecular weight similarity: 0-4 points based on relative error
          * ≤0.3% error: 4 points
          * ≤0.7% error: 3 points  
          * ≤1.5% error: 2 points
          * ≤3.0% error: 1 point
          * >3.0% error: 0 points
    """
    try:
        results = chemical_fuzzy_search(
            target_name=target_name,
            target_formula=target_formula,
            target_mol_weight=target_mol_weight,
            target_smiles=target_smiles,
            target_inchi=target_inchi,
            target_inchikey=target_inchikey,
            limit=limit,
            strict=strict
        )
        
        if not results:
            mode_text = " (strict mode)" if strict else ""
            return {
                "status": "no_results",
                "message": f"No matching entities found for '{target_name}' ({target_formula}){mode_text}",
                "target_info": {
                    "name": target_name,
                    "formula": target_formula,
                    "mol_weight": target_mol_weight,
                    "smiles": target_smiles,
                    "inchi": target_inchi,
                    "inchikey": target_inchikey
                },
                "results": []
            }
        
        mode_text = " (strict mode)" if strict else ""
        return {
            "status": "success",
            "message": f"Found {len(results)} matching entities for '{target_name}' ({target_formula}){mode_text}",
            "target_info": {
                "name": target_name,
                "formula": target_formula,
                "mol_weight": target_mol_weight,
                "smiles": target_smiles,
                "inchi": target_inchi,
                "inchikey": target_inchikey
            },
            "total_results": len(results),
            "results": results
        }
        
    except FileNotFoundError as e:
        return {
            "status": "error",
            "message": "OntoMOP data files not found. Please ensure extraction files are available.",
            "error": str(e),
            "target_info": {
                "name": target_name,
                "formula": target_formula,
                "mol_weight": target_mol_weight
            },
            "results": []
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Error during chemical fuzzy search: {str(e)}",
            "error": str(e),
            "target_info": {
                "name": target_name,
                "formula": target_formula,
                "mol_weight": target_mol_weight
            },
            "results": []
        }

@mcp.tool(name="text_fuzzy_search", description="Perform text-based fuzzy search on OntoMOP entities using string similarity matching. Complements chemical_fuzzy_search for broader text-based searches.", tags=["text_search", "ontomop", "fuzzy", "string_similarity"])
@mcp_tool_logger
def text_fuzzy_search_tool(
    search_string: str,
    limit: int = 5
) -> Any:
    """
    Perform text-based fuzzy search on OntoMOP entities using string similarity matching.
    
    This tool performs traditional text-based fuzzy matching using multiple algorithms:
    1. Exact ratio matching for precise character-by-character comparison
    2. Partial ratio matching for substring/partial formula matching
    3. Token sort ratio for handling different word/element orders
    
    Use this tool when:
    - You want to find entities with similar text/formula patterns
    - You have partial or approximate formula strings
    - You need broader text-based matching beyond chemical composition
    - You want to cross-validate results with chemical_fuzzy_search
    
    Args:
        search_string (str): The text/formula string to search for (e.g., "C16H10O4", "[Cu2(C10H10N2)2]")
        limit (int): Maximum number of results to return (default: 5)
        
    Returns:
        Dictionary with search results including:
        - status: "success", "no_results", or "error"
        - message: Human-readable status message
        - search_string: The original search term
        - total_results: Number of matches found
        - results: List of matching entities with similarity scores and metadata
        
    Scoring System:
        - Uses multiple fuzzy matching algorithms (ratio, partial_ratio, token_sort_ratio)
        - Scores range from 0-100 based on string similarity
        - Results are deduplicated and sorted by highest similarity score
    """
    try:
        results = get_best_fuzzy_matches(search_string, limit)
        
        if not results:
            return {
                "status": "no_results",
                "message": f"No matching entities found for text search '{search_string}'",
                "search_string": search_string,
                "results": []
            }
        
        return {
            "status": "success",
            "message": f"Found {len(results)} matching entities for text search '{search_string}'",
            "search_string": search_string,
            "total_results": len(results),
            "results": results
        }
        
    except FileNotFoundError as e:
        return {
            "status": "error",
            "message": "Formula data file not found. Please run the extraction script first.",
            "error": str(e),
            "search_string": search_string,
            "results": []
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Error during text fuzzy search: {str(e)}",
            "error": str(e),
            "search_string": search_string,
            "results": []
        }

# -------------------- LABEL LISTING TOOLS --------------------

@mcp.tool(name="list_labels_by_category", description="List available labels/formulas by entity category in the OntoMOP dataset.", tags=["label_listing", "ontomop", "category"])
@mcp_tool_logger
def list_labels_by_category_tool(
    category: Literal["ChemicalBuildingUnit", "MetalOrganicPolyhedron", "AssemblyModel", "GenericBuildingUnitType", "MetalSite", "OrganicSite", "all"] = "all",
    limit: Optional[int] = None
) -> Any:
    """
    List available labels/formulas by entity category in the OntoMOP dataset.
    
    Args:
        category: Entity category to filter by. Options: ChemicalBuildingUnit, MetalOrganicPolyhedron, AssemblyModel, GenericBuildingUnitType, MetalSite, OrganicSite, or all
        limit: Maximum number of labels to return. If None, return all.
        
    Returns:
        Dictionary with status and list of labels for the specified category
    """
    try:
        import json
        from pathlib import Path
        
        results = {"labels": [], "formulas": [], "identifiers": []}
        
        if category == "all" or category == "ChemicalBuildingUnit":
            # Load formulas (CBUs have formulas)
            formula_file = Path("data/ontomop_extraction/all_formulas.json")
            if formula_file.exists():
                with open(formula_file, 'r', encoding='utf-8') as f:
                    formula_data = json.load(f)
                cbu_formulas = [item['formula'] for item in formula_data 
                              if item.get('subjectType', '').endswith('ChemicalBuildingUnit')]
                if category == "ChemicalBuildingUnit":
                    results["formulas"] = cbu_formulas[:limit] if limit else cbu_formulas
                else:
                    results["formulas"].extend(cbu_formulas)
        
        if category == "all" or category in ["MetalOrganicPolyhedron", "AssemblyModel", "GenericBuildingUnitType", "MetalSite", "OrganicSite"]:
            # Load labels
            labels_file = Path("data/ontomop_extraction/all_labels.json")
            if labels_file.exists():
                with open(labels_file, 'r', encoding='utf-8') as f:
                    labels_data = json.load(f)
                
                if category == "all":
                    filtered_labels = [item['label'] for item in labels_data]
                else:
                    filtered_labels = [item['label'] for item in labels_data 
                                     if item.get('subjectType', '').endswith(category)]
                
                results["labels"] = filtered_labels[:limit] if limit else filtered_labels
        
        if category == "all" or category == "MetalOrganicPolyhedron":
            # Load identifiers (MOPs have CCDC numbers)
            identifiers_file = Path("data/ontomop_extraction/all_identifiers.json")
            if identifiers_file.exists():
                with open(identifiers_file, 'r', encoding='utf-8') as f:
                    identifiers_data = json.load(f)
                mop_identifiers = [item['identifier'] for item in identifiers_data 
                                 if 'MetalOrganicPolyhedron' in item.get('subjectType', '')]
                if category == "MetalOrganicPolyhedron":
                    results["identifiers"] = mop_identifiers[:limit] if limit else mop_identifiers
                else:
                    results["identifiers"].extend(mop_identifiers)
        
        # Combine all results
        all_items = results["labels"] + results["formulas"] + results["identifiers"]
        if limit and category == "all":
            all_items = all_items[:limit]
        
        total_count = len(all_items)
        
        return {
            "status": "success",
            "message": f"Found {total_count} items for category '{category}'" + (f" (limited to {limit})" if limit else ""),
            "category": category,
            "total_items": total_count,
            "labels": results["labels"] if category != "all" else [],
            "formulas": results["formulas"] if category != "all" else [],
            "identifiers": results["identifiers"] if category != "all" else [],
            "all_items": all_items if category == "all" else []
        }
        
    except FileNotFoundError as e:
        return {
            "status": "error",
            "message": "Data files not found. Please ensure extraction files are available.",
            "error": str(e),
            "category": category,
            "labels": [],
            "formulas": [],
            "identifiers": [],
            "all_items": []
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Error listing labels by category: {str(e)}",
            "category": category,
            "labels": [],
            "formulas": [],
            "identifiers": [],
            "all_items": []
        }


@mcp.tool(name="get_entity_details_by_label", description="Get comprehensive entity details by label/formula/identifier match.", tags=["label_listing", "ontomop", "entity"])
@mcp_tool_logger
def get_entity_details_by_label_tool(
    search_term: str,
    exact_match: bool = True
) -> Any:
    """
    Get comprehensive entity details by searching across labels, formulas, and identifiers.
    
    Args:
        search_term (str): The label, formula, or identifier to search for
        exact_match (bool): Whether to require exact match or allow case-insensitive partial match
        
    Returns:
        Dictionary with status and comprehensive entity details
    """
    try:
        import json
        from pathlib import Path
        
        results = {
            "label_matches": [],
            "formula_matches": [],
            "identifier_matches": []
        }
        
        # Search in labels
        labels_file = Path("data/ontomop_extraction/all_labels.json")
        if labels_file.exists():
            with open(labels_file, 'r', encoding='utf-8') as f:
                labels_data = json.load(f)
            
            for item in labels_data:
                label = item.get('label', '')
                if exact_match:
                    if label == search_term:
                        results["label_matches"].append(item)
                else:
                    if search_term.lower() in label.lower():
                        results["label_matches"].append(item)
        
        # Search in formulas  
        formula_file = Path("data/ontomop_extraction/all_formulas.json")
        if formula_file.exists():
            with open(formula_file, 'r', encoding='utf-8') as f:
                formula_data = json.load(f)
            
            for item in formula_data:
                formula = item.get('formula', '')
                if exact_match:
                    if formula == search_term:
                        results["formula_matches"].append(item)
                else:
                    if search_term.lower() in formula.lower():
                        results["formula_matches"].append(item)
        
        # Search in identifiers
        identifiers_file = Path("data/ontomop_extraction/all_identifiers.json")
        if identifiers_file.exists():
            with open(identifiers_file, 'r', encoding='utf-8') as f:
                identifiers_data = json.load(f)
            
            for item in identifiers_data:
                identifier = item.get('identifier', '')
                if exact_match:
                    if identifier == search_term:
                        results["identifier_matches"].append(item)
                else:
                    if search_term.lower() in identifier.lower():
                        results["identifier_matches"].append(item)
        
        # Count total matches
        total_matches = len(results["label_matches"]) + len(results["formula_matches"]) + len(results["identifier_matches"])
        
        if total_matches == 0:
            return {
                "status": "no_results",
                "message": f"No entities found for search term '{search_term}'",
                "search_term": search_term,
                "exact_match": exact_match,
                "label_matches": [],
                "formula_matches": [],
                "identifier_matches": [],
                "total_matches": 0
            }
        
        return {
            "status": "success",
            "message": f"Found {total_matches} matches for search term '{search_term}'",
            "search_term": search_term,
            "exact_match": exact_match,
            "total_matches": total_matches,
            "label_matches": results["label_matches"],
            "formula_matches": results["formula_matches"],
            "identifier_matches": results["identifier_matches"]
        }
        
    except FileNotFoundError as e:
        return {
            "status": "error",
            "message": "Data files not found. Please ensure extraction files are available.",
            "error": str(e),
            "search_term": search_term,
            "label_matches": [],
            "formula_matches": [],
            "identifier_matches": [],
            "total_matches": 0
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Error retrieving entity details: {str(e)}",
            "search_term": search_term,
            "label_matches": [],
            "formula_matches": [],
            "identifier_matches": [],
            "total_matches": 0
        }

@mcp.tool(name="get_ontomop_statistics", description="Get comprehensive statistics about all OntoMOP data categories.", tags=["label_listing", "ontomop", "statistics"])
@mcp_tool_logger
def get_ontomop_statistics_tool() -> Any:
    """
    Get comprehensive statistics about all OntoMOP data categories.
    
    Returns:
        Dictionary with comprehensive statistics across all data types
    """
    try:
        import json
        from pathlib import Path
        from collections import defaultdict
        
        stats = {
            "labels": {"total": 0, "by_type": {}},
            "formulas": {"total": 0, "by_type": {}},
            "identifiers": {"total": 0, "by_type": {}},
            "overall": {"total_entities": 0, "unique_entity_types": 0}
        }
        
        # Analyze labels
        labels_file = Path("data/ontomop_extraction/all_labels.json")
        if labels_file.exists():
            with open(labels_file, 'r', encoding='utf-8') as f:
                labels_data = json.load(f)
            
            stats["labels"]["total"] = len(labels_data)
            type_counts = defaultdict(int)
            for item in labels_data:
                entity_type = item.get('subjectType', '').split('/')[-1] if item.get('subjectType') else 'Unknown'
                type_counts[entity_type] += 1
            stats["labels"]["by_type"] = dict(type_counts)
        
        # Analyze formulas
        formula_file = Path("data/ontomop_extraction/all_formulas.json")
        if formula_file.exists():
            with open(formula_file, 'r', encoding='utf-8') as f:
                formula_data = json.load(f)
            
            stats["formulas"]["total"] = len(formula_data)
            type_counts = defaultdict(int)
            for item in formula_data:
                entity_type = item.get('subjectType', '').split('/')[-1] if item.get('subjectType') else 'Unknown'
                type_counts[entity_type] += 1
            stats["formulas"]["by_type"] = dict(type_counts)
        
        # Analyze identifiers
        identifiers_file = Path("data/ontomop_extraction/all_identifiers.json")
        if identifiers_file.exists():
            with open(identifiers_file, 'r', encoding='utf-8') as f:
                identifiers_data = json.load(f)
            
            stats["identifiers"]["total"] = len(identifiers_data)
            type_counts = defaultdict(int)
            for item in identifiers_data:
                entity_type = item.get('subjectType', '').split('/')[-1] if item.get('subjectType') else 'Unknown'
                type_counts[entity_type] += 1
            stats["identifiers"]["by_type"] = dict(type_counts)
        
        # Calculate overall statistics
        all_types = set()
        all_types.update(stats["labels"]["by_type"].keys())
        all_types.update(stats["formulas"]["by_type"].keys())
        all_types.update(stats["identifiers"]["by_type"].keys())
        
        stats["overall"]["total_entities"] = stats["labels"]["total"] + stats["formulas"]["total"] + stats["identifiers"]["total"]
        stats["overall"]["unique_entity_types"] = len(all_types)
        stats["overall"]["entity_types"] = sorted(list(all_types))
        
        return {
            "status": "success",
            "message": "OntoMOP statistics retrieved successfully",
            **stats
        }
        
    except FileNotFoundError as e:
        return {
            "status": "error",
            "message": "Data files not found. Please ensure extraction files are available.",
            "error": str(e)
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Error getting OntoMOP statistics: {str(e)}"
        }


# -------------------- MAIN ENTRYPOINT --------------------
if __name__ == "__main__":
    mcp.run(transport="stdio")
 

