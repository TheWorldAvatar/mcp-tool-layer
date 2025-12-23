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
        # Use ASCII-safe printing to avoid Unicode encoding errors
        try:
            print(f"Found synthesis: {synthesis_label}")
        except UnicodeEncodeError:
            print(f"Found synthesis: {synthesis_label.encode('ascii', 'replace').decode('ascii')}")
    
    print(f"Total ChemicalSynthesis entities found: {len(syntheses)}")
    return syntheses


def query_synthesis_inputs(graph: Graph, namespaces: Dict[str, Namespace], synthesis_uri: str) -> List[Dict[str, Any]]:
    """Query chemical inputs for a synthesis.
    
    Collects all rdfs:label values and ontosyn:hasAlternativeNames values.
    Excludes labels that match ontomops:hasCBUFormula to avoid formula duplication.
    """
    
    ontosyn = namespaces.get('ontosyn')
    rdfs = namespaces.get('rdfs')
    ontomops = namespaces.get('ontomops')
    
    if not ontosyn or not rdfs:
        return []
    
    # SPARQL query to get chemical inputs with all labels, alternative names, and CBU formula
    query = """
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    PREFIX ontomops: <https://www.theworldavatar.com/kg/ontomops/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    
    SELECT DISTINCT ?chemical ?chemicalLabel ?altName ?amount ?formula ?purity ?supplierName ?cbuFormula
    WHERE {
        ?synthesis ontosyn:hasChemicalInput ?chemical .
        OPTIONAL { ?chemical rdfs:label ?chemicalLabel }
        OPTIONAL { ?chemical ontosyn:hasAlternativeNames ?altName }
        OPTIONAL { ?chemical ontosyn:hasAmount ?amount }
        OPTIONAL { ?chemical ontosyn:hasChemicalFormula ?formula }
        OPTIONAL { ?chemical ontosyn:hasPurity ?purity }
        OPTIONAL { ?chemical ontomops:hasCBUFormula ?cbuFormula }
        OPTIONAL { 
            ?chemical ontosyn:isSuppliedBy ?supplier .
            ?supplier rdfs:label ?supplierName .
        }
    }
    """
    
    results = graph.query(query, initBindings={'synthesis': URIRef(synthesis_uri)})
    
    # Group by chemical URI to collect all labels and alternative names
    chemicals_dict = {}
    for row in results:
        chemical_uri = str(row.chemical)
        
        if chemical_uri not in chemicals_dict:
            chemicals_dict[chemical_uri] = {
                'labels': [],
                'alt_names': [],
                'amount': str(row.amount) if row.amount else "N/A",
                'formula': str(row.formula) if row.formula else "N/A",
                'purity': str(row.purity) if row.purity else "N/A",
                'supplier_name': str(row.supplierName) if row.supplierName else "N/A",
                'cbu_formula': str(row.cbuFormula) if row.cbuFormula else None
            }
        
        # Collect all labels
        if row.chemicalLabel:
            label = str(row.chemicalLabel)
            if label not in chemicals_dict[chemical_uri]['labels']:
                chemicals_dict[chemical_uri]['labels'].append(label)
        
        # Collect all alternative names
        if row.altName:
            alt_name = str(row.altName)
            if alt_name not in chemicals_dict[chemical_uri]['alt_names']:
                chemicals_dict[chemical_uri]['alt_names'].append(alt_name)
    
    # Build final inputs list
    inputs = []
    for chem_data in chemicals_dict.values():
        # Combine all names, excluding CBU formula from labels
        all_names = []
        cbu_formula = chem_data['cbu_formula']
        
        # Add labels that don't match CBU formula
        for label in chem_data['labels']:
            if not cbu_formula or label != cbu_formula:
                if label not in all_names:
                    all_names.append(label)
        
        # Add all alternative names
        for alt_name in chem_data['alt_names']:
            if alt_name not in all_names:
                all_names.append(alt_name)
        
        # If no names after filtering, use "Unknown"
        if not all_names:
            all_names = ["Unknown"]
        
        inputs.append({
            'chemicalName': all_names,
            'chemicalAmount': chem_data['amount'],
            'chemicalFormula': chem_data['formula'],
            'purity': chem_data['purity'],
            'supplierName': chem_data['supplier_name']
        })
    
    return inputs


