#!/usr/bin/env python3
"""
CBU (Chemical Building Unit) TTL to JSON conversion using proper SPARQL queries and RDF libraries.

This script extracts Chemical Building Unit data from ontomops_extension.ttl and output.ttl files
to create JSON output matching the CBU ground truth format.
"""

import json
from rdflib import Graph, Namespace, URIRef
from rdflib.plugins.sparql import prepareQuery
from typing import Dict, List, Any


def load_ttl_files(file_paths: List[str]) -> Graph:
    """Load multiple TTL files into a single RDF graph."""
    g = Graph()
    for file_path in file_paths:
        g.parse(file_path, format="turtle")
        print(f"Loaded TTL file {file_path} with {len(g)} total triples")
    return g


def get_namespaces(graph: Graph) -> Dict[str, Namespace]:
    """Extract namespaces from the graph."""
    namespaces = {}
    for prefix, namespace in graph.namespaces():
        namespaces[prefix] = namespace
        print(f"Found namespace: {prefix} -> {namespace}")
    return namespaces


def query_mop_data(graph: Graph, namespaces: Dict[str, Namespace]) -> List[Dict[str, str]]:
    """Query MOP (Metal Organic Polyhedra) data from ontomops_extension.ttl."""
    
    ontomops = namespaces.get('ontomops')
    rdfs = namespaces.get('rdfs')
    
    if not ontomops or not rdfs:
        print("Required namespaces not found!")
        return []
    
    # SPARQL query to get all MOPs with their CCDC numbers and CBUs
    query = """
    PREFIX ontomops: <https://www.theworldavatar.com/kg/ontomops/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    
    SELECT DISTINCT ?mop ?mopLabel ?ccdcNumber ?cbu
    WHERE {
        ?mop a ontomops:MetalOrganicPolyhedron .
        ?mop rdfs:label ?mopLabel .
        OPTIONAL { ?mop ontomops:hasCCDCNumber ?ccdcNumber }
        OPTIONAL { ?mop ontomops:hasChemicalBuildingUnit ?cbu }
    }
    ORDER BY ?mopLabel
    """
    
    print("Executing SPARQL query for MOP data...")
    results = graph.query(query)
    
    mops = []
    for row in results:
        mop_uri = str(row.mop)
        mop_label = str(row.mopLabel) if row.mopLabel else "Unknown"
        ccdc_number = str(row.ccdcNumber) if row.ccdcNumber else "N/A"
        cbu_uri = str(row.cbu) if row.cbu else None
        
        # Find existing MOP or create new one
        existing_mop = None
        for mop in mops:
            if mop['mop_uri'] == mop_uri:
                existing_mop = mop
                break
        
        if existing_mop:
            if cbu_uri and cbu_uri not in existing_mop['cbu_uris']:
                existing_mop['cbu_uris'].append(cbu_uri)
        else:
            mops.append({
                'mop_uri': mop_uri,
                'mop_label': mop_label,
                'ccdc_number': ccdc_number,
                'cbu_uris': [cbu_uri] if cbu_uri else []
            })
    
    print(f"Found {len(mops)} MOPs")
    return mops


def query_cbu_details(graph: Graph, namespaces: Dict[str, Namespace], cbu_uris: List[str]) -> List[Dict[str, Any]]:
    """Query Chemical Building Unit details from both ontomops_extension.ttl and output.ttl."""
    
    ontomops = namespaces.get('ontomops')
    ontosyn = namespaces.get('ontosyn')
    rdfs = namespaces.get('rdfs')
    
    if not rdfs or not cbu_uris:
        return []
    
    # Create a filter for the CBU URIs
    cbu_filter = " || ".join([f"?cbu = <{uri}>" for uri in cbu_uris if uri])
    
    if not cbu_filter:
        return []
    
    # SPARQL query to get CBU details from both ontomops and ontosyn
    query = f"""
    PREFIX ontomops: <https://www.theworldavatar.com/kg/ontomops/>
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    
    SELECT DISTINCT ?cbu ?cbuLabel ?cbuFormula ?cbuAltNames
    WHERE {{
        ?cbu rdfs:label ?cbuLabel .
        OPTIONAL {{ ?cbu ontosyn:hasChemicalFormula ?cbuFormula }}
        OPTIONAL {{ ?cbu ontosyn:hasAlternativeNames ?cbuAltNames }}
        FILTER ({cbu_filter})
    }}
    ORDER BY ?cbuLabel
    """
    
    print(f"Executing SPARQL query for {len(cbu_uris)} CBU details...")
    results = graph.query(query)
    
    cbu_details = []
    for row in results:
        cbu_uri = str(row.cbu)
        cbu_label = str(row.cbuLabel) if row.cbuLabel else "Unknown"
        cbu_formula = str(row.cbuFormula) if row.cbuFormula else "N/A"
        cbu_alt_names = str(row.cbuAltNames) if row.cbuAltNames else ""
        
        # Process alternative names
        alt_names = [cbu_label]  # Start with the main label
        if cbu_alt_names:
            # Split by comma but be careful with chemical formulas that contain commas
            # Look for patterns like "name1, name2" but not "4,4'-..." 
            if ',' in cbu_alt_names and not cbu_alt_names.strip().startswith(('4,4', '1,3', '1,4')):
                alt_names.extend([name.strip() for name in cbu_alt_names.split(',') if name.strip()])
            else:
                alt_names.append(cbu_alt_names.strip())
        
        cbu_details.append({
            'cbu_uri': cbu_uri,
            'cbu_label': cbu_label,
            'cbu_formula': cbu_formula,
            'cbu_alt_names': alt_names
        })
    
    print(f"Found {len(cbu_details)} CBU details")
    return cbu_details


