#!/usr/bin/env python3
"""
Step TTL to JSON conversion using proper SPARQL queries and RDF libraries.

This script uses rdflib to properly parse TTL files and execute SPARQL queries
to extract synthesis step data and convert it to the steps JSON format.
"""

import json
from rdflib import Graph, Namespace, URIRef
from rdflib.plugins.sparql import prepareQuery
from typing import Dict, List, Any, Optional
from scripts.output_conversion_ttl_to_json.step.chemicalinput_query import query_synthesis_inputs
from scripts.output_conversion_ttl_to_json.step.ccdc_query import query_ccdc_numbers
from scripts.output_conversion_ttl_to_json.step.step_query import query_synthesis_steps
from scripts.output_conversion_ttl_to_json.step.step_details import query_step_details_all_fields


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
    """Query all ChemicalSynthesis entities with only their labels. No outputs/CCDC."""
    ontosyn = namespaces.get('ontosyn')
    rdfs = namespaces.get('rdfs')
    if not ontosyn or not rdfs:
        print("Required namespaces not found!")
        return []

    query = """
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

    SELECT DISTINCT ?synthesis ?synthesisLabel
    WHERE {
        ?synthesis a ontosyn:ChemicalSynthesis .
        ?synthesis rdfs:label ?synthesisLabel .
    }
    ORDER BY ?synthesisLabel
    """

    print("Executing SPARQL query for ChemicalSynthesis entities (labels only)...")
    results = graph.query(query)
    syntheses: List[Dict[str, str]] = []
    for row in results:
        syntheses.append({
            'uri': str(row.synthesis),
            'label': str(row.synthesisLabel)
        })
    print(f"Total ChemicalSynthesis entities found: {len(syntheses)}")
    return syntheses


def query_outputs(graph: Graph, synthesis_uri: str) -> Dict[str, List[str]]:
    """Collect product labels and CCDC numbers for a synthesis via multiple paths.

    This is the PRIMARY method for CCDC retrieval and includes all major paths:
    
    Supported paths for CCDC numbers:
    1. OntoSpecies: ChemicalSynthesis -> ontosyn:hasChemicalOutput -> ontospecies:Species -> ontospecies:hasCCDCNumber/ontospecies:hasCCDCNumberValue
    2. OntoSyn: ChemicalSynthesis -> ontosyn:hasChemicalOutput -> ontosyn:ChemicalOutput (use rdfs:label)
    3. OntoMOPs (FALLBACK): ChemicalSynthesis -> ontosyn:hasChemicalOutput -> ontomops:MetalOrganicPolyhedron -> ontomops:hasCCDCNumber (literal)
    
    Returns dict with 'labels' and 'ccdc' keys, each containing a list of found values.
    """
    query = """
    PREFIX rdfs:       <http://www.w3.org/2000/01/rdf-schema#>
    PREFIX ontosyn:    <https://www.theworldavatar.com/kg/OntoSyn/>
    PREFIX ontospecies:<http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#>
    PREFIX ontomops:   <https://www.theworldavatar.com/kg/ontomops/>

    SELECT DISTINCT ?productLabel ?ccdcVal
    WHERE {
      { # OntoSpecies Species as output
        ?syn ontosyn:hasChemicalOutput ?sp .
        ?sp a ontospecies:Species .
        OPTIONAL { ?sp rdfs:label ?productLabel }
        OPTIONAL { ?sp ontospecies:hasCCDCNumber/ontospecies:hasCCDCNumberValue ?ccdcVal }
      }
      UNION
      { # OntoSyn ChemicalOutput as output
        ?syn ontosyn:hasChemicalOutput ?co .
        ?co a ontosyn:ChemicalOutput .
        OPTIONAL { ?co rdfs:label ?productLabel }
      }
      UNION
      { # OntoMOPs MetalOrganicPolyhedron as output
        ?syn ontosyn:hasChemicalOutput ?mop .
        ?mop a ontomops:MetalOrganicPolyhedron .
        OPTIONAL { ?mop rdfs:label ?productLabel }
        OPTIONAL { ?mop ontomops:hasCCDCNumber ?ccdcVal }
      }
    }
    """
    labels: List[str] = []
    ccdc_vals: List[str] = []
    try:
        res = graph.query(query, initBindings={'syn': URIRef(synthesis_uri)})
        for row in res:
            if getattr(row, 'productLabel', None):
                lbl = str(row.productLabel).strip()
                if lbl and lbl not in labels:
                    labels.append(lbl)
            if getattr(row, 'ccdcVal', None):
                cv = str(row.ccdcVal).strip()
                if cv and cv not in ccdc_vals:
                    ccdc_vals.append(cv)
    except Exception:
        pass
    return {"labels": labels, "ccdc": ccdc_vals}


