from fastmcp import FastMCP
from src.ontomops_extension.operations.ontomops_extension import (
    # Memory API
    init_memory as _init_memory,
    inspect_memory as _inspect_memory,
    export_memory as _export_memory,
    # Core classes (renamed to add_*)
    add_chemical_building_unit as _add_chemical_building_unit,
    add_metal_organic_polyhedron as _add_metal_organic_polyhedron,
    # Property updaters
    update_mop_ccdc_number as _update_mop_ccdc_number,
    update_mop_formula as _update_mop_formula,
    update_entity_label as _update_entity_label,
    # Relationship creators
    add_cbu_to_mop as _add_cbu_to_mop,
    remove_cbu_from_mop as _remove_cbu_from_mop,
    # Query utilities
    find_mops_by_ccdc_number as _find_mops_by_ccdc_number,
    find_mops_by_formula as _find_mops_by_formula,
    get_mop_cbus as _get_mop_cbus,
    # Delete utilities
    delete_entity as _delete_entity,
    delete_triple as _delete_triple,
)
from src.utils.global_logger import mcp_tool_logger

mcp = FastMCP(name="ontomops_extension")

@mcp.prompt(name="instruction")
def instruction_prompt():
    return (
        "OntoMOPs MCP server for building Metal-Organic Polyhedra (MOPs) knowledge graphs. "
        "This server provides tools to create and manage ChemicalBuildingUnits and MetalOrganicPolyhedra instances "
        "with their properties and relationships. All tools accept plain strings and numbers and return IRIs as strings. "
        "IRIs are minted to be readable and stable. "
        "The server uses entity-specific memory with global state management. The agent writes global state to ontomops_global_state.json, "
        "and the MCP server reads it automatically. No need to pass hash or entity parameters to tools. "
        "Prefer to use paper exact wording for rdfs:label fields to preserve provenance. "
        "\n\n"
        "Recommended calling sequence to build a complete MOP knowledge graph: "
        "\n1. init_memory to open or create the persistent graph for the current entity. "
        "\n2. Create ChemicalBuildingUnits: "
        "\n   - create_chemical_building_unit for each building block (linkers, metals, etc.) "
        "\n3. Create MetalOrganicPolyhedra: "
        "\n   - create_metal_organic_polyhedron for each MOP with optional CCDC number and formula "
        "\n4. Establish relationships: "
        "\n   - add_cbu_to_mop to link ChemicalBuildingUnits to MetalOrganicPolyhedra "
        "\n   - Usually, one MetalOrganicPolyhedron has multiple ChemicalBuildingUnits, metal and organic "
        "\n5. Update properties as needed: "
        "\n   - update_mop_ccdc_number to set or update CCDC numbers (ensures single value) "
        "\n   - update_mop_formula to set or update MOP formulas (ensures single value) "
        "\n   - update_entity_label to set or update labels for any entity (ensures single value) "
        "\n6. Query and explore: "
        "\n   - find_mops_by_ccdc_number to find MOPs by CCDC number "
        "\n   - find_mops_by_formula to find MOPs by formula "
        "\n   - get_mop_cbus to get all ChemicalBuildingUnits in a MOP "
        "\n7. Inspect and export: "
        "\n   - inspect_memory to see a human readable summary "
        "\n   - export_memory to persist a Turtle snapshot to data/<hash>/ontomops_output/ "
        "\n8. Clean up if necessary: "
        "\n   - remove_cbu_from_mop to remove CBU-MOP relationships "
        "\n   - delete_triple to remove a specific link or delete_entity to remove an entire node "
        "\n\n"
        "General guidance: keep labels faithful to source text, always create and then connect, "
        "use the relationship functions to establish proper connections between CBUs and MOPs, "
        "and use update functions to modify existing entities rather than creating duplicates."
    )

# =========================
# Memory API
# =========================
@mcp.tool(name="init_memory", description="Initialize or resume the persistent graph. Reads from ontomops_global_state.json for hash and entity information.")
@mcp_tool_logger
def init_memory(hash_value: str = None, top_level_entity_name: str = None) -> str:
    return _init_memory(hash_value, top_level_entity_name)

@mcp.tool(name="inspect_memory", description="Return a detailed summary of all individuals, types, labels, attributes, and connections in the current memory graph.")
@mcp_tool_logger
def inspect_memory() -> str:
    return _inspect_memory()

@mcp.tool(name="export_memory", description="Serialize the entire memory graph to a Turtle file (.ttl) and return the absolute path. Saves to data/<hash>/ontomops_output/ directory with entity-specific filename.")
@mcp_tool_logger
def export_memory() -> str:
    return _export_memory()

# =========================
# Core Classes
# =========================
@mcp.tool(name="add_chemical_building_unit", description="Register an existing ChemicalBuildingUnit by full IRI (no minting) and a non-empty label. Returns the CBU IRI.")
@mcp_tool_logger
def add_chemical_building_unit(iri: str, label: str) -> str:
    return _add_chemical_building_unit(iri, label)

