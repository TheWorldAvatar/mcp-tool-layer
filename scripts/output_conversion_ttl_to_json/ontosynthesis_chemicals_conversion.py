#!/usr/bin/env python3
"""
Chemicals TTL to JSON conversion using proper SPARQL queries and RDF libraries.

This script uses rdflib to properly parse TTL files and execute SPARQL queries
to extract chemical synthesis data and convert it to the chemicals JSON format.
"""

import json
from rdflib import Graph, Namespace, URIRef
from rdflib.plugins.sparql import prepareQuery
from typing import Dict, List, Any, Optional


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


def query_synthesis_procedures(graph: Graph, namespaces: Dict[str, Namespace]) -> List[Dict[str, str]]:
    """Query all ChemicalSynthesis entities."""
    
    ontosyn = namespaces.get('ontosyn')
    rdfs = namespaces.get('rdfs')
    
    if not ontosyn or not rdfs:
        print("Required namespaces not found!")
        return []
    
    # SPARQL query to get all ChemicalSynthesis entities
    query = """
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    PREFIX owl: <http://www.w3.org/2002/07/owl#>
    
    SELECT DISTINCT ?synthesis ?synthesisLabel
    WHERE {
        ?synthesis a ontosyn:ChemicalSynthesis .
        ?synthesis rdfs:label ?synthesisLabel .
        # Exclude Session objects
        FILTER NOT EXISTS {
            ?synthesis a owl:NamedIndividual .
            ?synthesis rdfs:label ?sessionLabel .
            FILTER(CONTAINS(?sessionLabel, "Session"))
        }
    }
    ORDER BY ?synthesisLabel
    """
    
    print("Executing SPARQL query for ChemicalSynthesis entities...")
    results = graph.query(query)
    
    syntheses = []
    for row in results:
        synthesis_uri = str(row.synthesis)
        synthesis_label = str(row.synthesisLabel)
        syntheses.append({
            'uri': synthesis_uri,
            'label': synthesis_label
        })
        print(f"Found synthesis: {synthesis_label}")
    
    print(f"Total ChemicalSynthesis entities found: {len(syntheses)}")
    return syntheses


def query_synthesis_inputs(graph: Graph, namespaces: Dict[str, Namespace], synthesis_uri: str) -> List[Dict[str, Any]]:
    """Query chemical inputs for a synthesis."""
    
    ontosyn = namespaces.get('ontosyn')
    rdfs = namespaces.get('rdfs')
    
    if not ontosyn or not rdfs:
        return []
    
    # SPARQL query to get chemical inputs
    query = """
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    
    SELECT DISTINCT ?chemical ?chemicalLabel ?amount ?formula ?purity ?supplierName
    WHERE {
        ?synthesis ontosyn:hasChemicalInput ?chemical .
        ?chemical rdfs:label ?chemicalLabel .
        OPTIONAL { ?chemical ontosyn:hasAmount ?amount }
        OPTIONAL { ?chemical ontosyn:hasChemicalFormula ?formula }
        OPTIONAL { ?chemical ontosyn:hasPurity ?purity }
        OPTIONAL { 
            ?chemical ontosyn:isSuppliedBy ?supplier .
            ?supplier rdfs:label ?supplierName .
        }
    }
    """
    
    results = graph.query(query, initBindings={'synthesis': URIRef(synthesis_uri)})
    
    inputs = []
    for row in results:
        chemical_label = str(row.chemicalLabel) if row.chemicalLabel else "Unknown"
        amount = str(row.amount) if row.amount else "N/A"
        formula = str(row.formula) if row.formula else "N/A"
        purity = str(row.purity) if row.purity else "N/A"
        supplier_name = str(row.supplierName) if row.supplierName else "N/A"
        
        inputs.append({
            'chemicalName': [chemical_label],
            'chemicalAmount': amount,
            'chemicalFormula': formula,
            'purity': purity,
            'supplierName': supplier_name
        })
    
    return inputs


def extract_yield(graph: Graph, namespaces: Dict[str, Namespace], synthesis_uri: str) -> str:
    """Extract yield information from a synthesis URI using SPARQL."""
    ontosyn = namespaces.get('ontosyn')
    rdfs = namespaces.get('rdfs')
    om2 = namespaces.get('om-2')
    
    if not ontosyn or not rdfs or not om2:
        return "N/A"
    
    # SPARQL query to get yield information
    query = """
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    PREFIX om-2: <http://www.ontology-of-units-of-measure.org/resource/om-2/>
    
    SELECT DISTINCT ?yieldValue ?yieldUnit
    WHERE {
        ?synthesis ontosyn:hasYield ?yieldUri .
        ?yieldUri om-2:hasNumericalValue ?yieldValue .
        ?yieldUri om-2:hasUnit ?yieldUnit .
    }
    """
    
    results = graph.query(query, initBindings={'synthesis': URIRef(synthesis_uri)})
    
    for row in results:
        if row.yieldValue and row.yieldUnit:
            try:
                yield_value = float(row.yieldValue)
                yield_unit = str(row.yieldUnit)
                
                # Format yield string based on value and unit
                if yield_unit == "percent":
                    yield_str = f"{yield_value}%"
                else:
                    yield_str = f"{yield_value} {yield_unit}"
                
                return yield_str
                
            except (ValueError, TypeError):
                continue
    
    return "N/A"