def query_syntheses_via_steps(graph: Graph, namespaces: Dict[str, Namespace]) -> List[Dict[str, str]]:
    """Fallback: find any subject that has ontosyn:hasSynthesisStep and treat it as a synthesis.
    Label is optional.
    """
    ontosyn = namespaces.get('ontosyn')
    rdfs = namespaces.get('rdfs')
    if not rdfs:
        return []

    query = """
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    
    SELECT DISTINCT ?synthesis ?synthesisLabel
    WHERE {
        ?synthesis ontosyn:hasSynthesisStep ?step .
        OPTIONAL { ?synthesis rdfs:label ?synthesisLabel }
    }
    ORDER BY ?synthesisLabel
    """

    print("Executing fallback SPARQL for syntheses via hasSynthesisStep...")
    results = graph.query(query)

    syntheses = []
    for row in results:
        synthesis_uri = str(row.synthesis)
        synthesis_label = str(row.synthesisLabel) if row.synthesisLabel else synthesis_uri
        syntheses.append({
            'uri': synthesis_uri,
            'label': synthesis_label,
            'output_label': "",
            'ccdc_number': ""
        })
        # Use ASCII-safe printing to avoid Unicode encoding errors
        try:
            print(f"Found synthesis via step link: {synthesis_label}")
        except UnicodeEncodeError:
            print(f"Found synthesis via step link: {synthesis_label.encode('ascii', 'replace').decode('ascii')}")

    print(f"Total syntheses found via steps: {len(syntheses)}")
    return syntheses


## Local query_synthesis_inputs removed; using implementation from step/chemicalinput_query.py


def extract_duration(graph: Graph, namespaces: Dict[str, Namespace], step_uri: str) -> str:
    """Extract duration information from a step URI using SPARQL and return clean format."""
    ontosyn = namespaces.get('ontosyn')
    rdfs = namespaces.get('rdfs')
    om2 = namespaces.get('om-2')
    
    if not ontosyn or not rdfs:
        return "N/A"
    
    # SPARQL query to get duration information
    query = """
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    PREFIX om-2: <http://www.ontology-of-units-of-measure.org/resource/om-2/>
    
    SELECT DISTINCT ?durationValue ?durationUnit ?durationLabel
    WHERE {
        ?step ontosyn:hasStepDuration ?durationUri .
        ?durationUri om-2:hasNumericalValue ?durationValue .
        ?durationUri om-2:hasUnit ?durationUnit .
        OPTIONAL { ?durationUri rdfs:label ?durationLabel }
    }
    """
    
    results = graph.query(query, initBindings={'step': URIRef(step_uri)})
    
    for row in results:
        if row.durationValue and row.durationUnit:
            try:
                duration_value = float(row.durationValue)
                duration_unit = str(row.durationUnit)
                duration_label = str(row.durationLabel) if row.durationLabel else ""
                
                # Use label if available and clean
                if duration_label and duration_label != "N/A" and not duration_label.startswith("http"):
                    return duration_label
                
                # Return duration with value and unit as-is
                return f"{duration_value} {duration_unit}"
                
            except (ValueError, TypeError):
                continue
    
    return "N/A"


def extract_temperature(graph: Graph, namespaces: Dict[str, Namespace], step_uri: str, temperature_property: str) -> str:
    """Extract temperature information from a step URI using SPARQL and return clean format."""
    ontosyn = namespaces.get('ontosyn')
    rdfs = namespaces.get('rdfs')
    om2 = namespaces.get('om-2')
    
    if not ontosyn or not rdfs:
        return "N/A"
    
    # SPARQL query to get temperature information
    query = """
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    PREFIX om-2: <http://www.ontology-of-units-of-measure.org/resource/om-2/>
    
    SELECT DISTINCT ?tempValue ?tempUnit ?tempLabel
    WHERE {
        ?step ?temperatureProperty ?tempUri .
        ?tempUri om-2:hasNumericalValue ?tempValue .
        ?tempUri om-2:hasUnit ?tempUnit .
        OPTIONAL { ?tempUri rdfs:label ?tempLabel }
    }
    """
    
    results = graph.query(query, initBindings={'step': URIRef(step_uri), 'temperatureProperty': URIRef(temperature_property)})
    
    for row in results:
        if row.tempValue and row.tempUnit:
            try:
                temp_value = float(row.tempValue)
                temp_unit = str(row.tempUnit)
                temp_label = str(row.tempLabel) if row.tempLabel else ""
                
                # Use label if present and not a URL
                if temp_label and temp_label != "N/A" and not temp_label.startswith("http"):
                    return temp_label
                
                # Return temperature with value and unit as-is
                return f"{temp_value} {temp_unit}"
                
            except (ValueError, TypeError):
                continue
    
    return "N/A"