@mcp.tool(name="add_metal_organic_polyhedron", description="Add a MetalOrganicPolyhedron with optional CCDC number and MOP formula. Returns the IRI of the created MOP.")
@mcp_tool_logger
def add_metal_organic_polyhedron(name: str, ccdc_number: str = None, mop_formula: str = None) -> str:
    return _add_metal_organic_polyhedron(name, ccdc_number, mop_formula)

# =========================
# Property Updaters
# =========================
@mcp.tool(name="update_mop_ccdc_number", description="Update or set the CCDC number for a MetalOrganicPolyhedron. Ensures single value. Returns the MOP IRI.")
@mcp_tool_logger
def update_mop_ccdc_number(mop_iri: str, ccdc_number: str) -> str:
    return _update_mop_ccdc_number(mop_iri, ccdc_number)

@mcp.tool(name="update_mop_formula", description="Update or set the MOP formula for a MetalOrganicPolyhedron. Ensures single value. Returns the MOP IRI.")
@mcp_tool_logger
def update_mop_formula(mop_iri: str, mop_formula: str) -> str:
    return _update_mop_formula(mop_iri, mop_formula)

@mcp.tool(name="update_entity_label", description="Update or set the rdfs:label for any entity. Ensures single value. Returns the entity IRI.")
@mcp_tool_logger
def update_entity_label(entity_iri: str, label: str) -> str:
    return _update_entity_label(entity_iri, label)

# =========================
# Relationship Creators
# =========================
@mcp.tool(name="add_cbu_to_mop", description="Add a ChemicalBuildingUnit to a MetalOrganicPolyhedron. Returns the MOP IRI.")
@mcp_tool_logger
def add_cbu_to_mop(mop_iri: str, cbu_iri: str) -> str:
    return _add_cbu_to_mop(mop_iri, cbu_iri)

@mcp.tool(name="remove_cbu_from_mop", description="Remove a ChemicalBuildingUnit from a MetalOrganicPolyhedron. Returns the MOP IRI.")
@mcp_tool_logger
def remove_cbu_from_mop(mop_iri: str, cbu_iri: str) -> str:
    return _remove_cbu_from_mop(mop_iri, cbu_iri)

# =========================
# Query Utilities
# =========================
@mcp.tool(name="find_mops_by_ccdc_number", description="Find all MetalOrganicPolyhedra with a specific CCDC number. Returns a formatted list of MOP IRIs.")
@mcp_tool_logger
def find_mops_by_ccdc_number(ccdc_number: str) -> str:
    results = _find_mops_by_ccdc_number(ccdc_number)
    if not results:
        return f"No MetalOrganicPolyhedra found with CCDC number: {ccdc_number}"
    return f"Found {len(results)} MetalOrganicPolyhedra with CCDC number {ccdc_number}:\n" + "\n".join(results)

@mcp.tool(name="find_mops_by_formula", description="Find all MetalOrganicPolyhedra with a specific formula. Returns a formatted list of MOP IRIs.")
@mcp_tool_logger
def find_mops_by_formula(mop_formula: str) -> str:
    results = _find_mops_by_formula(mop_formula)
    if not results:
        return f"No MetalOrganicPolyhedra found with formula: {mop_formula}"
    return f"Found {len(results)} MetalOrganicPolyhedra with formula {mop_formula}:\n" + "\n".join(results)

@mcp.tool(name="get_mop_cbus", description="Get all ChemicalBuildingUnits associated with a MetalOrganicPolyhedron. Returns a formatted list of CBU IRIs.")
@mcp_tool_logger
def get_mop_cbus(mop_iri: str) -> str:
    results = _get_mop_cbus(mop_iri)
    if not results:
        return f"No ChemicalBuildingUnits found for MOP: {mop_iri}"
    return f"Found {len(results)} ChemicalBuildingUnits in MOP {mop_iri}:\n" + "\n".join(results)

# =========================
# Delete Utilities
# =========================
@mcp.tool(name="delete_entity", description="Remove all triples where the entity IRI is subject or object. Returns the deleted entity IRI.")
@mcp_tool_logger
def delete_entity(entity_iri: str) -> str:
    return _delete_entity(entity_iri)

@mcp.tool(name="delete_triple", description="Remove a specific triple given subject, predicate, and object. Set is_object_literal to true when removing a literal value. Returns the subject IRI.")
@mcp_tool_logger
def delete_triple(subject_iri: str, predicate_uri: str, object_iri_or_literal: str, is_object_literal: bool = False) -> str:
    return _delete_triple(subject_iri, predicate_uri, object_iri_or_literal, is_object_literal)

if __name__ == "__main__":
    mcp.run(transport="stdio")