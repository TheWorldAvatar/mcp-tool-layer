#!/usr/bin/env python3
"""
Characterisation TTL to JSON conversion using proper SPARQL queries and RDF libraries.

This script uses rdflib to properly parse TTL files and execute SPARQL queries
to extract characterisation data from OntoSpecies extension files.
"""

import json
from rdflib import Graph, Namespace, URIRef
from rdflib.plugins.sparql import prepareQuery
from typing import Dict, List, Any, Optional


def load_ttl_file(file_path: str) -> Graph:
    """Load TTL file into an RDF graph."""
    g = Graph()
    g.parse(file_path, format="turtle")
    print(f"Loaded TTL file with {len(g)} triples")
    return g


def get_namespaces(graph: Graph) -> Dict[str, Namespace]:
    """Extract namespaces from the graph."""
    namespaces = {}
    for prefix, namespace in graph.namespaces():
        namespaces[prefix] = namespace
        print(f"Found namespace: {prefix} -> {namespace}")
    return namespaces


def query_characterisation_devices(graph: Graph, namespaces: Dict[str, Namespace]) -> Dict[str, Any]:
    """Query characterisation devices from the graph."""
    
    ontospecies = namespaces.get('ontospecies')
    rdfs = namespaces.get('rdfs')
    
    if not ontospecies or not rdfs:
        print("Required namespaces not found!")
        return {}
    
    # SPARQL query to get all characterisation devices
    query = """
    PREFIX ontospecies: <http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    
    SELECT DISTINCT ?device ?deviceType ?deviceName ?frequency
    WHERE {
        ?device a ?deviceType .
        ?device rdfs:label ?deviceName .
        OPTIONAL { ?device ontospecies:hasFrequency ?frequency }
        FILTER (?deviceType = ontospecies:ElementalAnalysisDevice || 
                ?deviceType = ontospecies:HNMRDevice || 
                ?deviceType = ontospecies:InfraredSpectroscopyDevice)
    }
    """
    
    print("Executing SPARQL query for characterisation devices...")
    results = graph.query(query)
    
    devices = {
        "ElementalAnalysisDevice": {},
        "HNMRDevice": {},
        "InfraredSpectroscopyDevice": {}
    }
    
    for row in results:
        device_type = str(row.deviceType).split('#')[-1]
        device_name = str(row.deviceName) if row.deviceName else "N/A"
        frequency = str(row.frequency) if row.frequency else None
        
        if device_type == "ElementalAnalysisDevice":
            devices["ElementalAnalysisDevice"] = {
                "deviceName": device_name
            }
        elif device_type == "HNMRDevice":
            device_info = {"deviceName": device_name}
            if frequency:
                device_info["frequency"] = frequency
            devices["HNMRDevice"] = device_info
        elif device_type == "InfraredSpectroscopyDevice":
            devices["InfraredSpectroscopyDevice"] = {
                "deviceName": device_name
            }
    
    print(f"Found devices: {devices}")
    return devices