def extract_temperature_rate(graph: Graph, namespaces: Dict[str, Namespace], step_uri: str) -> str:
    """Extract heating/cooling rate via ontosyn:hasTemperatureRate.
    Format as e.g. "10 °C per hour" for om-2:kelvinPerHour.
    """
    ontosyn = namespaces.get('ontosyn')
    if not ontosyn:
        return "N/A"

    query = """
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    PREFIX om-2: <http://www.ontology-of-units-of-measure.org/resource/om-2/>
    SELECT DISTINCT ?val ?unit ?label WHERE {
      ?step ontosyn:hasTemperatureRate ?rate .
      ?rate om-2:hasNumericalValue ?val .
      OPTIONAL { ?rate om-2:hasUnit ?unit }
      OPTIONAL { ?rate rdfs:label ?label }
    } LIMIT 1
    """

    try:
        results = graph.query(query, initBindings={'step': URIRef(step_uri)})
    except Exception:
        results = []

    for row in results:
        try:
            v = float(row.val) if getattr(row, 'val', None) is not None else None
        except Exception:
            v = None
        unit_iri = str(row.unit) if getattr(row, 'unit', None) else ""
        label = str(row.label) if getattr(row, 'label', None) else ""

        if label and not label.startswith("http"):
            return label

        if v is None:
            continue

        # Return rate with value and unit as-is
        if unit_iri:
            return f"{v} {unit_iri}"
        return str(v)

    return "N/A"


def extract_transferred_amount(graph: Graph, namespaces: Dict[str, Namespace], step_uri: str) -> str:
    """Extract transferred amount via ontosyn:hasTransferedAmount.
    Returns formatted string like "2.4 milliliter" or "N/A" if not found.
    """
    ontosyn = namespaces.get('ontosyn')
    if not ontosyn:
        return "N/A"
    
    query = """
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    PREFIX om-2: <http://www.ontology-of-units-of-measure.org/resource/om-2/>
    
    SELECT DISTINCT ?val ?unit ?label WHERE {
      ?step ontosyn:hasTransferedAmount ?amount .
      OPTIONAL { ?amount om-2:hasNumericalValue ?val }
      OPTIONAL { ?amount om-2:hasUnit ?unit }
      OPTIONAL { ?amount rdfs:label ?label }
    } LIMIT 1
    """
    
    try:
        results = graph.query(query, initBindings={'step': URIRef(step_uri)})
    except Exception:
        results = []
    
    for row in results:
        label = str(row.label) if getattr(row, 'label', None) else ""
        
        # Prefer label if available and clean
        if label and not label.startswith("http"):
            return label
        
        # Otherwise construct from value and unit
        try:
            v = float(row.val) if getattr(row, 'val', None) is not None else None
        except Exception:
            v = None
        
        unit_iri = str(row.unit) if getattr(row, 'unit', None) else ""
        
        if v is not None:
            if unit_iri:
                return f"{v} {unit_iri}"
            return str(v)
    
    return "N/A"


def extract_target_vessel(graph: Graph, namespaces: Dict[str, Namespace], step_uri: str) -> Dict[str, str]:
    """Extract target vessel information for Transfer steps via ontosyn:isTransferedTo.
    Returns dict with 'name' and 'type' keys.
    """
    ontosyn = namespaces.get('ontosyn')
    if not ontosyn:
        return {"name": "N/A", "type": "N/A"}
    
    # Query for target vessel name
    query_name = """
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    
    SELECT DISTINCT ?vesselName WHERE {
      ?step ontosyn:isTransferedTo ?vessel .
      OPTIONAL { ?vessel rdfs:label ?vesselName }
    } LIMIT 1
    """
    
    # Query for target vessel type
    query_type = """
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    
    SELECT DISTINCT ?vesselTypeLabel WHERE {
      ?step ontosyn:isTransferedTo ?vessel .
      ?vessel ontosyn:hasVesselType ?vesselType .
      ?vesselType rdfs:label ?vesselTypeLabel .
    } LIMIT 1
    """
    
    vessel_name = "N/A"
    vessel_type = "N/A"
    
    try:
        results_name = list(graph.query(query_name, initBindings={'step': URIRef(step_uri)}))
        if results_name and getattr(results_name[0], 'vesselName', None):
            vessel_name = str(results_name[0].vesselName)
    except Exception:
        pass
    
    try:
        results_type = list(graph.query(query_type, initBindings={'step': URIRef(step_uri)}))
        if results_type and getattr(results_type[0], 'vesselTypeLabel', None):
            vessel_type = str(results_type[0].vesselTypeLabel)
    except Exception:
        pass
    
    return {"name": vessel_name, "type": vessel_type}


