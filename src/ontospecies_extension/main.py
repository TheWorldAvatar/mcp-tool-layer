# main.py — MCP server wiring; accepts labels or IRIs; "N/A" is a sentinel for unknowns
from fastmcp import FastMCP
from urllib.parse import urlparse
from src.utils.global_logger import mcp_tool_logger
from src.ontospecies_extension.operations.ontospecies_extension import (
    # memory
    init_memory as _init_memory,
    inspect_memory as _inspect_memory,
    export_memory as _export_memory,
    # top-level creators
    create_species as _create_species,
    create_element as _create_element,
    # child creation + linking (relations)
    add_characterization_session_to_species as _add_session_to_species,
    add_hnmr_device_to_characterization_session as _add_hnmr_dev_to_session,
    add_elemental_analysis_device_to_characterization_session as _add_elem_dev_to_session,
    add_infrared_spectroscopy_device_to_characterization_session as _add_ir_dev_to_session,
    add_hnmr_data_to_species as _add_hnmr_data_to_species,
    add_elemental_analysis_data_to_species as _add_elem_data_to_species,
    add_infrared_spectroscopy_data_to_species as _add_ir_data_to_species,
    add_material_to_infrared_spectroscopy_data as _add_material_to_ir,
    add_molecular_formula_to_species as _add_mf_to_species,
    add_chemical_formula_to_species as _add_cf_to_species,
    add_ccdc_number_to_species as _add_ccdc_to_species,
    add_atomic_weight_to_element as _add_aw_to_element,
    # deletes
    delete_entity as _delete_entity,
    delete_triple as _delete_triple,
)

mcp = FastMCP(name="ontospecies_extension")

# ---------- helpers ----------
def _is_abs_iri(s: str) -> bool:
    if not s or not isinstance(s, str):
        return False
    u = urlparse(s)
    return bool(u.scheme) and bool(u.netloc)

def _require_abs_iri(param_name: str, value: str) -> None:
    """
    Enforce absolute HTTPS IRIs for graph parents or existing nodes.
    Accepts 'N/A' as an explicit unknown sentinel that will be stored as a literal when applicable.
    """
    if value == "N/A":
        return
    if not _is_abs_iri(value):
        raise ValueError(f"{param_name} must be an absolute IRI (https://...). Got: {value!r}")

# ---------- system prompt ----------
@mcp.prompt(name="instruction")
def instruction_prompt():
    """
    High-level usage banner returned to MCP runtimes.
    """
    return (
        "OntoSpecies MCP (simplified ops)\n"
        "Rules:\n"
        "**Critical**: Always create the species first, before adding any other information. "
        "- ** Top priority: don't derive any new information yourself, only use the information provided in the paper."
        "- Parent arguments must be absolute IRIs (https). Use 'N/A' when a parent value is unknown.\n"
        "- Child nodes are created from provided labels; IRIs are minted internally.\n"
        "- Do not emit file:// IRIs.\n"
        "Workflow: init_memory → create_species/element → add_* relations → export_memory\n"
        "Notes:\n"
        "- 'N/A' is treated as an unknown sentinel for values (e.g., CCDC) and may be persisted as a literal.\n"
        "- Labels are human-readable; IRIs identify individuals already in the graph.\n"
        "- Every function must be called in the workflow, even if the information is not provided in the paper, you will need to call the function."
    )

# ===== Memory =====
@mcp.tool(
    name="init_memory",
    description=(
        "Initialize or resume a persisted RDF graph for the current session. "
        "Call this once before creating or linking entities. "
        "Args: doi (optional string), top_level_entity_name (optional string label). "
        "Returns a status string with the active store location or resume info."
    ),
)
@mcp_tool_logger
def init_memory() -> str:
    return _init_memory()

@mcp.tool(
    name="inspect_memory",
    description=(
        "Summarize the current graph: counts of individuals, rdf:types, rdfs:labels, "
        "literals, and links. Returns a human-readable report."
    ),
)
@mcp_tool_logger
def inspect_memory() -> str:
    return _inspect_memory()

@mcp.tool(
    name="export_memory",
    description=(
        "Serialize the current graph to Turtle (.ttl) and return the filesystem path. "
        "Use after a sequence of create/link operations to persist results."
    ),
)
@mcp_tool_logger
def export_memory() -> str:
    return _export_memory()