def build_cbu_json_structure(mops: List[Dict[str, str]], cbu_details_map: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    """Build JSON structure for CBU data."""
    
    synthesis_procedures = []
    
    for mop in mops:
        if mop['ccdc_number'] == "N/A":
            continue  # Skip MOPs without CCDC numbers
        
        cbu_uris = mop['cbu_uris']
        if not cbu_uris:
            continue  # Skip MOPs without CBUs
        
        # Get CBU details for this MOP
        cbu_details = []
        for cbu_uri in cbu_uris:
            if cbu_uri in cbu_details_map:
                cbu_details.extend(cbu_details_map[cbu_uri])
        
        if len(cbu_details) < 2:
            continue  # Need at least 2 CBUs for the structure
        
        # Sort CBUs by label for consistent ordering
        cbu_details.sort(key=lambda x: x['cbu_label'])
        
        # Build the procedure entry
        procedure = {
            "mopCCDCNumber": mop['ccdc_number']
        }
        
        # Add CBU1 (first CBU)
        if len(cbu_details) >= 1:
            cbu1 = cbu_details[0]
            procedure["cbuFormula1"] = cbu1['cbu_formula']
            procedure["cbuSpeciesNames1"] = cbu1['cbu_alt_names']
        
        # Add CBU2 (second CBU)
        if len(cbu_details) >= 2:
            cbu2 = cbu_details[1]
            procedure["cbuFormula2"] = cbu2['cbu_formula']
            procedure["cbuSpeciesNames2"] = cbu2['cbu_alt_names']
        
        synthesis_procedures.append(procedure)
        print(f"Added procedure for MOP {mop['mop_label']} with CCDC {mop['ccdc_number']}")
    
    return {"synthesisProcedures": synthesis_procedures}


def main():
    """Main function to build complete CBU JSON."""
    print("=== Building CBU JSON ===")
    
    # Load TTL files
    ttl_files = ["ontomops_extension.ttl", "output.ttl"]
    graph = load_ttl_files(ttl_files)
    
    # Get namespaces
    namespaces = get_namespaces(graph)
    
    # Query MOP data
    mops = query_mop_data(graph, namespaces)
    
    # Collect all CBU URIs
    all_cbu_uris = []
    for mop in mops:
        all_cbu_uris.extend(mop['cbu_uris'])
    
    # Remove duplicates
    all_cbu_uris = list(set([uri for uri in all_cbu_uris if uri]))
    
    # Query CBU details
    cbu_details = query_cbu_details(graph, namespaces, all_cbu_uris)
    
    # Create a map of CBU URI to details
    cbu_details_map = {}
    for cbu_detail in cbu_details:
        cbu_uri = cbu_detail['cbu_uri']
        if cbu_uri not in cbu_details_map:
            cbu_details_map[cbu_uri] = []
        cbu_details_map[cbu_uri].append(cbu_detail)
    
    # Build complete JSON structure
    json_data = build_cbu_json_structure(mops, cbu_details_map)
    
    # Save to file
    with open("converted_cbu.json", "w", encoding="utf-8") as f:
        json.dump(json_data, f, indent=2, ensure_ascii=False)
    
    print(f"\nComplete CBU JSON built with {len(json_data['synthesisProcedures'])} synthesis procedures")
    print("Output saved to converted_cbu.json")


if __name__ == "__main__":
    main()