def query_step_details(graph: Graph, namespaces: Dict[str, Namespace], step_uri: str) -> Dict[str, Any]:
    """Query detailed information for a specific synthesis step.
    Prefer the most specific step type (e.g., Crystallize) over the generic SynthesisStep.
    """
    
    ontosyn = namespaces.get('ontosyn')
    rdfs = namespaces.get('rdfs')
    
    if not ontosyn or not rdfs:
        print("Required namespaces not found!")
        return {}
    
    # SPARQL query to get step details (excluding duration and temperature)
    query = """
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    
    SELECT DISTINCT ?stepType ?label ?order ?comment ?vesselName ?vesselEnvironment ?isStirred ?isLayered ?isSealed ?isVacuum ?isRepeated ?isVacuumFiltration ?targetPh ?deviceLabel ?isLayeredTransfer ?isWait
    WHERE {
        ?step a ?stepType .
        ?step rdfs:label ?label .
        OPTIONAL { ?step rdfs:comment ?comment }
        OPTIONAL { ?step ontosyn:hasOrder ?order }
        OPTIONAL { 
            ?step ontosyn:hasVessel ?vessel .
            OPTIONAL { ?vessel rdfs:label ?vesselName }
        }
        OPTIONAL {
            ?step ontosyn:hasVesselEnvironment ?env .
            OPTIONAL { ?env rdfs:label ?vesselEnvironment }
        }
        OPTIONAL {
            ?step ontosyn:hasHeatChillDevice ?dev .
            OPTIONAL { ?dev rdfs:label ?deviceLabel }
        }
        OPTIONAL { ?step ontosyn:isStirred ?isStirred }
        OPTIONAL { ?step ontosyn:isLayered ?isLayered }
        OPTIONAL { ?step ontosyn:isSealed ?isSealed }
        OPTIONAL { ?step ontosyn:hasVacuum ?isVacuum }
        OPTIONAL { ?step ontosyn:isRepeated ?isRepeated }
        OPTIONAL { ?step ontosyn:isVacuumFiltration ?isVacuumFiltration }
        OPTIONAL { ?step ontosyn:hasTargetPh ?targetPh }
        OPTIONAL { ?step ontosyn:isLayeredTransfer ?isLayeredTransfer }
        OPTIONAL { ?step ontosyn:isWait ?isWait }
    }
    """
    
    res = list(graph.query(query, initBindings={'step': URIRef(step_uri)}))
    
    # Choose the most specific type (not SynthesisStep) if present
    chosen_row = None
    chosen_type = "Unknown"
    fallback_row = None
    fallback_type = "Unknown"
    for row in res:
        local = str(row.stepType).split('/')[-1] if row.stepType else "Unknown"
        if local != "SynthesisStep" and chosen_row is None:
            chosen_row = row
            chosen_type = local
        if local == "SynthesisStep" and fallback_row is None:
            fallback_row = row
            fallback_type = local
    row = chosen_row or fallback_row
    step_type = chosen_type if chosen_row is not None else fallback_type
    
    if row is None:
        # No data
        return {}
    
    label = str(row.label) if row.label else ""
    order = int(row.order) if row.order else 0
    comment = str(row.comment) if row.comment else "N/A"
    vessel_name = str(row.vesselName) if row.vesselName else "N/A"
    
    # Separate query for vessel type to ensure we follow the correct path:
    # SynthesisStep -> hasVessel -> Vessel -> hasVesselType -> VesselType -> label
    vessel_type_query = """
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    
    SELECT DISTINCT ?vesselTypeLabel
    WHERE {
        ?step ontosyn:hasVessel ?vessel .
        ?vessel ontosyn:hasVesselType ?vesselType .
        ?vesselType rdfs:label ?vesselTypeLabel .
    }
    """
    vessel_type_result = list(graph.query(vessel_type_query, initBindings={'step': URIRef(step_uri)}))
    vessel_type = str(vessel_type_result[0].vesselTypeLabel) if vessel_type_result and vessel_type_result[0].vesselTypeLabel else "N/A"
    
    vessel_environment = str(row.vesselEnvironment) if row.vesselEnvironment else "N/A"
    device_label = str(row.deviceLabel) if row.deviceLabel else "N/A"
    is_stirred = (str(row.isStirred) == "true") if row.isStirred else None
    is_layered = (str(row.isLayered) == "true") if row.isLayered else None
    is_sealed = (str(row.isSealed) == "true") if row.isSealed else None
    is_vacuum = (str(row.isVacuum) == "true") if row.isVacuum else None
    is_repeated = int(row.isRepeated) if row.isRepeated else None
    is_vacuum_filtration = (str(row.isVacuumFiltration) == "true") if row.isVacuumFiltration else None
    target_ph = float(row.targetPh) if row.targetPh else None
    is_layered_transfer = (str(row.isLayeredTransfer) == "true") if row.isLayeredTransfer else None
    is_wait = (str(row.isWait) == "true") if row.isWait else None
    
    # Extract duration and temperature separately
    duration = extract_duration(graph, namespaces, step_uri)
    target_temp = extract_temperature(graph, namespaces, step_uri, "https://www.theworldavatar.com/kg/OntoSyn/hasTargetTemperature")
    cryst_temp = extract_temperature(graph, namespaces, step_uri, "https://www.theworldavatar.com/kg/OntoSyn/hasCrystallizationTargetTemperature")
    temp_rate = extract_temperature_rate(graph, namespaces, step_uri)
    
    # Extract Transfer-specific fields
    transferred_amount = extract_transferred_amount(graph, namespaces, step_uri)
    target_vessel_info = extract_target_vessel(graph, namespaces, step_uri)
    
    step_details = {
        'step_type': step_type,
        'label': label,
        'order': order,
        'comment': comment,
        'vessel_name': vessel_name,
        'vessel_type': vessel_type,
        'vessel_environment': vessel_environment,
        'device_label': device_label,
        'duration': duration,
        'target_temperature': target_temp,
        'heating_cooling_rate': temp_rate,
        'crystallization_temperature': cryst_temp,
        'is_stirred': is_stirred,
        'is_layered': is_layered,
        'is_sealed': is_sealed,
        'is_vacuum': is_vacuum,
        'is_repeated': is_repeated,
        'is_vacuum_filtration': is_vacuum_filtration,
        'target_ph': target_ph,
        'is_layered_transfer': is_layered_transfer,
        'is_wait': is_wait,
        'transferred_amount': transferred_amount,
        'target_vessel_name': target_vessel_info['name'],
        'target_vessel_type': target_vessel_info['type']
    }
    return step_details