# ===== Creators =====
@mcp.tool(
    name="create_species",
    description=(
        "Create a new OntoSpecies individual from a human-readable label. "
        "Argument: name (string label, not an IRI). "
        "Returns the minted absolute IRI for the new species."
    ),
)
@mcp_tool_logger
def create_species(name: str) -> str:
    return _create_species(name)

@mcp.tool(
    name="create_element",
    description=(
        "Create a new Element individual from a label (e.g., 'Carbon'). "
        "Argument: name (string label). Returns the minted absolute IRI."
    ),
)
@mcp_tool_logger
def create_element(name: str) -> str:
    return _create_element(name)

# ===== Relations / Child creation =====
@mcp.tool(
    name="add_characterization_session_to_species",
    description=(
        "Create and link a CharacterizationSession under a Species. "
        "Args: species_iri (absolute https IRI), session_label (string). "
        "Returns the new session IRI."
    ),
)
@mcp_tool_logger
def add_characterization_session_to_species(species_iri: str, session_label: str) -> str:
    _require_abs_iri("species_iri", species_iri)
    return _add_session_to_species(species_iri, session_label)

@mcp.tool(
    name="add_hnmr_device_to_characterization_session",
    description=(
        "Create and link an HNMR Device to a CharacterizationSession. "
        "Args: session_iri (absolute https IRI), device_label (string), frequency (optional string). "
        "Returns the new device IRI."
    ),
)
@mcp_tool_logger
def add_hnmr_device_to_characterization_session(
    session_iri: str,
    device_label: str,
    frequency: str | None = None,
) -> str:
    _require_abs_iri("session_iri", session_iri)
    return _add_hnmr_dev_to_session(session_iri, device_label, frequency)

@mcp.tool(
    name="add_elemental_analysis_device_to_characterization_session",
    description=(
        "Create and link an Elemental Analysis Device to a CharacterizationSession. "
        "Args: session_iri (absolute https IRI), device_label (string). "
        "Returns the new device IRI."
    ),
)
@mcp_tool_logger
def add_elemental_analysis_device_to_characterization_session(session_iri: str, device_label: str) -> str:
    _require_abs_iri("session_iri", session_iri)
    return _add_elem_dev_to_session(session_iri, device_label)

@mcp.tool(
    name="add_infrared_spectroscopy_device_to_characterization_session",
    description=(
        "Create and link an Infrared Spectroscopy Device to a CharacterizationSession. "
        "Args: session_iri (absolute https IRI), device_label (string). "
        "Returns the new device IRI."
    ),
)
@mcp_tool_logger
def add_infrared_spectroscopy_device_to_characterization_session(session_iri: str, device_label: str) -> str:
    _require_abs_iri("session_iri", session_iri)
    return _add_ir_dev_to_session(session_iri, device_label)

@mcp.tool(
    name="add_hnmr_data_to_species",
    description=(
        "Create and link an HNMR Data node under a Species and attach literals when provided. "
        "Args: species_iri (absolute https IRI), data_label (string), "
        "shifts_text (optional string), solvent (optional string), temperature (optional string). "
        "Returns the new data IRI."
    ),
)
@mcp_tool_logger
def add_hnmr_data_to_species(
    species_iri: str,
    data_label: str,
    shifts_text: str | None = None,
    solvent: str | None = None,
    temperature: str | None = None,
) -> str:
    _require_abs_iri("species_iri", species_iri)
    # Underlying signature: (species_iri, data_label, shifts, temperature, solvent_name)
    return _add_hnmr_data_to_species(species_iri, data_label, shifts_text, temperature, solvent)

@mcp.tool(
    name="add_elemental_analysis_data_to_species",
    description=(
        "Create and link an Elemental Analysis Data node under a Species and attach literals when provided. "
        "Args: species_iri (absolute https IRI), data_label (string), "
        "weight_percentage_calculated (optional string), "
        "weight_percentage_experimental (optional string), "
        "chemical_formula (optional string). You should always use empirical (elemental) chemical formula as stated in the paper. "
        "This chemical formula meaning the formula used for elemental analysis, not other formulae. "
        "Only include the elemental formula directly stated used in the Elemental Analysis, if not, leave it as 'N/A'."
        "In other words, if the Elemental Analysis data is not provided, then the chemicalFormula should be 'N/A'."
        "**Critical**: Sometimes the formula is provided in the text, but not directly used in the Elemental Analysis, you must carefully judge and decide whether it is used in the Elemental Analysis. If there is no direct evidence, this case, leave it as 'N/A'."
        "Returns the new data IRI."
    ),
)
@mcp_tool_logger
def add_elemental_analysis_data_to_species(
    species_iri: str,
    data_label: str,
    weight_percentage_calculated: str | None = None,
    weight_percentage_experimental: str | None = None,
    chemical_formula: str | None = None,
) -> str:
    _require_abs_iri("species_iri", species_iri)
    return _add_elem_data_to_species(
        species_iri,
        data_label,
        weight_percentage_calculated,
        weight_percentage_experimental,
        chemical_formula,
    )