def extract_yield(graph: Graph, namespaces: Dict[str, Namespace], output_uri: str) -> str:
    """Extract yield numerical value from a ChemicalOutput via ontosyn:hasYield and render as "<value> percent".
    Falls back to N/A if no numerical value is found.
    """
    ontosyn = namespaces.get('ontosyn')
    if not ontosyn:
        return "N/A"

    # SPARQL: only require numerical value; unit is assumed percent per schema
    query = """
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    PREFIX om-2: <http://www.ontology-of-units-of-measure.org/resource/om-2/>

    SELECT DISTINCT ?yieldValue
    WHERE {
        ?output ontosyn:hasYield ?yieldUri .
        ?yieldUri om-2:hasNumericalValue ?yieldValue .
    } LIMIT 1
    """

    results = graph.query(query, initBindings={'output': URIRef(output_uri)})

    for row in results:
        if getattr(row, 'yieldValue', None):
            try:
                yield_value = float(row.yieldValue)
                if abs(yield_value - round(yield_value)) < 1e-6:
                    return f"{int(round(yield_value))}%"
                return f"{yield_value}%"
            except (ValueError, TypeError):
                continue

    return "N/A"


def query_synthesis_outputs(graph: Graph, namespaces: Dict[str, Namespace], synthesis_uri: str, ontomops_data: Dict[str, Dict[str, str]], debug: bool = False) -> List[Dict[str, Any]]:
    """Query chemical outputs for a synthesis.
    Robust to two patterns observed in merged TTLs:
    1) hasChemicalOutput directly to ontospecies:Species
    2) hasChemicalOutput to ontosyn:ChemicalOutput (optionally blank) with ontosyn:isRepresentedBy a MOP

    Falls back to Species label/CCDC/formula when OntoMOPs linkage is unavailable.
    """
    
    ontosyn = namespaces.get('ontosyn')
    rdfs = namespaces.get('rdfs')
    
    if not ontosyn or not rdfs:
        return []
    
    # Get all outputs and their labels (Species or ChemicalOutput)
    query = """
    PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    
    SELECT DISTINCT ?output ?outputLabel
    WHERE {
        ?synthesis ontosyn:hasChemicalOutput ?output .
        OPTIONAL { ?output rdfs:label ?outputLabel }
    }
    """
    
    results = graph.query(query, initBindings={'synthesis': URIRef(synthesis_uri)})
    
    outputs = []
    def _species_info(out_uri: str) -> Dict[str, str]:
        q = f"""
        PREFIX ontospecies: <http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#>
        PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
        PREFIX dc: <http://purl.org/dc/elements/1.1/>
        SELECT DISTINCT ?label ?ccdcVal ?ccdcId ?mfLabel WHERE {{
          OPTIONAL {{ <{out_uri}> rdfs:label ?label }}
          OPTIONAL {{
            <{out_uri}> ontospecies:hasCCDCNumber ?c .
            OPTIONAL {{ ?c ontospecies:hasCCDCNumberValue ?ccdcVal }}
            OPTIONAL {{ ?c dc:identifier ?ccdcId }}
          }}
          OPTIONAL {{
            <{out_uri}> ontospecies:hasMolecularFormula ?mf .
            OPTIONAL {{ ?mf rdfs:label ?mfLabel }}
          }}
        }} LIMIT 1
        """
        for r in graph.query(q):
            return {
                'label': (str(r.label) if getattr(r, 'label', None) else 'Unknown'),
                'ccdc_id': (str(getattr(r, 'ccdcVal', None)) if getattr(r, 'ccdcVal', None) else (str(getattr(r, 'ccdcId', None)) if getattr(r, 'ccdcId', None) else 'N/A')),
                'formula': (str(r.mfLabel) if getattr(r, 'mfLabel', None) else 'N/A'),
            }
        return {'label': 'Unknown', 'ccdc_id': 'N/A', 'formula': 'N/A'}

    def _is_species(out_uri: str) -> bool:
        q = f"""
        ASK {{ <{out_uri}> a <http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#Species> }}
        """
        return bool(graph.query(q).askAnswer)

    def _mop_via_is_represented_by(out_uri: str) -> Optional[str]:
        q = f"""
        PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
        SELECT DISTINCT ?mop WHERE {{ <{out_uri}> ontosyn:isRepresentedBy ?mop }} LIMIT 1
        """
        for r in graph.query(q):
            return str(r.mop)
        return None

    # Aggregate possibly-multiple outputs (Species and ChemicalOutput) by human label
    aggregated: Dict[str, Dict[str, Any]] = {}

    def _ensure_entry(lbl: str) -> Dict[str, Any]:
        rec = aggregated.get(lbl)
        if not rec:
            rec = {
                'names': [lbl],
                'chemicalFormula': 'N/A',
                'yield': 'N/A',
                'CCDCNumber': 'N/A'
            }
            aggregated[lbl] = rec
        return rec

    # Helper: check if a node is a ChemicalOutput
    def _is_chemical_output(out_uri: str) -> bool:
        q = f"""
        ASK {{ <{out_uri}> a <https://www.theworldavatar.com/kg/OntoSyn/ChemicalOutput> }}
        """
        try:
            return bool(graph.query(q).askAnswer)
        except Exception:
            return False

    for row in results:
        output_uri = str(row.output)
        output_label = str(row.outputLabel) if row.outputLabel else "Unknown"

        # Ensure an aggregate record for this label
        rec = _ensure_entry(output_label)

        if _is_species(output_uri):
            sp = _species_info(output_uri)
            # Normalize label merge
            if sp['label'] and sp['label'] != output_label:
                rec['names'] = [sp['label']]
            # Prefer species molecular formula if present
            if sp['formula'] and sp['formula'] != 'N/A':
                rec['chemicalFormula'] = sp['formula']
            # Species-sourced CCDC (preferred)
            if sp['ccdc_id'] and sp['ccdc_id'] != 'N/A':
                rec['CCDCNumber'] = sp['ccdc_id']
            if debug and 'iri' not in rec:
                rec['iri'] = output_uri
        else:
            # ChemicalOutput: attach yield and try to map to MOP formula via isRepresentedBy
            mop_uri = _mop_via_is_represented_by(output_uri)
            if mop_uri:
                info = ontomops_data.get(mop_uri.lower(), {})
                if info.get('mop_formula') and info.get('mop_formula') != 'N/A':
                    rec['chemicalFormula'] = info.get('mop_formula')
                # Use CCDC from MOP only if species did not provide one
                if rec.get('CCDCNumber') in (None, '', 'N/A') and info.get('ccdc_number') and info.get('ccdc_number') != 'N/A':
                    rec['CCDCNumber'] = info.get('ccdc_number')
                # Optionally update label from known MOP label
                lbl = info.get('label')
                if lbl and lbl != output_label:
                    rec['names'] = [lbl]
            # Yield always from ChemicalOutput
            yv = extract_yield(graph, namespaces, output_uri)
            if yv and yv != 'N/A':
                rec['yield'] = yv
            if debug:
                rec['iri'] = output_uri

    # Emit aggregated list, preferring entries that have a valid CCDCNumber
    # If both a Species and a ChemicalOutput exist for the same product, keep only the one with CCDCNumber
    filtered: List[Dict[str, Any]] = []
    for rec in aggregated.values():
        ccdc = (rec.get('CCDCNumber') or '').strip()
        filtered.append(rec)

    # Prefer records with CCDC; drop duplicates without CCDC when a CCDC-bearing record shares the same canonical name
    def _canon(lbls: List[str]) -> str:
        return (lbls[0] if lbls else '').strip().lower()

    name_to_best: Dict[str, Dict[str, Any]] = {}
    for rec in filtered:
        key = _canon(rec.get('names') or [])
        has_ccdc = bool((rec.get('CCDCNumber') or '').strip()) and (rec.get('CCDCNumber') not in ("N/A", "na", "Na", ""))
        prev = name_to_best.get(key)
        if prev is None:
            name_to_best[key] = rec
        else:
            prev_has = bool((prev.get('CCDCNumber') or '').strip()) and (prev.get('CCDCNumber') not in ("N/A", "na", "Na", ""))
            if has_ccdc and not prev_has:
                name_to_best[key] = rec

    outputs = list(name_to_best.values())

    # Final rule: if any outputs have a valid CCDCNumber, keep ONLY those; drop others
    def _has_valid_ccdc(rec: Dict[str, Any]) -> bool:
        c = str((rec or {}).get('CCDCNumber') or '').strip().lower()
        return c not in ('', 'n/a', 'na')

    with_ccdc = [rec for rec in outputs if _has_valid_ccdc(rec)]
    if with_ccdc:
        outputs = with_ccdc
    
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