def query_synthesis_outputs(graph: Graph, namespaces: Dict[str, Namespace], synthesis_uri: str, ontomops_data: Dict[str, Dict[str, str]]) -> List[Dict[str, Any]]:
    """Query chemical outputs for a synthesis."""
    
    ontosyn = namespaces.get('ontosyn')
    rdfs = namespaces.get('rdfs')
    
    if not ontosyn or not rdfs:
        return []
    
    # SPARQL query to get chemical outputs from output.ttl
    query = """
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    
    SELECT DISTINCT ?output ?outputLabel
    WHERE {
        ?synthesis ontosyn:hasChemicalOutput ?output .
        ?output rdfs:label ?outputLabel .
    }
    """
    
    results = graph.query(query, initBindings={'synthesis': URIRef(synthesis_uri)})
    
    outputs = []
    for row in results:
        output_uri = str(row.output)
        output_label = str(row.outputLabel) if row.outputLabel else "Unknown"
        
        # Get CCDC number and formula from ontomops data using lowercase URI matching
        output_uri_lower = output_uri.lower()
        ontomops_info = ontomops_data.get(output_uri_lower, {})
        ccdc_number = ontomops_info.get('ccdc_number', 'N/A')
        mop_formula = ontomops_info.get('mop_formula', 'N/A')
        
        # Extract yield separately
        yield_value = extract_yield(graph, namespaces, synthesis_uri)
        
        outputs.append({
            'names': [output_label],
            'CCDCNumber': ccdc_number,
            'chemicalFormula': mop_formula,
            'yield': yield_value
        })
    
    return outputs


def query_all_ontomops_data(graph: Graph, namespaces: Dict[str, Namespace]) -> Dict[str, Dict[str, str]]:
    """Query all CCDC numbers and formulas from ontomops_extension.ttl."""
    
    ontomops = namespaces.get('ontomops')
    rdfs = namespaces.get('rdfs')
    
    if not ontomops or not rdfs:
        return {}
    
    # SPARQL query to get all CCDC numbers and formulas
    query = """
    PREFIX ontomops: <https://www.theworldavatar.com/kg/ontomops/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    
    SELECT DISTINCT ?output ?label ?ccdcNumber ?mopFormula
    WHERE {
        ?output a ontomops:MetalOrganicPolyhedron .
        ?output rdfs:label ?label .
        OPTIONAL { ?output ontomops:hasCCDCNumber ?ccdcNumber }
        OPTIONAL { ?output ontomops:hasMOPFormula ?mopFormula }
    }
    """
    
    results = graph.query(query)
    
    ontomops_data = {}
    for row in results:
        output_uri = str(row.output)
        label = str(row.label) if row.label else ""
        ccdc_number = str(row.ccdcNumber) if row.ccdcNumber else "N/A"
        mop_formula = str(row.mopFormula) if row.mopFormula else "N/A"
        
        # Use lowercase URI as key for matching
        key = output_uri.lower()
        ontomops_data[key] = {
            'ccdc_number': ccdc_number,
            'mop_formula': mop_formula,
            'label': label
        }
    
    return ontomops_data




def build_procedure_json(graph: Graph, namespaces: Dict[str, Namespace], synthesis: Dict[str, str], ontomops_data: Dict[str, Dict[str, str]]) -> Dict[str, Any]:
    """Build JSON structure for a single synthesis procedure."""
    
    procedure_name = synthesis['label']
    
    # Query inputs and outputs
    inputs = query_synthesis_inputs(graph, namespaces, synthesis['uri'])
    outputs = query_synthesis_outputs(graph, namespaces, synthesis['uri'], ontomops_data)
    
    # Group inputs by chemical (each input becomes a separate group)
    input_chemicals = []
    for input_chem in inputs:
        input_chemicals.append({
            "chemical": [input_chem],
            "purity": input_chem['purity'],
            "supplierName": input_chem['supplierName']
        })
    
    # Build step structure
    step = {
        "inputChemicals": input_chemicals,
        "outputChemical": outputs
    }
    
    return {
        "procedureName": procedure_name,
        "steps": [step]
    }


def build_json_structure(graph: Graph, namespaces: Dict[str, Namespace], syntheses: List[Dict[str, str]], ontomops_data: Dict[str, Dict[str, str]]) -> Dict[str, Any]:
    """Build complete JSON structure."""
    
    synthesis_procedures = []
    
    for synthesis in syntheses:
        print(f"\nBuilding JSON for: {synthesis['label']}")
        procedure_json = build_procedure_json(graph, namespaces, synthesis, ontomops_data)
        synthesis_procedures.append(procedure_json)
        print(f"Added procedure with {len(procedure_json['steps'])} steps")
    
    return {"synthesisProcedures": synthesis_procedures}


def main():
    """Main function to build complete chemicals JSON."""
    print("=== Building chemicals JSON ===")
    
    # Load TTL files
    ttl_files = ["output.ttl", "ontomops_extension.ttl"]
    graph = load_ttl_files(ttl_files)
    
    # Get namespaces
    namespaces = get_namespaces(graph)
    
    # Query all OntoMOPs data first
    ontomops_data = query_all_ontomops_data(graph, namespaces)
    print(f"Loaded {len(ontomops_data)} OntoMOPs entries")
    
    # Query synthesis procedures
    syntheses = query_synthesis_procedures(graph, namespaces)
    
    # Build complete JSON structure
    json_data = build_json_structure(graph, namespaces, syntheses, ontomops_data)
    
    # Save to file
    with open("converted_chemicals.json", "w", encoding="utf-8") as f:
        json.dump(json_data, f, indent=2, ensure_ascii=False)
    
    print(f"\nComplete chemicals JSON built with {len(syntheses)} synthesis procedures")
    print("Output saved to converted_chemicals.json")


if __name__ == "__main__":
    main()