def query_characterisation_data(graph: Graph, namespaces: Dict[str, Namespace]) -> List[Dict[str, Any]]:
    """Query characterisation data for all species."""
    
    ontospecies = namespaces.get('ontospecies')
    rdfs = namespaces.get('rdfs')
    dc = namespaces.get('dc')
    
    if not ontospecies or not rdfs:
        print("Required namespaces not found!")
        return []
    
    # SPARQL query to get all species with their characterisation data
    query = """
    PREFIX ontospecies: <http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    PREFIX dc: <http://purl.org/dc/elements/1.1/>
    
    SELECT DISTINCT ?species ?speciesLabel ?ccdcNumber ?ccdcId ?molecularFormula ?irData ?irBands ?irMaterial ?nmrData
    WHERE {
        ?species a ontospecies:Species .
        ?species rdfs:label ?speciesLabel .
        OPTIONAL { 
            ?species ontospecies:hasCCDCNumber ?ccdcUri .
            ?ccdcUri rdfs:label ?ccdcNumber .
            ?ccdcUri dc:identifier ?ccdcId .
        }
        OPTIONAL { 
            ?species ontospecies:hasMolecularFormula ?formulaUri .
            ?formulaUri rdfs:label ?molecularFormula .
        }
        OPTIONAL { 
            ?species ontospecies:hasInfraredSpectroscopyData ?irUri .
            ?irUri rdfs:label ?irData .
            ?irUri dc:description ?irBands .
            OPTIONAL { 
                ?irUri ontospecies:hasMaterial ?materialUri .
                ?materialUri rdfs:label ?irMaterial .
            }
        }
        OPTIONAL { 
            ?species ontospecies:hasHNMRData ?nmrUri .
            ?nmrUri rdfs:label ?nmrData .
        }
    }
    ORDER BY ?speciesLabel
    """
    
    print("Executing SPARQL query for characterisation data...")
    results = graph.query(query)
    
    characterisations = []
    for row in results:
        species_label = str(row.speciesLabel) if row.speciesLabel else "Unknown"
        ccdc_number = str(row.ccdcNumber) if row.ccdcNumber else "N/A"
        ccdc_id = str(row.ccdcId) if row.ccdcId else "N/A"
        molecular_formula = str(row.molecularFormula) if row.molecularFormula else "N/A"
        ir_data = str(row.irData) if row.irData else "N/A"
        ir_bands = str(row.irBands) if row.irBands else "N/A"
        ir_material = str(row.irMaterial) if row.irMaterial else "N/A"
        nmr_data = str(row.nmrData) if row.nmrData else "N/A"
        
        # Build characterisation entry
        char_entry = {
            "ElementalAnalysis": {
                "chemicalFormula": molecular_formula,
                "weightPercentageCalculated": "N/A",
                "weightPercentageExperimental": "N/A"
            },
            "HNMR": {
                "shifts": "N/A",
                "solvent": "N/A",
                "temperature": "N/A"
            },
            "InfraredSpectroscopy": {
                "bands": ir_bands,
                "material": ir_material
            },
            "productCCDCNumber": ccdc_id,
            "productNames": [species_label]
        }
        
        characterisations.append(char_entry)
        print(f"Found characterisation for: {species_label} (CCDC: {ccdc_id})")
    
    print(f"Total characterisation entries found: {len(characterisations)}")
    return characterisations


def build_json_structure(devices: Dict[str, Any], characterisations: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build JSON structure based on devices and characterisation data."""
    
    if not characterisations:
        return {"Devices": []}
    
    # Group characterisations under a single device entry
    device_entry = {
        "Characterisation": characterisations
    }
    
    # Add device information if available
    if devices.get("ElementalAnalysisDevice"):
        device_entry["ElementalAnalysisDevice"] = devices["ElementalAnalysisDevice"]
    if devices.get("HNMRDevice"):
        device_entry["HNMRDevice"] = devices["HNMRDevice"]
    if devices.get("InfraredSpectroscopyDevice"):
        device_entry["InfraredSpectroscopyDevice"] = devices["InfraredSpectroscopyDevice"]
    
    return {"Devices": [device_entry]}


def main():
    """Main function to build complete characterisation JSON."""
    print("=== Building characterisation JSON ===")
    
    # Load TTL file
    graph = load_ttl_file("ontospecies_extension.ttl")
    
    # Get namespaces
    namespaces = get_namespaces(graph)
    
    # Query characterisation devices
    devices = query_characterisation_devices(graph, namespaces)
    
    # Query characterisation data
    characterisations = query_characterisation_data(graph, namespaces)
    
    # Build complete JSON structure
    json_data = build_json_structure(devices, characterisations)
    
    # Save to file
    with open("converted_characterisation.json", "w", encoding="utf-8") as f:
        json.dump(json_data, f, indent=2, ensure_ascii=False)
    
    print(f"\nComplete characterisation JSON built with {len(characterisations)} characterisation entries")
    print("Output saved to converted_characterisation.json")


if __name__ == "__main__":
    main()