@mcp.tool(
    name="add_infrared_spectroscopy_data_to_species",
    description=(
        "Create and link an Infrared Spectroscopy Data node under a Species. "
        "Optionally attach free-text IR bands and a material label. "
        "Args: species_iri (absolute https IRI), data_label (string), bands_text (optional string), material_label (optional string). "
        "Returns the new data IRI."
    ),
)
@mcp_tool_logger
def add_infrared_spectroscopy_data_to_species(
    species_iri: str,
    data_label: str,
    bands_text: str | None = None,
    material_label: str | None = None,
) -> str:
    _require_abs_iri("species_iri", species_iri)
    return _add_ir_data_to_species(species_iri, data_label, bands_text, material_label)

@mcp.tool(
    name="add_material_to_infrared_spectroscopy_data",
    description=(
        "Create and link a Material node to an Infrared Spectroscopy Data node. "
        "Args: ir_data_iri (absolute https IRI), material_label (string). "
        "Returns the new material IRI."
    ),
)
@mcp_tool_logger
def add_material_to_infrared_spectroscopy_data(ir_data_iri: str, material_label: str) -> str:
    _require_abs_iri("ir_data_iri", ir_data_iri)
    return _add_material_to_ir(ir_data_iri, material_label)


@mcp.tool(
    name="add_ccdc_number_to_species",
    description=(
        "Attach a CCDC number literal to a Species. "
        "Args: species_iri (absolute https IRI), ccdc_value (string or 'N/A'). "
        "Returns a confirmation string."
    ),
)
@mcp_tool_logger
def add_ccdc_number_to_species(species_iri: str, ccdc_value: str = None) -> str:
    _require_abs_iri("species_iri", species_iri)
    return _add_ccdc_to_species(species_iri, ccdc_value)

@mcp.tool(
    name="add_atomic_weight_to_element",
    description=(
        "Attach an atomic weight literal to an Element. "
        "Args: element_iri (absolute https IRI), value_label (string or numeric literal). "
        "Returns a confirmation string."
    ),
)
@mcp_tool_logger
def add_atomic_weight_to_element(element_iri: str, value_label: str) -> str:
    _require_abs_iri("element_iri", element_iri)
    return _add_aw_to_element(element_iri, value_label)

# ===== Deletes =====
@mcp.tool(
    name="delete_triple",
    description=(
        "Delete a specific triple from the graph. "
        "Args: subject_iri (absolute https IRI), predicate_uri (absolute IRI), object_iri_or_literal (IRI or literal). "
        "The object is treated as a literal when it is not an absolute IRI."
    ),
)
@mcp_tool_logger
def delete_triple(
    subject_iri: str,
    predicate_uri: str,
    object_iri_or_literal: str,
) -> str:
    if not _is_abs_iri(predicate_uri):
        raise ValueError(f"predicate_uri must be an absolute IRI. Got: {predicate_uri!r}")
    _require_abs_iri("subject_iri", subject_iri)
    # Only enforce absolute IRI for object when it's not a literal
    if _is_abs_iri(object_iri_or_literal):
        _require_abs_iri("object_iri_or_literal", object_iri_or_literal)
    return _delete_triple(subject_iri, predicate_uri, object_iri_or_literal)

@mcp.tool(
    name="delete_entity",
    description=(
        "Delete an entity and its outgoing triples. "
        "Arg: entity_iri (absolute https IRI). "
        "Use with care. Returns a confirmation string."
    ),
)
@mcp_tool_logger
def delete_entity(entity_iri: str) -> str:
    _require_abs_iri("entity_iri", entity_iri)
    return _delete_entity(entity_iri)

if __name__ == "__main__":
    mcp.run()