def build_procedure_json(graph: Graph, namespaces: Dict[str, Namespace], synthesis: Dict[str, str], ontomops_data: Dict[str, Dict[str, str]], debug: bool = False) -> Dict[str, Any]:
    """Build JSON structure for a single synthesis procedure."""
    
    procedure_name = synthesis['label']
    
    # Query inputs and outputs
    inputs = query_synthesis_inputs(graph, namespaces, synthesis['uri'])
    outputs = query_synthesis_outputs(graph, namespaces, synthesis['uri'], ontomops_data, debug=debug)
    
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


def build_json_structure(graph: Graph, namespaces: Dict[str, Namespace], syntheses: List[Dict[str, str]], ontomops_data: Dict[str, Dict[str, str]], debug: bool = False) -> Dict[str, Any]:
    """Build complete JSON structure."""
    
    synthesis_procedures = []
    
    for synthesis in syntheses:
        # Use ASCII-safe printing to avoid Unicode encoding errors
        try:
            print(f"\nBuilding JSON for: {synthesis['label']}")
        except UnicodeEncodeError:
            print(f"\nBuilding JSON for: {synthesis['label'].encode('ascii', 'replace').decode('ascii')}")
        procedure_json = build_procedure_json(graph, namespaces, synthesis, ontomops_data, debug=debug)
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