def query_step_chemicals(graph: Graph, namespaces: Dict[str, Namespace], step_uri: str) -> Dict[str, List[Dict[str, Any]]]:
    """Query chemical information for a synthesis step."""
    
    ontosyn = namespaces.get('ontosyn')
    rdfs = namespaces.get('rdfs')
    
    if not ontosyn or not rdfs:
        return {"addedChemical": [], "solvent": [], "washingSolvent": []}
    
    # Queries for chemicals: include label, alternative names and chemical formula values
    def _chem_query_for(prop_iri: str) -> str:
        return f"""
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    PREFIX os: <http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#>
    
    SELECT DISTINCT ?chemical ?label ?amount ?alt ?cfLabel ?cfVal ?cfValSyn
    WHERE {{
        ?step {prop_iri} ?chemical .
        OPTIONAL {{ ?chemical rdfs:label ?label }}
        OPTIONAL {{ ?chemical ontosyn:hasAmount ?amount }}
        OPTIONAL {{ ?chemical ontosyn:hasAlternativeNames ?alt }}
        OPTIONAL {{
          ?chemical ontosyn:hasChemicalFormula ?cf .
          OPTIONAL {{ ?cf rdfs:label ?cfLabel }}
          OPTIONAL {{ ?cf os:hasChemicalFormulaValue ?cfVal }}
          OPTIONAL {{ ?cf ontosyn:hasChemicalFormulaValue ?cfValSyn }}
        }}
    }}
    """

    added_chemicals_query = _chem_query_for("ontosyn:hasAddedChemicalInput")
    solvent_chemicals_query = _chem_query_for("ontosyn:hasSolventDissolve")
    
    def process_chemicals(query: str) -> List[Dict[str, Any]]:
        results = graph.query(query, initBindings={'step': URIRef(step_uri)})
        # Group by chemical node
        grouped: Dict[str, Dict[str, Any]] = {}
        for row in results:
            chem_uri = str(row.chemical) if getattr(row, 'chemical', None) else ""
            rec = grouped.setdefault(chem_uri or f"_local_{len(grouped)}", {"names": [], "amount": None})
            # Append label
            if row.label:
                lbl = str(row.label).strip()
                if lbl and lbl not in rec["names"]:
                    rec["names"].append(lbl)
            # Append alternative name
            if row.alt:
                alt = str(row.alt).strip()
                if alt and alt not in rec["names"]:
                    rec["names"].append(alt)
            # Append chemical formula values/labels
            for attr in ("cfLabel", "cfVal", "cfValSyn"):
                val = getattr(row, attr, None)
                if val:
                    s = str(val).strip()
                    if s and s not in rec["names"]:
                        rec["names"].append(s)
            # Amount (keep first non-empty)
            if (row.amount is not None) and (rec["amount"] in (None, "", "N/A")):
                amt = str(row.amount).strip()
                rec["amount"] = amt if amt else rec["amount"]

        # Build output list
        chemicals: List[Dict[str, Any]] = []
        for rec in grouped.values():
            names = rec["names"] if rec["names"] else ["N/A"]
            amt = rec["amount"] if rec["amount"] else "N/A"
            chemicals.append({
                "chemicalName": names,
                "chemicalAmount": amt,
            })
        return chemicals
    
    def process_washing_solvents() -> List[Dict[str, Any]]:
        """Separate function to query washing solvents using both possible property names."""
        # Try ontosyn:hasWashingSolvent first
        query1 = """
        PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
        PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
        PREFIX os: <http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#>
        
        SELECT DISTINCT ?chemical ?label ?amount ?alt ?cfLabel ?cfVal ?cfValSyn
        WHERE {
            ?step ontosyn:hasWashingSolvent ?chemical .
            OPTIONAL { ?chemical rdfs:label ?label }
            OPTIONAL { ?chemical ontosyn:hasAmount ?amount }
            OPTIONAL { ?chemical ontosyn:hasAlternativeNames ?alt }
            OPTIONAL {
              ?chemical ontosyn:hasChemicalFormula ?cf .
              OPTIONAL { ?cf rdfs:label ?cfLabel }
              OPTIONAL { ?cf os:hasChemicalFormulaValue ?cfVal }
              OPTIONAL { ?cf ontosyn:hasChemicalFormulaValue ?cfValSyn }
            }
        }
        """
        
        # Try ontosyn:hasWashingChemical as fallback
        query2 = """
        PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
        PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
        PREFIX os: <http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#>
        
        SELECT DISTINCT ?chemical ?label ?amount ?alt ?cfLabel ?cfVal ?cfValSyn
        WHERE {
            ?step ontosyn:hasWashingChemical ?chemical .
            OPTIONAL { ?chemical rdfs:label ?label }
            OPTIONAL { ?chemical ontosyn:hasAmount ?amount }
            OPTIONAL { ?chemical ontosyn:hasAlternativeNames ?alt }
            OPTIONAL {
              ?chemical ontosyn:hasChemicalFormula ?cf .
              OPTIONAL { ?cf rdfs:label ?cfLabel }
              OPTIONAL { ?cf os:hasChemicalFormulaValue ?cfVal }
              OPTIONAL { ?cf ontosyn:hasChemicalFormulaValue ?cfValSyn }
            }
        }
        """
        
        # Try first query
        try:
            results1 = list(graph.query(query1, initBindings={'step': URIRef(step_uri)}))
        except Exception:
            results1 = []
        
        # If no results, try second query
        if not results1:
            try:
                results2 = list(graph.query(query2, initBindings={'step': URIRef(step_uri)}))
            except Exception:
                results2 = []
            results = results2
        else:
            results = results1
        
        # Group by chemical node
        grouped: Dict[str, Dict[str, Any]] = {}
        for row in results:
            chem_uri = str(row.chemical) if getattr(row, 'chemical', None) else ""
            rec = grouped.setdefault(chem_uri or f"_local_{len(grouped)}", {"names": [], "amount": None})
            # Append label
            if getattr(row, 'label', None):
                lbl = str(row.label).strip()
                if lbl and lbl not in rec["names"]:
                    rec["names"].append(lbl)
            # Append alternative name
            if getattr(row, 'alt', None):
                alt = str(row.alt).strip()
                if alt and alt not in rec["names"]:
                    rec["names"].append(alt)
            # Append chemical formula values/labels
            for attr in ("cfLabel", "cfVal", "cfValSyn"):
                val = getattr(row, attr, None)
                if val:
                    s = str(val).strip()
                    if s and s not in rec["names"]:
                        rec["names"].append(s)
            # Amount (keep first non-empty)
            if getattr(row, 'amount', None) is not None and (rec["amount"] in (None, "", "N/A")):
                amt = str(row.amount).strip()
                rec["amount"] = amt if amt else rec["amount"]
        
        # Build output list
        chemicals: List[Dict[str, Any]] = []
        for rec in grouped.values():
            names = rec["names"] if rec["names"] else ["N/A"]
            amt = rec["amount"] if rec["amount"] else "N/A"
            chemicals.append({
                "chemicalName": names,
                "chemicalAmount": amt,
            })
        return chemicals
    
    return {
        "addedChemical": process_chemicals(added_chemicals_query),
        "solvent": process_chemicals(solvent_chemicals_query),
        "washingSolvent": process_washing_solvents()
    }


