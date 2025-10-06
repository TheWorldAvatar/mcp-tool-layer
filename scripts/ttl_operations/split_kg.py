#!/usr/bin/env python3
"""
Split output.ttl into individual TTL files, one for each synthesis object.
Each file contains the synthesis object and all entities connected to it.
"""

import rdflib
from rdflib import Graph, URIRef, Namespace
import os


def _is_synthesis_related_entity(entity_uri, graph):
    """
    Check if an entity is directly related to synthesis processes.
    Returns True for ChemicalInput, ChemicalOutput, and synthesis step entities.
    Returns False for other ChemicalSynthesis entities (to avoid cross-contamination).
    """
    ONTOSYN = Namespace("https://www.theworldavatar.com/kg/OntoSyn/")
    RDF = Namespace("http://www.w3.org/1999/02/22-rdf-syntax-ns#")
    
    # Get entity types
    entity_types = list(graph.objects(entity_uri, RDF.type))
    
    # Check if it's a synthesis-related entity (but not another ChemicalSynthesis)
    synthesis_related_types = [
        ONTOSYN.ChemicalInput,
        ONTOSYN.ChemicalOutput,
        ONTOSYN.Add,
        ONTOSYN.Dissolve,
        ONTOSYN.Filter,
        ONTOSYN.HeatChill,
        ONTOSYN.Separate,
        ONTOSYN.Sonicate,
        ONTOSYN.Stir,
        ONTOSYN.Transfer,
        # Add other synthesis step types as needed
    ]
    
    # Return True if entity has any of the synthesis-related types
    # Return False if it's a ChemicalSynthesis (to avoid including other synthesis processes)
    for entity_type in entity_types:
        if entity_type == ONTOSYN.ChemicalSynthesis:
            return False  # Exclude other synthesis processes
        if entity_type in synthesis_related_types:
            return True
    
    return False


def split_knowledge_graph(input_file="output.ttl", output_prefix="output"):
    """
    Split the knowledge graph into individual TTL files for each synthesis object.
    
    Args:
        input_file (str): Path to the input TTL file
        output_prefix (str): Prefix for output files (will create output_1.ttl, output_2.ttl, etc.)
    """
    
    # Load the graph
    g = Graph()
    try:
        g.parse(input_file, format="ttl")
        print(f"Loaded {len(g)} triples from {input_file}")
    except Exception as e:
        print(f"Error loading {input_file}: {e}")
        return
    
    # Define namespaces
    ONTOSYN = Namespace("https://www.theworldavatar.com/kg/OntoSyn/")
    RDFS = Namespace("http://www.w3.org/2000/01/rdf-schema#")
    RDF = Namespace("http://www.w3.org/1999/02/22-rdf-syntax-ns#")
    XSD = Namespace("http://www.w3.org/2001/XMLSchema#")
    
    # Find all synthesis objects (ChemicalSynthesis entities)
    synthesis_objects = []
    for subject in g.subjects(RDF.type, ONTOSYN.ChemicalSynthesis):
        synthesis_objects.append(subject)
    
    print(f"Found {len(synthesis_objects)} synthesis objects")
    
    # Process each synthesis object
    for i, synthesis_uri in enumerate(synthesis_objects, 1):
        print(f"Processing synthesis object {i}: {synthesis_uri}")
        
        # Create a new graph for this synthesis object
        synthesis_graph = Graph()
        
        # Add namespace bindings
        synthesis_graph.bind("ontosyn", ONTOSYN)
        synthesis_graph.bind("rdfs", RDFS)
        synthesis_graph.bind("xsd", XSD)
        
        # Build connected subgraph starting from synthesis entity
        visited_entities = set()
        entities_to_process = [synthesis_uri]
        
        while entities_to_process:
            current_entity = entities_to_process.pop(0)
            if current_entity in visited_entities:
                continue
            visited_entities.add(current_entity)
            
            # Add all direct outgoing triples from current entity
            for predicate, obj in g.predicate_objects(current_entity):
                synthesis_graph.add((current_entity, predicate, obj))
                
                # If object is a URI and represents a meaningful connected entity, explore it
                if isinstance(obj, URIRef) and _is_synthesis_related_entity(obj, g):
                    entities_to_process.append(obj)
            
            # Add incoming triples only if they come from synthesis-related entities
            for subj, predicate in g.subject_predicates(current_entity):
                if _is_synthesis_related_entity(subj, g) or subj == synthesis_uri:
                    synthesis_graph.add((subj, predicate, current_entity))
                    if isinstance(subj, URIRef) and subj not in visited_entities:
                        entities_to_process.append(subj)
        
        # Get synthesis label for filename info
        synthesis_labels = list(g.objects(synthesis_uri, RDFS.label))
        synthesis_label = str(synthesis_labels[0]) if synthesis_labels else f"synthesis_{i}"
        
        # Write to individual TTL file
        output_file = f"{output_prefix}_{i}.ttl"
        try:
            synthesis_graph.serialize(destination=output_file, format="ttl")
            print(f"  → Created {output_file} with {len(synthesis_graph)} triples")
            print(f"     Synthesis: {synthesis_label}")
            print(f"     Connected entities: {len(connected_entities)}")
        except Exception as e:
            print(f"  → Error writing {output_file}: {e}")
    
    print(f"\nCompleted splitting into {len(synthesis_objects)} files")


def get_synthesis_summary(input_file="output.ttl"):
    """
    Print a summary of synthesis objects in the TTL file.
    """
    g = Graph()
    try:
        g.parse(input_file, format="ttl")
    except Exception as e:
        print(f"Error loading {input_file}: {e}")
        return
    
    ONTOSYN = Namespace("https://www.theworldavatar.com/kg/OntoSyn/")
    RDFS = Namespace("http://www.w3.org/2000/01/rdf-schema#")
    RDF = Namespace("http://www.w3.org/1999/02/22-rdf-syntax-ns#")
    
    print(f"Summary of {input_file}:")
    print(f"Total triples: {len(g)}")
    
    synthesis_objects = list(g.subjects(RDF.type, ONTOSYN.ChemicalSynthesis))
    print(f"Synthesis objects: {len(synthesis_objects)}")
    
    for i, synthesis_uri in enumerate(synthesis_objects, 1):
        labels = list(g.objects(synthesis_uri, RDFS.label))
        label = str(labels[0]) if labels else "No label"
        
        # Count connected inputs, outputs, steps
        inputs = list(g.objects(synthesis_uri, ONTOSYN.hasChemicalInput))
        outputs = list(g.objects(synthesis_uri, ONTOSYN.hasChemicalOutput))
        steps = list(g.objects(synthesis_uri, ONTOSYN.hasSynthesisStep))
        
        print(f"  {i}. {label}")
        print(f"     URI: {synthesis_uri}")
        print(f"     Inputs: {len(inputs)}, Outputs: {len(outputs)}, Steps: {len(steps)}")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Split knowledge graph by synthesis objects")
    parser.add_argument("--input", "-i", default="output.ttl", help="Input TTL file (default: output.ttl)")
    parser.add_argument("--output-prefix", "-o", default="output", help="Output file prefix (default: output)")
    parser.add_argument("--summary", "-s", action="store_true", help="Show summary only, don't split")
    
    args = parser.parse_args()
    
    if args.summary:
        get_synthesis_summary(args.input)
    else:
        split_knowledge_graph(args.input, args.output_prefix)
