#!/usr/bin/env python3
"""
Step-by-step TTL to JSON conversion using proper SPARQL queries and RDF libraries.

This script uses rdflib to properly parse TTL files and execute SPARQL queries.
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


def query_chemical_syntheses(graph: Graph, namespaces: Dict[str, Namespace]) -> List[Dict[str, str]]:
    """Query all ChemicalSynthesis entities and get their ChemicalOutput labels."""
    
    # Get the ontosyn namespace
    ontosyn = namespaces.get('ontosyn')
    rdfs = namespaces.get('rdfs')
    
    if not ontosyn or not rdfs:
        print("Required namespaces not found!")
        return []
    
    # SPARQL query to get all ChemicalSynthesis entities with their ChemicalOutput labels
    # Exclude Session objects that are incorrectly typed as ChemicalSynthesis
    query = """
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    PREFIX owl: <http://www.w3.org/2002/07/owl#>
    
    SELECT DISTINCT ?synthesis ?synthesisLabel ?outputLabel
    WHERE {
        ?synthesis a ontosyn:ChemicalSynthesis .
        ?synthesis rdfs:label ?synthesisLabel .
        ?synthesis ontosyn:hasChemicalOutput ?output .
        ?output rdfs:label ?outputLabel .
        # Exclude Session objects
        FILTER NOT EXISTS {
            ?synthesis a owl:NamedIndividual .
            ?synthesis rdfs:label ?sessionLabel .
            FILTER(CONTAINS(?sessionLabel, "Session"))
        }
    }
    ORDER BY ?synthesisLabel
    """
    
    print("Executing SPARQL query for ChemicalSynthesis entities with ChemicalOutput labels...")
    results = graph.query(query)
    
    syntheses = []
    for row in results:
        synthesis_uri = str(row.synthesis)
        synthesis_label = str(row.synthesisLabel)
        output_label = str(row.outputLabel)
        syntheses.append({
            'uri': synthesis_uri,
            'label': synthesis_label,
            'output_label': output_label
        })
        print(f"Found synthesis: {synthesis_label} -> output: {output_label}")
    
    print(f"Total ChemicalSynthesis entities found: {len(syntheses)}")
    return syntheses


def query_synthesis_steps(graph: Graph, namespaces: Dict[str, Namespace], synthesis_uri: str) -> List[Dict[str, str]]:
    """Query synthesis steps for a particular synthesis."""
    
    ontosyn = namespaces.get('ontosyn')
    rdfs = namespaces.get('rdfs')
    
    if not ontosyn or not rdfs:
        print("Required namespaces not found!")
        return []
    
    # SPARQL query to get synthesis steps for a specific synthesis
    query = """
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    
    SELECT DISTINCT ?step ?label ?order
    WHERE {
        ?synthesis ontosyn:hasSynthesisStep ?step .
        ?step rdfs:label ?label .
        OPTIONAL { ?step ontosyn:hasOrder ?order }
    }
    ORDER BY ?order
    """
    
    print(f"Executing SPARQL query for synthesis steps...")
    results = graph.query(query, initBindings={'synthesis': URIRef(synthesis_uri)})
    
    steps = []
    for row in results:
        step_uri = str(row.step)
        label = str(row.label)
        order = int(row.order) if row.order else 0
        steps.append({
            'uri': step_uri,
            'label': label,
            'order': order
        })
        print(f"Found step: {label} (order: {order})")
    
    print(f"Total synthesis steps found: {len(steps)}")
    return steps


def extract_duration(graph: Graph, namespaces: Dict[str, Namespace], step_uri: str) -> tuple[str, float, str]:
    """Extract duration information from a step URI using SPARQL."""
    ontosyn = namespaces.get('ontosyn')
    rdfs = namespaces.get('rdfs')
    om2 = namespaces.get('om-2')
    
    if not ontosyn or not rdfs:
        return "N/A", 0.0, ""
    
    # SPARQL query to get duration information
    query = """
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    PREFIX om-2: <http://www.ontology-of-units-of-measure.org/resource/om-2/>
    
    SELECT DISTINCT ?durationValue ?durationUnit
    WHERE {
        ?step ontosyn:hasStepDuration ?durationUri .
        ?durationUri om-2:hasNumericalValue ?durationValue .
        ?durationUri om-2:hasUnit ?durationUnit .
    
    }
    """
    
    results = graph.query(query, initBindings={'step': URIRef(step_uri)})
    
    for row in results:
        if row.durationValue and row.durationUnit:
            try:
                duration_value = float(row.durationValue)
                duration_unit = str(row.durationUnit)
                
                # Format duration string based on value and unit
                if duration_unit == "minute":
                    if duration_value >= 60:
                        hours = int(duration_value // 60)
                        minutes = int(duration_value % 60)
                        if minutes > 0:
                            duration_str = f"{hours} hours {minutes} minutes"
                        else:
                            duration_str = f"{hours} hours"
                    else:
                        duration_str = f"{int(duration_value)} minutes"
                elif duration_unit == "hour":
                    duration_str = f"{int(duration_value)} hours"
                elif duration_unit == "day":
                    duration_str = f"{int(duration_value)} days"
                else:
                    duration_str = f"{duration_value} {duration_unit}"
                
                return duration_str, duration_value, duration_unit
                
            except (ValueError, TypeError):
                continue
    
    return "N/A", 0.0, ""


def extract_temperature(graph: Graph, namespaces: Dict[str, Namespace], step_uri: str, temperature_property: str) -> tuple[str, float, str]:
    """Extract temperature information from a step URI using SPARQL."""
    ontosyn = namespaces.get('ontosyn')
    rdfs = namespaces.get('rdfs')
    om2 = namespaces.get('om-2')
    
    if not ontosyn or not rdfs:
        return "N/A", 0.0, ""
    
    # SPARQL query to get temperature information
    query = """
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    PREFIX om-2: <http://www.ontology-of-units-of-measure.org/resource/om-2/>
    
    SELECT DISTINCT ?tempValue ?tempUnit
    WHERE {
        ?step ?temperatureProperty ?tempUri .
        ?tempUri om-2:hasNumericalValue ?tempValue .
        ?tempUri om-2:hasUnit ?tempUnit .
    }
    """
    
    results = graph.query(query, initBindings={'step': URIRef(step_uri), 'temperatureProperty': URIRef(temperature_property)})
    
    for row in results:
        if row.tempValue and row.tempUnit:
            try:
                temp_value = float(row.tempValue)
                temp_unit = str(row.tempUnit)
                
                # Format temperature string based on value and unit
                if temp_unit == "degreeCelsius":
                    temp_str = f"{temp_value}°C"
                elif temp_unit == "degreeFahrenheit":
                    temp_str = f"{temp_value}°F"
                elif temp_unit == "kelvin":
                    temp_str = f"{temp_value}K"
                else:
                    temp_str = f"{temp_value} {temp_unit}"
                
                return temp_str, temp_value, temp_unit
                
            except (ValueError, TypeError):
                continue
    
    return "N/A", 0.0, ""


def query_step_details(graph: Graph, namespaces: Dict[str, Namespace], step_uri: str) -> Dict[str, Any]:
    """Query detailed information for a specific synthesis step."""
    
    ontosyn = namespaces.get('ontosyn')
    rdfs = namespaces.get('rdfs')
    
    if not ontosyn or not rdfs:
        print("Required namespaces not found!")
        return {}
    
    # SPARQL query to get step details (excluding duration and temperature)
    query = """
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    
    SELECT DISTINCT ?stepType ?label ?order ?vesselName ?vesselType ?vesselEnvironment ?isStirred ?isLayered ?isSealed ?isVacuum ?isRepeated ?isVacuumFiltration ?targetPh
    WHERE {
        ?step a ?stepType .
        ?step rdfs:label ?label .
        OPTIONAL { ?step ontosyn:hasOrder ?order }
        OPTIONAL { 
            ?step ontosyn:hasVessel ?vessel .
            ?vessel rdfs:label ?vesselName .
            OPTIONAL {
                ?vessel ontosyn:hasVesselType ?vesselTypeUri .
                ?vesselTypeUri rdfs:label ?vesselType .
            }
        }
        OPTIONAL {
            ?step ontosyn:hasVesselEnvironment ?env .
            ?env rdfs:label ?vesselEnvironment .
        }
        OPTIONAL { ?step ontosyn:isStirred ?isStirred }
        OPTIONAL { ?step ontosyn:isLayered ?isLayered }
        OPTIONAL { ?step ontosyn:isSealed ?isSealed }
        OPTIONAL { ?step ontosyn:hasVacuum ?isVacuum }
        OPTIONAL { ?step ontosyn:isRepeated ?isRepeated }
        OPTIONAL { ?step ontosyn:isVacuumFiltration ?isVacuumFiltration }
        OPTIONAL { ?step ontosyn:hasTargetPh ?targetPh }
    }
    """
    
    results = graph.query(query, initBindings={'step': URIRef(step_uri)})
    
    step_details = {}
    for row in results:
        step_type = str(row.stepType).split('/')[-1] if row.stepType else "Unknown"
        label = str(row.label) if row.label else ""
        order = int(row.order) if row.order else 0
        vessel_name = str(row.vesselName) if row.vesselName else "vessel 1"
        vessel_type = str(row.vesselType) if row.vesselType else "glass vial"
        vessel_environment = str(row.vesselEnvironment) if row.vesselEnvironment else "N/A"
        is_stirred = str(row.isStirred) == "true" if row.isStirred else False
        is_layered = str(row.isLayered) == "true" if row.isLayered else False
        is_sealed = str(row.isSealed) == "true" if row.isSealed else False
        is_vacuum = str(row.isVacuum) == "true" if row.isVacuum else False
        is_repeated = int(row.isRepeated) if row.isRepeated else 1
        is_vacuum_filtration = str(row.isVacuumFiltration) == "true" if row.isVacuumFiltration else False
        target_ph = float(row.targetPh) if row.targetPh else -1
        
        # Extract duration separately
        duration, duration_value, duration_unit = extract_duration(graph, namespaces, step_uri)
        
        # Extract target temperature separately
        target_temp, target_temp_value, target_temp_unit = extract_temperature(graph, namespaces, step_uri, "https://www.theworldavatar.com/kg/OntoSyn/hasTargetTemperature")
        
        step_details = {
            'step_type': step_type,
            'label': label,
            'order': order,
            'vessel_name': vessel_name,
            'vessel_type': vessel_type,
            'vessel_environment': vessel_environment,
            'duration': duration,
            'duration_value': duration_value,
            'duration_unit': duration_unit,
            'target_temperature': target_temp,
            'target_temperature_value': target_temp_value,
            'target_temperature_unit': target_temp_unit,
            'is_stirred': is_stirred,
            'is_layered': is_layered,
            'is_sealed': is_sealed,
            'is_vacuum': is_vacuum,
            'is_repeated': is_repeated,
            'is_vacuum_filtration': is_vacuum_filtration,
            'target_ph': target_ph
        }
        break  # Take the first result
    
    return step_details


def query_step_chemicals(graph: Graph, namespaces: Dict[str, Namespace], step_uri: str) -> Dict[str, List[Dict[str, Any]]]:
    """Query chemical information for a synthesis step."""
    
    ontosyn = namespaces.get('ontosyn')
    rdfs = namespaces.get('rdfs')
    
    if not ontosyn or not rdfs:
        return {"addedChemical": [], "solvent": [], "washingSolvent": []}
    
    # Query for added chemicals - include labels and amounts
    added_chemicals_query = """
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    
    SELECT DISTINCT ?label ?amount
    WHERE {
        ?step ontosyn:hasAddedChemicalInput ?chemical .
        ?chemical rdfs:label ?label .
        OPTIONAL { ?chemical ontosyn:hasAmount ?amount }
    }
    """
    
    # Query for solvent chemicals - include labels and amounts
    solvent_chemicals_query = """
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    
    SELECT DISTINCT ?label ?amount
    WHERE {
        ?step ontosyn:hasSolventDissolve ?chemical .
        ?chemical rdfs:label ?label .
        OPTIONAL { ?chemical ontosyn:hasAmount ?amount }
    }
    """
    
    # Query for washing solvents - include labels and amounts
    washing_solvents_query = """
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    
    SELECT DISTINCT ?label ?amount
    WHERE {
        ?step ontosyn:hasWashingSolvent ?chemical .
        ?chemical rdfs:label ?label .
        OPTIONAL { ?chemical ontosyn:hasAmount ?amount }
    }
    """
    
    def process_chemicals(query: str) -> List[Dict[str, Any]]:
        chemicals = []
        results = graph.query(query, initBindings={'step': URIRef(step_uri)})
        
        for row in results:
            chemical_name = str(row.label) if row.label else ""
            chemical_amount = str(row.amount) if row.amount else "N/A"
            
            chemicals.append({
                "chemicalName": [chemical_name],
                "chemicalAmount": chemical_amount
            })
        
        return chemicals
    
    return {
        "addedChemical": process_chemicals(added_chemicals_query),
        "solvent": process_chemicals(solvent_chemicals_query),
        "washingSolvent": process_chemicals(washing_solvents_query)
    }


def build_step_json(step_details: Dict[str, Any], chemicals: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    """Build JSON structure for a single step based on its type."""
    step_type = step_details.get('step_type', 'Unknown')
    
    base_step = {
        "usedVesselName": step_details.get('vessel_name', 'vessel 1'),
        "usedVesselType": step_details.get('vessel_type', 'glass vial'),
        "stepNumber": step_details.get('order', 0),
        "atmosphere": step_details.get('vessel_environment', 'N/A'),
        "duration": step_details.get('duration', 'N/A'),
        "targetPH": int(step_details.get('target_ph', -1)) if step_details.get('target_ph', -1) != -1 else -1,
        "comment": "N/A"
    }
    
    if step_type == "Stir":
        base_step.update({
            "temperature": step_details.get('target_temperature', 'N/A'),
            "wait": False
        })
    elif step_type == "HeatChill":
        base_step.update({
            "usedDevice": "N/A",
            "targetTemperature": step_details.get('target_temperature', 'N/A'),
            "heatingCoolingRate": "N/A",
            "underVacuum": step_details.get('is_vacuum', False),
            "sealedVessel": step_details.get('is_sealed', False),
            "stir": step_details.get('is_stirred', False)
        })
    elif step_type == "Filter":
        base_step.update({
            "washingSolvent": chemicals.get('washingSolvent', []),
            "vacuumFiltration": step_details.get('is_vacuum_filtration', False),
            "numberOfFiltrations": step_details.get('is_repeated', 1)
        })
    elif step_type == "Add":
        base_step.update({
            "addedChemical": chemicals.get('addedChemical', []),
            "stir": step_details.get('is_stirred', False),
            "isLayered": step_details.get('is_layered', False)
        })
    elif step_type == "Dissolve":
        base_step.update({
            "solvent": chemicals.get('solvent', [])
        })
    elif step_type == "Transfer":
        base_step.update({
            "targetVesselName": "vessel 2",
            "targetVesselType": "Teflon-lined stainless-steel vessel",
            "isLayered": step_details.get('is_layered', False),
            "transferedAmount": "N/A"
        })
    elif step_type == "Sonicate":
        base_step.update({
            "duration": step_details.get('duration', 'N/A')
        })
    
    return {step_type: base_step}


def build_json_structure(graph: Graph, namespaces: Dict[str, Namespace], syntheses: List[Dict[str, str]]) -> Dict[str, Any]:
    """Build JSON structure based on synthesis entities with populated steps."""
    synthesis_list = []
    
    for synthesis in syntheses:
        print(f"\nBuilding JSON for: {synthesis['label']} -> {synthesis['output_label']}")
        
        # Query steps for this synthesis
        steps = query_synthesis_steps(graph, namespaces, synthesis['uri'])
        
        # Build step JSON structures
        step_json_list = []
        for step in steps:
            step_details = query_step_details(graph, namespaces, step['uri'])
            if step_details:
                # Query chemical information for this step
                chemicals = query_step_chemicals(graph, namespaces, step['uri'])
                step_json = build_step_json(step_details, chemicals)
                step_json_list.append(step_json)
        
        synthesis_entry = {
            "productNames": [synthesis['output_label']],
            "productCCDCNumber": "",
            "steps": step_json_list
        }
        synthesis_list.append(synthesis_entry)
        print(f"Added {len(step_json_list)} steps to synthesis")
    
    return {"Synthesis": synthesis_list}


def main():
    """Main function to build complete JSON with populated steps."""
    print("=== Building complete JSON with populated steps ===")
    
    # Load TTL file
    graph = load_ttl_file("output.ttl")
    
    # Get namespaces
    namespaces = get_namespaces(graph)
    
    # Query ChemicalSynthesis entities
    syntheses = query_chemical_syntheses(graph, namespaces)
    
    # Build complete JSON structure with populated steps
    json_data = build_json_structure(graph, namespaces, syntheses)
    
    # Save to file
    with open("converted_output.json", "w", encoding="utf-8") as f:
        json.dump(json_data, f, indent=4, ensure_ascii=False)
    
    print(f"\nComplete JSON structure built with {len(syntheses)} synthesis procedures")
    print("Output saved to converted_output.json")


if __name__ == "__main__":
    main()