def build_step_json(step_details: Dict[str, Any], chemicals: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    """Build JSON structure for a single step based on its type."""
    step_type = step_details.get('step_type', 'Unknown')
    if step_type in ("SynthesisStep", "Unknown"):
        step_type = "Step"
    
    # Normalize atmosphere
    atmosphere_val = step_details.get('vessel_environment') or "N/A"
    if isinstance(atmosphere_val, str) and atmosphere_val.strip().lower() == "ambient air":
        atmosphere_val = "Air"

    # Normalize target pH: use -1 when not available
    target_ph_val = step_details.get('target_ph')
    if isinstance(target_ph_val, (int, float)):
        try:
            target_ph_val = int(target_ph_val)
        except Exception:
            target_ph_val = -1
    else:
        target_ph_val = -1

    # Base step fields (duration added conditionally per step type)
    base_step = {
        "usedVesselName": step_details.get('vessel_name') or "N/A",
        "usedVesselType": step_details.get('vessel_type') or "N/A",
        "stepNumber": step_details.get('order', 0),
        "atmosphere": atmosphere_val,
        "targetPH": target_ph_val,
        "comment": step_details.get('comment') or "N/A"
    }
    
    # Add duration for all step types EXCEPT Filter
    if step_type not in ("Filter",):
        base_step["duration"] = step_details.get('duration') or "N/A"
    
    if step_type == "Stir":
        base_step.update({
            "temperature": step_details.get('target_temperature') or "N/A",
            "wait": step_details.get('is_wait') if step_details.get('is_wait') is not None else False
        })
    elif step_type == "HeatChill":
        base_step.update({
            "usedDevice": step_details.get('device_label') or "N/A",
            "targetTemperature": step_details.get('target_temperature') or "N/A",
            "heatingCoolingRate": step_details.get('heating_cooling_rate') or "N/A",
            "underVacuum": step_details.get('is_vacuum') if step_details.get('is_vacuum') is not None else False,
            "sealedVessel": step_details.get('is_sealed') if step_details.get('is_sealed') is not None else False,
            "stir": step_details.get('is_stirred') if step_details.get('is_stirred') is not None else False,
        })
    elif step_type == "Filter":
        base_step.update({
            "washingSolvent": chemicals.get('washingSolvent', []),
            "vacuumFiltration": step_details.get('is_vacuum_filtration') if step_details.get('is_vacuum_filtration') is not None else False,
            "numberOfFiltrations": step_details.get('is_repeated') if step_details.get('is_repeated') is not None else 1
        })
    elif step_type == "Add":
        base_step.update({
            "addedChemical": chemicals.get('addedChemical', []),
            "stir": step_details.get('is_stirred') if step_details.get('is_stirred') is not None else False,
            "isLayered": step_details.get('is_layered') if step_details.get('is_layered') is not None else False,
        })
    elif step_type == "Dissolve":
        base_step.update({
            "solvent": chemicals.get('solvent', [])
        })
    elif step_type == "Transfer":
        base_step.update({
            "targetVesselName": step_details.get('target_vessel_name') or "N/A",
            "targetVesselType": step_details.get('target_vessel_type') or "N/A",
            "transferedAmount": step_details.get('transferred_amount') or "N/A",
            "isLayered": step_details.get('is_layered_transfer') if step_details.get('is_layered_transfer') is not None else False
        })
    elif step_type == "Crystallize":
        base_step.update({
            "targetTemperature": step_details.get('crystallization_temperature') or "N/A"
        })
    elif step_type == "Sonicate":
        base_step.update({
            "duration": step_details.get('duration') or "N/A"
        })
    else:
        # Generic fallback type
        base_step.update({})
    
    return {step_type: base_step}


def build_json_structure(graph: Graph, namespaces: Dict[str, Namespace], syntheses: List[Dict[str, str]], debug: bool = False) -> Dict[str, Any]:
    """Build JSON with only chemicals per synthesis (via hasChemicalInput)."""
    # Map synthesis URI -> list of CCDC number values via legacy path (fallback only)
    ccdc_map = query_ccdc_numbers(graph)
    synthesis_list: List[Dict[str, Any]] = []
    for synthesis in syntheses:
        # Use ASCII-safe printing to avoid Unicode encoding errors
        try:
            print(f"\nBuilding chemicals JSON for synthesis: {synthesis['label']}")
        except UnicodeEncodeError:
            print(f"\nBuilding chemicals JSON for synthesis: {synthesis['label'].encode('ascii', 'replace').decode('ascii')}")
        inputs = query_synthesis_inputs(graph, synthesis['uri'])
        # Aggregate by IRI (fallback to label if IRI missing)
        aggregated: Dict[str, Dict[str, Any]] = {}
        for inp in inputs:
            label = inp.get('label', 'N/A')
            iri = inp.get('uri', '')
            amount = inp.get('amount', 'N/A')
            alt_names = inp.get('alternative_names', [])

            key = iri if iri else f"label::{label}"
            entry = aggregated.setdefault(key, {"labels": [], "iri": iri, "amounts": []})

            # Add primary label
            if label and label != "N/A":
                entry["labels"].append(label)
            # Add alternative names
            for alt in alt_names:
                if alt and alt != "N/A" and alt not in entry["labels"]:
                    entry["labels"].append(alt)
            if amount and amount != "N/A":
                entry["amounts"].append(amount)

        # Build merged chemical entries (labels only; no IRIs)
        chemicals: List[Dict[str, Any]] = []
        for entry in aggregated.values():
            # Deduplicate labels while preserving order
            seen: set[str] = set()
            labels_dedup: List[str] = []
            for lbl in entry["labels"]:
                if lbl not in seen:
                    seen.add(lbl)
                    labels_dedup.append(lbl)

            name_list = labels_dedup[:]

            # Concatenate amounts; if none, set to N/A. Normalize units.
            amounts_uniq: List[str] = []
            seen_amt: set[str] = set()
            for amt in entry["amounts"]:
                if amt not in seen_amt:
                    seen_amt.add(amt)
                    amounts_uniq.append(amt)
            amount_str = " ; ".join(amounts_uniq) if amounts_uniq else "N/A"

            chemicals.append({
                "chemicalName": name_list,
                "chemicalAmount": amount_str,
            })
        # Build product labels and CCDC per synthesis via robust paths
        outputs_info = query_outputs(graph, synthesis['uri'])
        product_labels = outputs_info.get('labels', [])

        # Build CCDC number per synthesis (single string) with fallback chain:
        # 1. Try outputs_info (includes ontomops:hasCCDCNumber via query_outputs)
        # 2. If that's empty or only contains N/A, fall back to legacy ccdc_map
        # Only keep non-empty, non-NA values; pick the first such value deterministically
        
        # First, try outputs_info which includes multiple paths (OntoSpecies, OntoSyn, OntoMOPs)
        ccdc_vals_primary = outputs_info.get('ccdc', [])
        ccdc_clean: List[str] = []
        seen_ccdc: set[str] = set()
        
        # Filter primary results
        for raw in ccdc_vals_primary:
            v = str(raw or "").strip()
            if not v:
                continue
            vu = v.upper()
            if vu in ("N/A", "NA"):
                continue
            if v not in seen_ccdc:
                seen_ccdc.add(v)
                ccdc_clean.append(v)
        
        # If no valid CCDC found in primary method, try legacy fallback
        if not ccdc_clean:
            ccdc_vals_fallback = ccdc_map.get(synthesis['uri'], [])
            for raw in ccdc_vals_fallback:
                v = str(raw or "").strip()
                if not v:
                    continue
                vu = v.upper()
                if vu in ("N/A", "NA"):
                    continue
                if v not in seen_ccdc:
                    seen_ccdc.add(v)
                    ccdc_clean.append(v)
        
        ccdc_str = ccdc_clean[0] if ccdc_clean else ""

        # Steps: build normalized per-step JSON without IRIs and with expected fields
        steps_raw = query_synthesis_steps(graph, synthesis['uri'])
        steps_with_keys: List[tuple[int, Dict[str, Any]]] = []
        for s in steps_raw:
            # Extract normalized step details and chemicals per step
            step_uri = s.get('uri', '')
            sd = query_step_details(graph, namespaces, step_uri)
            sc = query_step_chemicals(graph, namespaces, step_uri)
            details = build_step_json(sd, sc)
            # Debug: include raw step IRI under each step object
            if debug:
                for k, v in details.items():
                    if isinstance(v, dict):
                        v["iri"] = step_uri
            # Normalize order to stepNumber
            try:
                step_num = int(s.get('order')) if s.get('order') else 0
            except Exception:
                step_num = 0
            # Inject normalized stepNumber under the inner object
            for k, v in details.items():
                if isinstance(v, dict):
                    v.setdefault("stepNumber", step_num)
            steps_with_keys.append((step_num, details))

        # Sort steps by stepNumber
        steps_full = [d for _, d in sorted(steps_with_keys, key=lambda x: x[0])]

        # Product names: Always include synthesis label first, then add any output labels
        # This ensures the synthesis name (e.g., "Structural_transformation_from_VMOP-α_to_VMOP-β") is always present
        prod_names = [synthesis['label']]
        if product_labels:
            prod_names.extend(product_labels)
        
        # Deduplicate while preserving order
        seen_names: set[str] = set()
        prod_names_dedup: List[str] = []
        for nm in prod_names:
            if nm and nm not in seen_names:
                seen_names.add(nm)
                prod_names_dedup.append(nm)

        synthesis_entry = {
            "steps": steps_full,
            "productNames": prod_names_dedup,
            "productCCDCNumber": ccdc_str,
        }
        if debug:
            synthesis_entry["iri"] = synthesis['uri']
        synthesis_list.append(synthesis_entry)
        print(f"Added {len(chemicals)} chemicals to synthesis")
    return {"Synthesis": synthesis_list}


def main():
    """Main function to build complete JSON with populated steps."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Convert TTL to steps JSON')
    parser.add_argument('--input', default='output.ttl', help='Input TTL file path')
    parser.add_argument('--output', default='converted_steps.json', help='Output JSON file path')
    args = parser.parse_args()
    
    print("=== Building complete JSON with populated steps ===")
    
    # Load TTL file
    graph = load_ttl_file(args.input)
    
    # Get namespaces
    namespaces = get_namespaces(graph)
    
    # Query ChemicalSynthesis entities, fallback to hasSynthesisStep linkage
    syntheses = query_chemical_syntheses(graph, namespaces)
    if not syntheses:
        syntheses = query_syntheses_via_steps(graph, namespaces)
    
    # Build complete JSON structure with populated steps
    json_data = build_json_structure(graph, namespaces, syntheses)
    
    # Save to file
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(json_data, f, indent=4, ensure_ascii=False)
    
    print(f"\nComplete JSON structure built with {len(syntheses)} synthesis procedures")
    print(f"Output saved to {args.output}")


if __name__ == "__main__":
    main()