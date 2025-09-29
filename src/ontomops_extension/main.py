from fastmcp import FastMCP
from .operations.ontomops_extension import (
    # Memory API
    init_memory as _init_memory,
    inspect_memory as _inspect_memory,
    export_memory as _export_memory,
    # Core classes
    create_chemical_building_unit as _create_chemical_building_unit,
    create_metal_organic_polyhedron as _create_metal_organic_polyhedron,
    # Property updaters
    update_mop_ccdc_number as _update_mop_ccdc_number,
    update_mop_formula as _update_mop_formula,
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
from src.utils.global_logger import get_logger, mcp_tool_logger

mcp = FastMCP(name="ontomops_extension")


@mcp.prompt(name="instruction")
def instruction_prompt():
    return (
        "OntoMOPs MCP server for building Metal-Organic Polyhedra (MOPs) knowledge graphs. "
        "This server provides tools to create and manage ChemicalBuildingUnits and MetalOrganicPolyhedra instances "
        "with their properties and relationships. All tools accept plain strings and numbers and return IRIs as strings. "
        "IRIs are minted to be readable and stable, but you can also provide existing IRIs for entity linking. "
        "The server uses a file-based object memory with locking. Begin every session by calling init_memory and persist the returned session hash in your task memory. "
        "The hash is implicit for all subsequent calls but keep it for reporting and continuity. "
        "Prefer to use paper exact wording for rdfs:label fields to preserve provenance. "
        "\n\n"
        "Recommended calling sequence to build a complete MOP knowledge graph: "
        "\n1. init_memory to open or create the persistent graph and obtain session hash. "
        "\n2. Create ChemicalBuildingUnits: "
        "\n   - create_chemical_building_unit for each building block (linkers, metals, etc.) "
        "\n   - Optionally provide an existing IRI for entity linking "
        "\n3. Create MetalOrganicPolyhedra: "
        "\n   - create_metal_organic_polyhedron for each MOP with optional CCDC number and formula "
        "\n   - Optionally provide an existing IRI for entity linking "
        "\n4. Establish relationships: "
        "\n   - add_cbu_to_mop to link ChemicalBuildingUnits to MetalOrganicPolyhedra "
        "\n5. Update properties as needed: "
        "\n   - update_mop_ccdc_number to set or update CCDC numbers "
        "\n   - update_mop_formula to set or update MOP formulas "
        "\n6. Query and explore: "
        "\n   - find_mops_by_ccdc_number to find MOPs by CCDC number "
        "\n   - find_mops_by_formula to find MOPs by formula "
        "\n   - get_mop_cbus to get all ChemicalBuildingUnits in a MOP "
        "\n7. Inspect and export: "
        "\n   - inspect_memory to see a human readable summary "
        "\n   - export_memory to persist a Turtle snapshot "
        "\n8. Clean up if necessary: "
        "\n   - remove_cbu_from_mop to remove CBU-MOP relationships "
        "\n   - delete_triple to remove a specific link or delete_entity to remove an entire node "
        "\n\n"
        "General guidance: keep labels faithful to source text, always create and then connect, "
        "use the relationship functions to establish proper connections between CBUs and MOPs, "
        "and leverage the optional IRI parameter for entity linking when you have existing identifiers."
    )


# =========================
# Memory API
# =========================
@mcp.tool(
    name="init_memory",
    description="Initialize or resume the persistent graph and return the session hash. Next: create ChemicalBuildingUnits and MetalOrganicPolyhedra instances."
)
@mcp_tool_logger
def init_memory() -> str:
    return _init_memory()


@mcp.tool(
    name="inspect_memory",
    description="Summarize all individuals, types, labels, attributes, and connections in the current memory. Next: after inspection, proceed to export_memory or continue adding entities and relationships as needed."
)
@mcp_tool_logger
def inspect_memory() -> str:
    return _inspect_memory()


@mcp.tool(
    name="export_memory",
    description="Export the entire graph to a Turtle file. Only .ttl extension is allowed. Next: keep building or finalize the workflow."
)
@mcp_tool_logger
def export_memory(file_name: str) -> str:
    return _export_memory(file_name)


# =========================
# Core Classes
# =========================
@mcp.tool(
    name="create_chemical_building_unit",
    description="Create a ChemicalBuildingUnit with a human-readable name. Optionally provide an existing IRI for entity linking. Next: add it to MetalOrganicPolyhedra using add_cbu_to_mop."
)
@mcp_tool_logger
def create_chemical_building_unit(name: str, iri: str = None) -> str:
    return _create_chemical_building_unit(name, iri)


@mcp.tool(
    name="create_metal_organic_polyhedron",
    description="Create a MetalOrganicPolyhedron with optional CCDC number and MOP formula. Optionally provide an existing IRI for entity linking. Next: add ChemicalBuildingUnits using add_cbu_to_mop."
)
@mcp_tool_logger
def create_metal_organic_polyhedron(name: str, ccdc_number: str = None, mop_formula: str = None, iri: str = None) -> str:
    return _create_metal_organic_polyhedron(name, ccdc_number, mop_formula, iri)


# =========================
# Property Updaters
# =========================
@mcp.tool(
    name="update_mop_ccdc_number",
    description="Update or set the CCDC number for a MetalOrganicPolyhedron. Next: use find_mops_by_ccdc_number to query by CCDC number."
)
@mcp_tool_logger
def update_mop_ccdc_number(mop_iri: str, ccdc_number: str) -> str:
    return _update_mop_ccdc_number(mop_iri, ccdc_number)


@mcp.tool(
    name="update_mop_formula",
    description="Update or set the MOP formula for a MetalOrganicPolyhedron. Next: use find_mops_by_formula to query by formula."
)
@mcp_tool_logger
def update_mop_formula(mop_iri: str, mop_formula: str) -> str:
    return _update_mop_formula(mop_iri, mop_formula)


# =========================
# Relationship Creators
# =========================
@mcp.tool(
    name="add_cbu_to_mop",
    description="Add a ChemicalBuildingUnit to a MetalOrganicPolyhedron. Next: use get_mop_cbus to see all CBUs in a MOP."
)
@mcp_tool_logger
def add_cbu_to_mop(mop_iri: str, cbu_iri: str) -> str:
    return _add_cbu_to_mop(mop_iri, cbu_iri)


@mcp.tool(
    name="remove_cbu_from_mop",
    description="Remove a ChemicalBuildingUnit from a MetalOrganicPolyhedron. Next: inspect_memory to verify the removal."
)
@mcp_tool_logger
def remove_cbu_from_mop(mop_iri: str, cbu_iri: str) -> str:
    return _remove_cbu_from_mop(mop_iri, cbu_iri)


# =========================
# Query Utilities
# =========================
@mcp.tool(
    name="find_mops_by_ccdc_number",
    description="Find all MetalOrganicPolyhedra with a specific CCDC number. Next: use get_mop_cbus to explore the CBUs in found MOPs."
)
@mcp_tool_logger
def find_mops_by_ccdc_number(ccdc_number: str) -> str:
    results = _find_mops_by_ccdc_number(ccdc_number)
    if not results:
        return f"No MetalOrganicPolyhedra found with CCDC number: {ccdc_number}"
    return f"Found {len(results)} MetalOrganicPolyhedra with CCDC number {ccdc_number}:\n" + "\n".join(results)


@mcp.tool(
    name="find_mops_by_formula",
    description="Find all MetalOrganicPolyhedra with a specific formula. Next: use get_mop_cbus to explore the CBUs in found MOPs."
)
@mcp_tool_logger
def find_mops_by_formula(mop_formula: str) -> str:
    results = _find_mops_by_formula(mop_formula)
    if not results:
        return f"No MetalOrganicPolyhedra found with formula: {mop_formula}"
    return f"Found {len(results)} MetalOrganicPolyhedra with formula {mop_formula}:\n" + "\n".join(results)


@mcp.tool(
    name="get_mop_cbus",
    description="Get all ChemicalBuildingUnits associated with a MetalOrganicPolyhedron. Next: use this to explore the composition of MOPs."
)
@mcp_tool_logger
def get_mop_cbus(mop_iri: str) -> str:
    results = _get_mop_cbus(mop_iri)
    if not results:
        return f"No ChemicalBuildingUnits found for MOP: {mop_iri}"
    return f"Found {len(results)} ChemicalBuildingUnits in MOP {mop_iri}:\n" + "\n".join(results)


# =========================
# Delete Utilities
# =========================
@mcp.tool(
    name="delete_entity",
    description="Delete an entity by removing all incoming and outgoing triples. Next: inspect_memory to verify cleanup and repair any broken links."
)
@mcp_tool_logger
def delete_entity(entity_iri: str) -> str:
    return _delete_entity(entity_iri)


@mcp.tool(
    name="delete_triple",
    description="Delete a specific triple given subject, predicate, and object. Set is_object_literal to true when removing a literal value. Next: inspect_memory to confirm the change."
)
@mcp_tool_logger
def delete_triple(subject_iri: str, predicate_uri: str, object_iri_or_literal: str, is_object_literal: bool = False) -> str:
    return _delete_triple(subject_iri, predicate_uri, object_iri_or_literal, is_object_literal)


if __name__ == "__main__":
    mcp.run(transport="stdio")
