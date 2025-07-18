#!/usr/bin/env python
"""
TTL validation operations
Functions for validating Turtle (.ttl) files with rdflib.
"""
import os
from rdflib import Graph
from models.Ontology import OntologyInput, OntologyBuilder
from models.locations import ROOT_DIR, DATA_TEMP_DIR
from models.Resource import Resource
from src.utils.resource_db_operations import ResourceDBOperator

resource_db_operator = ResourceDBOperator()


def create_temp_file(ttl_string: str, file_name: str) -> str:
    """
    Create a temporary file with the given TTL string.
    """
    temp_file_path = os.path.join(DATA_TEMP_DIR, file_name)
    with open(temp_file_path, "w") as f:
        f.write(ttl_string)
    return temp_file_path

def validate_ttl_file(ttl_file_path: str) -> tuple[bool, str]:

    # ✅ Ensure working directory is valid (rdflib workaround)
    try:
        os.getcwd()
    except FileNotFoundError:
        os.chdir(os.path.expanduser("~"))

    # ✅ Confirm the file exists
    if not os.path.exists(ttl_file_path):
        return False, f"TTL file does not exist: {ttl_file_path}"

    # ✅ Parse file content using rdflib with explicit base URI
    try:
        with open(ttl_file_path, "r", encoding="utf-8") as f:
            ttl_str = f.read()

        g = Graph()
        g.parse(data=ttl_str, format="turtle", publicID=f"file://{ttl_file_path}")
        return True, "Valid Turtle."
    except Exception as e:
        return False, f"Invalid Turtle: {e}"


def create_ontology(ontology: OntologyInput, meta_task_name: str, iteration_index: int) -> str:
    """
    Build, validate, and persist an ontology based on *ontology* input.
    Only if the ontology passes validation, the file will be created and registered.
    Otherwise, returns a detailed error message.
    """
    # Try to build the ontology graph in memory
    try:
        builder = OntologyBuilder(ontology)
        success, g = builder.build()
        if not success:
            return f"Failed to build ontology: {g}"
    except Exception as e:
        return f"Failed to build ontology from input. Error: {e}"

    # Prepare output paths
    relative_output_path = f"sandbox/data/{meta_task_name}/{iteration_index}/{meta_task_name}_{iteration_index}.ttl"
    absolute_output_path = os.path.join(ROOT_DIR, relative_output_path)
    temp_file_name = f"{meta_task_name}_{iteration_index}_temp.ttl"
    temp_file_path = os.path.join(DATA_TEMP_DIR, temp_file_name)

    uri = f"file://{absolute_output_path}"
    description = f"Ontology file for {meta_task_name} with {len(ontology.classes)} classes and {len(ontology.properties)} properties"

    # Serialize to a temporary string and write to temp file
    try:
        ttl_string = g.serialize(format="turtle")
    except Exception as e:
        return f"Failed to serialize ontology to Turtle. Error: {e}"

    try:
        temp_file_path = create_temp_file(ttl_string, temp_file_name)
    except Exception as e:
        return f"Failed to create temporary TTL file. Error: {e}"

    # Validate the temp file
    is_valid, validation_msg = validate_ttl_file(temp_file_path)
    if not is_valid:
        return f"Ontology validation failed: {validation_msg}"

    # Passed validation, now write to the actual output path
    try:
        with open(absolute_output_path, "w") as f:
            f.write(ttl_string)
    except Exception as e:
        return f"Failed to write ontology file to disk. Error: {e}"

    ontology_resource = Resource(
        type="file",
        relative_path=relative_output_path,
        absolute_path=absolute_output_path,
        uri=uri,
        meta_task_name=meta_task_name,
        iteration=iteration_index,
        description=description
    )
    try:
        resource_db_operator.register_resource(ontology_resource)
    except Exception as e:
        return f"Ontology file created but failed to register in resource database. Error: {e}"

    return (
        f"The ontology file has been created and registered in the resource database. "
        f"The relative path is {relative_output_path} and the absolute path is {absolute_output_path}"
    )

if __name__ == "__main__":
    ontology = OntologyInput(
        name="Building",
        description="Building ontology",
        base_uri="http://example.org/ontology",
        version="1.0",
        prefixes={
            "ex": "http://example.org/ontology#",
            "foaf": "http://xmlns.com/foaf/0.1/"
        },
        imports=["http://www.w3.org/2002/07/owl"]  # ← Try replacing with .../owl to see the difference
    )
    result = create_ontology(
        ontology=ontology,
        meta_task_name="ontocompchem",
        iteration_index=2
    )
    resources = resource_db_operator.get_resources_by_meta_task_name_and_iteration(meta_task_name="ontocompchem", iteration=2)
    for resource in resources:
        print(resource)
 
