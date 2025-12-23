#!/usr/bin/env python3
"""
CBU (Chemical Building Unit) TTL to JSON conversion using proper SPARQL queries and RDF libraries.

This script extracts Chemical Building Unit data from ontomops_extension.ttl and output.ttl files
to create JSON output matching the CBU ground truth format.
"""

import json
from rdflib import Graph, Namespace, URIRef, RDF, RDFS, Literal
from rdflib.namespace import OWL
from rdflib.plugins.sparql import prepareQuery
from typing import Dict, List, Any
def build_cbu_json_from_graph(graph: Graph) -> Dict[str, Any]:
    """
    Build CBU JSON structure directly from an rdflib Graph, scanning synthesis→MOP
    links and the MOP's ChemicalBuildingUnits. Creates one entry per MOP, even if
    multiple MOPs share the same CCDC number. Always fills fields even if data is
    missing (using "N/A" and empty arrays as fallbacks).

    Output schema:
    {
      "synthesisProcedures": [
        {
          "mopCCDCNumber": str,
          "cbuFormula1": str,
          "cbuSpeciesNames1": [str, ...],
          "cbuFormula2": str,
          "cbuSpeciesNames2": [str, ...]
        }, ...
      ]
    }
    """
    # Namespaces
    ONTOMOPS = Namespace("https://www.theworldavatar.com/kg/ontomops/")
    ONTOSYN = Namespace("https://www.theworldavatar.com/kg/OntoSyn/")
    QUDT = Namespace("http://qudt.org/schema/qudt/")

    # Aggregate by MOP to ensure one entry per MOP
    # Even when multiple MOPs share a CCDC, keep them separate
    mop_to_cbus: Dict[str, Dict[str, Any]] = {}

    def _normalize_string(s: str) -> str:
        if not s:
            return s
        # Replace specific variant everywhere
        s = s.replace("[V6O6(C6H5PO3)(CH3O)9]", "[V6O6(OCH3)9(PhPO3)]")
        return s

    def _literal_to_float(v: Any) -> float | None:
        try:
            if isinstance(v, Literal):
                return float(str(v))
            return float(v)  # type: ignore
        except Exception:
            return None

    def add_mop_to_aggregation(mop: URIRef) -> None:
        """Collect CBUs for a given MOP node into mop_to_cbus."""
        ccdc_vals = list(graph.objects(mop, URIRef(str(ONTOMOPS) + "hasCCDCNumber")))
        ccdc = str(ccdc_vals[0]) if ccdc_vals else "N/A"

        # Use MOP URI as the key to ensure each MOP gets its own entry
        mop_key = str(mop)
        if mop_key not in mop_to_cbus:
            mop_to_cbus[mop_key] = {
                'ccdc': ccdc,
                'cbus': {},
                'yield': None
            }

        for cbu in graph.objects(mop, URIRef(str(ONTOMOPS) + "hasChemicalBuildingUnit")):
            # Prefer ontomops:hasCBUFormula for the canonical formula
            cbu_formula_vals = list(graph.objects(cbu, URIRef(str(ONTOMOPS) + "hasCBUFormula")))
            formula = str(cbu_formula_vals[0]) if cbu_formula_vals else None
            if not formula:
                # Try to get formula from the label (which may be the formula itself)
                labels = [str(l) for l in graph.objects(cbu, RDFS.label)]
                formula = labels[0] if labels else "N/A"
            formula = _normalize_string(str(formula))

            names_set = set()
            # Include all labels on the CBU itself (may have multiple)
            for l in graph.objects(cbu, RDFS.label):
                names_set.add(_normalize_string(str(l)))
            # Include alternative names on the CBU - these are important for chemical species identification
            for alt_name_literal in graph.objects(cbu, URIRef(str(ONTOSYN) + "hasAlternativeNames")):
                alt_name_str = str(alt_name_literal).strip()
                # Remove surrounding quotes if present
                if alt_name_str.startswith('"') and alt_name_str.endswith('"'):
                    alt_name_str = alt_name_str[1:-1]
                if alt_name_str:
                    names_set.add(_normalize_string(alt_name_str))
            # Fallback: include formula if we still have no names
            if not names_set and formula != "N/A":
                names_set.add(_normalize_string(formula))
            for chem_input in graph.objects(cbu, OWL.sameAs):
                for ci_label in graph.objects(chem_input, RDFS.label):
                    names_set.add(_normalize_string(str(ci_label)))
                # Also collect alternative names from chemical inputs
                for ci_alt_literal in graph.objects(chem_input, URIRef(str(ONTOSYN) + "hasAlternativeNames")):
                    alt_name_str = str(ci_alt_literal).strip()
                    # Remove surrounding quotes if present
                    if alt_name_str.startswith('"') and alt_name_str.endswith('"'):
                        alt_name_str = alt_name_str[1:-1]
                    if alt_name_str:
                        names_set.add(_normalize_string(alt_name_str))
                for ci_formula in graph.objects(chem_input, URIRef(str(ONTOSYN) + "hasChemicalFormula")):
                    names_set.add(_normalize_string(str(ci_formula)))

            if formula not in mop_to_cbus[mop_key]['cbus']:
                mop_to_cbus[mop_key]['cbus'][formula] = []
            for name in sorted(names_set):
                if name not in mop_to_cbus[mop_key]['cbus'][formula]:
                    mop_to_cbus[mop_key]['cbus'][formula].append(name)

    # Path A: Iterate syntheses → outputs → isRepresentedBy → MOP
    for synth in graph.subjects(RDF.type, URIRef(str(ONTOSYN) + "ChemicalSynthesis")):
        for chem_out in graph.objects(synth, URIRef(str(ONTOSYN) + "hasChemicalOutput")):
            # Attempt to capture yields associated with this output
            yield_vals: List[float] = []
            for y in graph.objects(chem_out, URIRef(str(ONTOSYN) + "hasYield")):
                # Try common numeric value predicates
                for pv in graph.objects(y, URIRef(str(ONTOSYN) + "hasNumericalValue")):
                    fv = _literal_to_float(pv)
                    if fv is not None:
                        yield_vals.append(fv)
                for pv in graph.objects(y, URIRef(str(QUDT) + "numericValue")):
                    fv = _literal_to_float(pv)
                    if fv is not None:
                        yield_vals.append(fv)
            for mop in graph.objects(chem_out, URIRef(str(ONTOSYN) + "isRepresentedBy")):
                # Store yield for this MOP
                mop_key = str(mop)
                if yield_vals:
                    mx = max(yield_vals)
                    if mop_key in mop_to_cbus:
                        mop_to_cbus[mop_key]['yield'] = mx
                add_mop_to_aggregation(mop)

    # Path B (fallback or supplemental): directly scan all MOP instances in the graph
    for mop in graph.subjects(RDF.type, URIRef(str(ONTOMOPS) + "MetalOrganicPolyhedron")):
        add_mop_to_aggregation(mop)

    # Build one entry per MOP with its CBUs
    procedures: List[Dict[str, Any]] = []

    # For integrated TTL files, directly query MOPs and their CBUs first
    print("Trying direct MOP processing...")
    mop_type_uri = URIRef(str(ONTOMOPS) + "MetalOrganicPolyhedron")
    print(f"Looking for MOPs with type: {mop_type_uri}")
    all_subjects = list(graph.subjects())
    print(f"Total subjects in graph: {len(all_subjects)}")
    for s in all_subjects[:5]:  # Show first 5
        print(f"Subject: {s}")
    direct_mops_found = 0
    for mop in graph.subjects(RDF.type, mop_type_uri):
        print(f"Found MOP: {mop}")
        direct_mops_found += 1
        ccdc_vals = list(graph.objects(mop, URIRef(str(ONTOMOPS) + "hasCCDCNumber")))
        ccdc = str(ccdc_vals[0]) if ccdc_vals else "N/A"

        if ccdc == "N/A":
            continue

        direct_mops_found += 1
        print(f"Found MOP {mop} with CCDC {ccdc}")

        # Get all CBUs for this MOP
        cbu_formulas = []
        cbu_names = []

        for cbu in graph.objects(mop, URIRef(str(ONTOMOPS) + "hasChemicalBuildingUnit")):
            # Get the formula
            formula_vals = list(graph.objects(cbu, URIRef(str(ONTOMOPS) + "hasCBUFormula")))
            formula = str(formula_vals[0]) if formula_vals else "N/A"

            print(f"Debug: CBU {cbu}, formula_vals: {formula_vals}, formula: {formula}")

            # Get names from labels
            names = []
            for label in graph.objects(cbu, RDFS.label):
                names.append(str(label))

            # Get alternative names - each should be a separate literal
            for alt_name_literal in graph.objects(cbu, URIRef(str(ONTOSYN) + "hasAlternativeNames")):
                alt_name_str = str(alt_name_literal).strip()
                # Remove surrounding quotes if present
                if alt_name_str.startswith('"') and alt_name_str.endswith('"'):
                    alt_name_str = alt_name_str[1:-1]
                if alt_name_str and alt_name_str not in names:
                    names.append(alt_name_str)

            # Get chemical formula if available
            for chem_formula in graph.objects(cbu, URIRef(str(ONTOSYN) + "hasChemicalFormula")):
                chem_formula_str = str(chem_formula).strip()
                if chem_formula_str and chem_formula_str not in names:
                    names.append(chem_formula_str)

            # Also check owl:sameAs links to chemical inputs for additional information
            for chem_input in graph.objects(cbu, OWL.sameAs):
                # Additional labels from chemical inputs
                for ci_label in graph.objects(chem_input, RDFS.label):
                    ci_label_str = str(ci_label).strip()
                    if ci_label_str and ci_label_str not in names:
                        names.append(ci_label_str)

                # Alternative names from chemical inputs
                for ci_alt_literal in graph.objects(chem_input, URIRef(str(ONTOSYN) + "hasAlternativeNames")):
                    alt_name_str = str(ci_alt_literal).strip()
                    # Remove surrounding quotes if present
                    if alt_name_str.startswith('"') and alt_name_str.endswith('"'):
                        alt_name_str = alt_name_str[1:-1]
                    if alt_name_str and alt_name_str not in names:
                        names.append(alt_name_str)

                # Chemical formula from chemical inputs
                for ci_formula in graph.objects(chem_input, URIRef(str(ONTOSYN) + "hasChemicalFormula")):
                    ci_formula_str = str(ci_formula).strip()
                    if ci_formula_str and ci_formula_str not in names:
                        names.append(ci_formula_str)

            cbu_formulas.append(formula)
            cbu_names.append(names)

        # Sort CBUs by formula for consistency
        cbu_data = list(zip(cbu_formulas, cbu_names))
        cbu_data.sort(key=lambda x: x[0])

        entry = {
            "mopCCDCNumber": ccdc,
            "cbuFormula1": cbu_data[0][0] if len(cbu_data) > 0 else "N/A",
            "cbuSpeciesNames1": cbu_data[0][1] if len(cbu_data) > 0 else [],
            "cbuFormula2": cbu_data[1][0] if len(cbu_data) > 1 else "N/A",
            "cbuSpeciesNames2": cbu_data[1][1] if len(cbu_data) > 1 else [],
        }

        procedures.append(entry)

    print(f"Direct processing found {direct_mops_found} MOPs")

    # If we found MOPs directly, return those results
    if procedures:
        print(f"Using direct processing results: {len(procedures)} procedures")
        return {"synthesisProcedures": procedures}

    # Fallback to the original logic
    for mop_key, mop_data in sorted(mop_to_cbus.items()):
        ccdc = mop_data['ccdc']
        cbu_map = mop_data['cbus']
        mop_yield = mop_data['yield']

        # Sort formulas by frequency (for consistency), then alphabetically
        # Since we aggregate per MOP now, we don't need complex frequency logic
        formulas = sorted(cbu_map.keys())

        cbu1_formula = formulas[0] if len(formulas) >= 1 else "N/A"
        cbu2_formula = formulas[1] if len(formulas) >= 2 else "N/A"

        cbu1_names_raw = cbu_map[cbu1_formula] if cbu1_formula in cbu_map else []
        cbu2_names_raw = cbu_map[cbu2_formula] if cbu2_formula in cbu_map else []
        # Remove any name that exactly equals the formula for that entry
        cbu1_names = [n for n in cbu1_names_raw if n != cbu1_formula]
        cbu2_names = [n for n in cbu2_names_raw if n != cbu2_formula]

        entry = {
            "mopCCDCNumber": ccdc,
            "cbuFormula1": cbu1_formula,
            "cbuSpeciesNames1": cbu1_names,
            "cbuFormula2": cbu2_formula,
            "cbuSpeciesNames2": cbu2_names,
        }

        # Fix cases where chemical names were incorrectly placed in formula fields
        # Check cbuFormula1
        if entry.get("cbuFormula1") and entry["cbuFormula1"] != "N/A":
            formula = entry["cbuFormula1"]
            # If formula doesn't start with [ (indicating a chemical formula), it's likely a chemical name
            if not formula.startswith("["):
                # Move the chemical name to species names list
                if formula not in entry.get("cbuSpeciesNames1", []):
                    entry["cbuSpeciesNames1"].append(formula)
                # Clear the formula field
                entry["cbuFormula1"] = "N/A"

        # Check cbuFormula2
        if entry.get("cbuFormula2") and entry["cbuFormula2"] != "N/A":
            formula = entry["cbuFormula2"]
            # If formula doesn't start with [ (indicating a chemical formula), it's likely a chemical name
            if not formula.startswith("["):
                # Move the chemical name to species names list
                if formula not in entry.get("cbuSpeciesNames2", []):
                    entry["cbuSpeciesNames2"].append(formula)
                # Clear the formula field
                entry["cbuFormula2"] = "N/A"

        if mop_yield is not None:
            entry["hasYield"] = mop_yield
        procedures.append(entry)

    return {"synthesisProcedures": procedures}


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

    # Load TTL files from the actual data structure
    import sys
    if len(sys.argv) > 1:
        hash_value = sys.argv[1]
        ttl_files = [f"data/{hash_value}/cbu_derivation/integrated/*.ttl"]
    else:
        # Default to a known hash for testing
        hash_value = "178ef569"
        ttl_files = [f"data/{hash_value}/cbu_derivation/integrated/*.ttl"]

    # Expand glob patterns
    import glob
    expanded_files = []
    for pattern in ttl_files:
        expanded_files.extend(glob.glob(pattern))

    if not expanded_files:
        print(f"No TTL files found for hash {hash_value}")
        return

    print(f"Loading {len(expanded_files)} TTL files for hash {hash_value}")
    graph = load_ttl_files(expanded_files)

    # For integrated TTL files, use direct processing
    print("Processing integrated TTL files directly...")
    procedures = []

    ONTOMOPS = Namespace("https://www.theworldavatar.com/kg/ontomops/")
    RDFS = Namespace("http://www.w3.org/2000/01/rdf-schema#")

    for mop in graph.subjects(RDF.type, URIRef(str(ONTOMOPS) + "MetalOrganicPolyhedron")):
        ccdc_vals = list(graph.objects(mop, URIRef(str(ONTOMOPS) + "hasCCDCNumber")))
        ccdc = str(ccdc_vals[0]) if ccdc_vals else "N/A"

        if ccdc == "N/A":
            continue

        # Get all CBUs for this MOP
        cbu_data = []
        for cbu in graph.objects(mop, URIRef(str(ONTOMOPS) + "hasChemicalBuildingUnit")):
            # Get the formula
            formula_vals = list(graph.objects(cbu, URIRef(str(ONTOMOPS) + "hasCBUFormula")))
            formula = str(formula_vals[0]) if formula_vals else "N/A"

            # Get names from labels
            names = []
            for label in graph.objects(cbu, RDFS.label):
                names.append(str(label))

            cbu_data.append((formula, names))

        # Sort CBUs by formula for consistency
        cbu_data.sort(key=lambda x: x[0])

        entry = {
            "mopCCDCNumber": ccdc,
            "cbuFormula1": cbu_data[0][0] if len(cbu_data) > 0 else "N/A",
            "cbuSpeciesNames1": cbu_data[0][1] if len(cbu_data) > 0 else [],
            "cbuFormula2": cbu_data[1][0] if len(cbu_data) > 1 else "N/A",
            "cbuSpeciesNames2": cbu_data[1][1] if len(cbu_data) > 1 else [],
        }

        procedures.append(entry)

    json_data = {"synthesisProcedures": procedures}

    # Fix cases where chemical names were incorrectly placed in formula fields
    for procedure in json_data["synthesisProcedures"]:
        # Check cbuFormula1
        if procedure.get("cbuFormula1") and procedure["cbuFormula1"] != "N/A":
            formula = procedure["cbuFormula1"]
            # If formula doesn't start with [ (indicating a chemical formula), it's likely a chemical name
            if not formula.startswith("["):
                # Move the chemical name to species names list
                if formula not in procedure.get("cbuSpeciesNames1", []):
                    procedure["cbuSpeciesNames1"].append(formula)
                # Clear the formula field
                procedure["cbuFormula1"] = "N/A"

        # Check cbuFormula2
        if procedure.get("cbuFormula2") and procedure["cbuFormula2"] != "N/A":
            formula = procedure["cbuFormula2"]
            # If formula doesn't start with [ (indicating a chemical formula), it's likely a chemical name
            if not formula.startswith("["):
                # Move the chemical name to species names list
                if formula not in procedure.get("cbuSpeciesNames2", []):
                    procedure["cbuSpeciesNames2"].append(formula)
                # Clear the formula field
                procedure["cbuFormula2"] = "N/A"

    # Save to file
    with open("converted_cbu.json", "w", encoding="utf-8") as f:
        json.dump(json_data, f, indent=2, ensure_ascii=False)

    print(f"\nComplete CBU JSON built with {len(json_data['synthesisProcedures'])} synthesis procedures")
    print("Output saved to converted_cbu.json")


if __name__ == "__main__":
    main()
