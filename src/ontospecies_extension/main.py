from fastmcp import FastMCP
from src.ontospecies_extension.operations.ontospecies_extension import (
    # Memory API
    init_memory as _init_memory,
    inspect_memory as _inspect_memory,
    export_memory as _export_memory,
    # Core classes
    create_species as _create_species,
    create_characterization_session as _create_characterization_session,
    create_hnmr_device as _create_hnmr_device,
    create_elemental_analysis_device as _create_elemental_analysis_device,
    create_infrared_spectroscopy_device as _create_infrared_spectroscopy_device,
    create_hnmr_data as _create_hnmr_data,
    create_elemental_analysis_data as _create_elemental_analysis_data,
    create_infrared_spectroscopy_data_with_bands as _create_infrared_spectroscopy_data_with_bands,
    create_material as _create_material,
    create_molecular_formula as _create_molecular_formula,
    create_chemical_formula as _create_chemical_formula,
    create_weight_percentage as _create_weight_percentage,
    create_ccdc_number as _create_ccdc_number,
    create_element as _create_element,
    create_atomic_weight as _create_atomic_weight,
    # High-level single-call functions
    create_and_link_ccdc_number as _create_and_link_ccdc_number,
    create_and_link_ir_data as _create_and_link_ir_data,
    # Relationships
    add_characterization_session_to_species as _add_characterization_session_to_species,
    add_hnmr_device_to_characterization_session as _add_hnmr_device_to_characterization_session,
    add_elemental_analysis_device_to_characterization_session as _add_elemental_analysis_device_to_characterization_session,
    add_infrared_spectroscopy_device_to_characterization_session as _add_infrared_spectroscopy_device_to_characterization_session,
    add_hnmr_data_to_species as _add_hnmr_data_to_species,
    add_elemental_analysis_data_to_species as _add_elemental_analysis_data_to_species,
    add_molecular_formula_to_species as _add_molecular_formula_to_species,
    add_chemical_formula_to_species as _add_chemical_formula_to_species,
    add_ccdc_number_to_species as _add_ccdc_number_to_species,
    add_atomic_weight_to_element as _add_atomic_weight_to_element,
    add_element_to_weight_percentage as _add_element_to_weight_percentage,
    # Delete
    delete_entity as _delete_entity,
    delete_triple as _delete_triple,
)
from src.utils.global_logger import get_logger, mcp_tool_logger

mcp = FastMCP(name="ontospecies_extension")

@mcp.prompt(name="instruction")
def instruction_prompt():
    return (
        "OntoSpecies MCP - Build comprehensive chemical characterization knowledge graphs\n\n"

        "If any information is indeed not found in the paper, use 'N/A' as the value. It is very important to include all information in the paper, even if it is not found in the paper. "
        "Inclusion priority over accuracy. "
        
        "WORKFLOW (3 steps):\n"
        "1) init_memory - Initialize the knowledge graph\n"
        "2) create_* entities - Create all needed components\n"
        "3) add_* relationships - Connect entities together\n"
        "4) Use high-level functions for complex operations (create_and_link_*)\n"
         
        "CORE ENTITIES:\n"
        "• Species: Chemical compounds/products (e.g., 'Water', 'Methanol', 'Aspirin')\n"
        "• CharacterizationSession: Analysis sessions (e.g., 'NMR Analysis 2024-01-15')\n"
        "• Devices: Analytical instruments\n"
        "  - HNMRDevice: NMR spectrometers (e.g., 'Bruker 400 MHz', 'Varian 500 MHz')\n"
        "  - ElementalAnalysisDevice: CHN analyzers (e.g., 'PerkinElmer 2400', 'Thermo Flash 2000')\n"
        "  - InfraredSpectroscopyDevice: FT-IR spectrometers (e.g., 'Nicolet iS50', 'Bruker Alpha II')\n\n"
        
        "DATA OBJECTS:\n"
        "• HNMRData: NMR spectroscopic data (e.g., 'Compound A 1H NMR', 'Product NMR Analysis')\n"
        "• ElementalAnalysisData: Elemental composition data (e.g., 'C12H8O4 Analysis', 'CHN Results')\n"
        "• InfraredSpectroscopyData: IR spectral data (e.g., 'Product IR Spectrum', 'Compound FTIR')\n\n"
        
        "MOLECULAR PROPERTIES:\n"
        "• MolecularFormula: Simple formulas (e.g., 'C6H12O6', 'H2O', 'C8H10N4O2')\n"
        "• ChemicalFormula: Extended formulas with structure (e.g., 'C6H5OH', 'CH3COOH')\n"
        "• CCDCNumber: Cambridge Crystallographic Data Centre numbers (e.g., '123456', '987654')\n\n"
        
        "SPECTROSCOPIC COMPONENTS:\n"
        "• ChemicalShift: NMR chemical shifts (e.g., '7.2 ppm', '3.4 ppm', '1.2 ppm')\n"
        "• InfraredBand: IR absorption bands (e.g., '3400 cm-1', '1650 cm-1', '1200 cm-1')\n"
        "• Solvent: NMR solvents (e.g., 'DMSO-d6', 'CDCl3', 'D2O', 'MeOH-d4', 'Acetone-d6')\n"
        "• Material: IR sample preparation materials (e.g., 'KBr pellet', 'KBr', 'ATR crystal', 'NaCl plate')\n\n"
        
        "ELEMENTS & WEIGHTS:\n"
        "• Element: Chemical elements (e.g., 'Carbon', 'Hydrogen', 'Oxygen', 'Nitrogen')\n"
        "• AtomicWeight: Element atomic weights (e.g., '12.011', '1.008', '15.999', '14.007')\n"
        "• WeightPercentage: Elemental composition percentages (e.g., 'C 60.0%', 'H 4.5%', 'O 35.5%')\n\n"
        
        "RELATIONSHIP PATTERNS:\n"
        "1. Link sessions to species: add_characterization_session_to_species\n"
        "2. Link devices to sessions: add_*_device_to_characterization_session\n"
        "3. Link data to species: add_*_data_to_species\n"
        "4. Link formulas to species: add_*_formula_to_species\n"
        "5. Link components to data: add_*_to_*_data\n"
        "6. Link elements to weights: add_atomic_weight_to_element\n\n"
        
        "HIGH-LEVEL FUNCTIONS:\n"
        "• create_and_link_ccdc_number: Create and link CCDC number to species\n"
        "• create_and_link_ir_data: Create IR data with bands and optionally link material/device/session\n"
        "• create_infrared_spectroscopy_data_with_bands: Create IR data with bands text\n\n"
        
        "IR BANDS FORMAT:\n"
        "• Use semicolon-separated format: '498 w; 579 w; 650 m; 782 m; 867 w; 947 s'\n"
        "• Intensity markers: w=weak, m=medium, s=strong, vs=very strong\n"
        "• Include wavenumber and intensity: '3400 (s), 1650 (m), 1200 (w) cm-1'\n\n"
        
        "COMMON MATERIALS FOR IR:\n"
        "• KBr pellet: Most common for solid samples\n"
        "• KBr: Potassium bromide powder\n"
        "• ATR crystal: Attenuated Total Reflectance\n"
        "• NaCl plate: Sodium chloride windows\n"
        "• CaF2: Calcium fluoride windows\n"
        "• ZnSe: Zinc selenide windows\n\n"
        
        "COMMON NMR SOLVENTS:\n"
        "• CDCl3: Chloroform-d (most common)\n"
        "• DMSO-d6: Dimethyl sulfoxide-d6\n"
        "• D2O: Deuterated water\n"
        "• MeOH-d4: Methanol-d4\n"
        "• Acetone-d6: Acetone-d6\n"
        "• THF-d8: Tetrahydrofuran-d8\n\n"
        
        "RULES:\n"
        "• Create entities first, then connect them\n"
        "• Use descriptive names that match source data\n"
        "• Reuse IRIs if provided\n"
        "• Set literal values after creating relationships\n"
        "• Use proper chemical notation and units\n"
        "• Include multiplicity and coupling in NMR data (s, d, t, q, m)\n"
        "• Use standard IR band notation (s=strong, m=medium, w=weak)\n"
    )

# =========================
# Memory
# =========================
@mcp.tool(name="init_memory", description="Init or resume persistent graph; returns session hash_value. If hash_value is provided, use data/{hash_value}/memory for storage.")
@mcp_tool_logger
def init_memory(hash_value: str) -> str: return _init_memory(hash_value)

@mcp.tool(name="inspect_memory", description="Human-readable dump for all nodes and edges.")
@mcp_tool_logger
def inspect_memory(hash_value: str) -> str: return _inspect_memory(hash_value)

@mcp.tool(name="export_memory", description="Serialize graph to .ttl file. If hash_value is provided, save to data/{hash_value}/ directory. Output filename is hardcoded as 'ontospecies_extension.ttl'.")
@mcp_tool_logger
def export_memory(hash_value: str) -> str: return _export_memory(hash_value)

# =========================
# Create
# =========================
@mcp.tool(name="create_species", description="Create Species.")
@mcp_tool_logger
def create_species(name: str, hash_value: str, iri: str = None) -> str: return _create_species(name, hash_value, iri)

@mcp.tool(name="create_characterization_session", description="Create CharacterizationSession.")
@mcp_tool_logger
def create_characterization_session(name: str, hash_value: str, iri: str = None) -> str:
    return _create_characterization_session(name, hash_value, iri)

@mcp.tool(name="create_hnmr_device", description="Create HNMRDevice.")
@mcp_tool_logger
def create_hnmr_device(name: str, hash_value: str, iri: str = None) -> str: return _create_hnmr_device(name, hash_value, iri)

@mcp.tool(name="create_elemental_analysis_device", description="Create ElementalAnalysisDevice.")
@mcp_tool_logger
def create_elemental_analysis_device(name: str, hash_value: str, iri: str = None) -> str:
    return _create_elemental_analysis_device(name, hash_value, iri)

@mcp.tool(name="create_infrared_spectroscopy_device", description="Create InfraredSpectroscopyDevice.")
@mcp_tool_logger
def create_infrared_spectroscopy_device(name: str, hash_value: str, iri: str = None) -> str:
    return _create_infrared_spectroscopy_device(name, hash_value, iri)

@mcp.tool(name="create_hnmr_data", description="Create HNMRData.")
@mcp_tool_logger
def create_hnmr_data(name: str, hash_value: str, iri: str = None) -> str: return _create_hnmr_data(name, hash_value, iri)

@mcp.tool(name="create_elemental_analysis_data", description="Create ElementalAnalysisData.")
@mcp_tool_logger
def create_elemental_analysis_data(name: str, hash_value: str, iri: str = None) -> str:
    return _create_elemental_analysis_data(name, hash_value, iri)

@mcp.tool(name="create_infrared_spectroscopy_data_with_bands", description="Create InfraredSpectroscopyData with bands text.")
@mcp_tool_logger
def create_infrared_spectroscopy_data_with_bands(label: str, bands_text: str = None, hash_value: str = None, iri: str = None) -> str:
    return _create_infrared_spectroscopy_data_with_bands(label, bands_text, hash_value, iri)

# High-level single-call functions
@mcp.tool(name="create_and_link_ccdc_number", description="Create CCDC number and link to species in one call.")
@mcp_tool_logger
def create_and_link_ccdc_number(species_iri: str, value: str = None, hash_value: str = None) -> str:
    return _create_and_link_ccdc_number(species_iri, value, hash_value)

@mcp.tool(name="create_and_link_ir_data", description="Create IR data with bands and optionally link material/device/session.")
@mcp_tool_logger
def create_and_link_ir_data(species_iri: str, ir_data_label: str, bands_text: str = None, 
                          material_label: str = None, device_label: str = None, session_label: str = None, hash_value: str = None) -> str:
    return _create_and_link_ir_data(species_iri, ir_data_label, bands_text, hash_value, material_label, device_label, session_label)

@mcp.tool(name="create_material", description="Create Material.")
@mcp_tool_logger
def create_material(name: str, hash_value: str, iri: str = None) -> str: return _create_material(name, hash_value, iri)

@mcp.tool(name="create_molecular_formula", description="Create MolecularFormula.")
@mcp_tool_logger
def create_molecular_formula(name: str, hash_value: str, iri: str = None) -> str: return _create_molecular_formula(name, hash_value, iri)

@mcp.tool(name="create_chemical_formula", description="Create ChemicalFormula.")
@mcp_tool_logger
def create_chemical_formula(name: str, hash_value: str, iri: str = None) -> str: return _create_chemical_formula(name, hash_value, iri)

@mcp.tool(name="create_weight_percentage", description="Create WeightPercentage.")
@mcp_tool_logger
def create_weight_percentage(name: str, hash_value: str, iri: str = None) -> str: return _create_weight_percentage(name, hash_value, iri)

@mcp.tool(name="create_ccdc_number", description="Create CCDCNumber.")
@mcp_tool_logger
def create_ccdc_number(name: str, hash_value: str, iri: str = None) -> str: return _create_ccdc_number(name, hash_value, iri)

@mcp.tool(name="create_element", description="Create Element.")
@mcp_tool_logger
def create_element(name: str, hash_value: str, iri: str = None) -> str: return _create_element(name, hash_value, iri)

@mcp.tool(name="create_atomic_weight", description="Create AtomicWeight.")
@mcp_tool_logger
def create_atomic_weight(value: str, hash_value: str, iri: str = None) -> str: return _create_atomic_weight(value, hash_value, iri)

# =========================
# Link
# =========================
@mcp.tool(name="add_characterization_session_to_species", description="Link session to species.")
@mcp_tool_logger
def add_characterization_session_to_species(species_iri: str, characterization_session_iri: str, hash_value: str) -> str:
    return _add_characterization_session_to_species(species_iri, characterization_session_iri, hash_value)


@mcp.tool(name="add_hnmr_device_to_characterization_session", description="Link HNMRDevice.")
@mcp_tool_logger
def add_hnmr_device_to_characterization_session(characterization_session_iri: str, hnmr_device_iri: str, hash_value: str) -> str:
    return _add_hnmr_device_to_characterization_session(characterization_session_iri, hnmr_device_iri, hash_value)

@mcp.tool(name="add_elemental_analysis_device_to_characterization_session", description="Link EA device.")
@mcp_tool_logger
def add_elemental_analysis_device_to_characterization_session(characterization_session_iri: str, elemental_device_iri: str, hash_value: str) -> str:
    return _add_elemental_analysis_device_to_characterization_session(characterization_session_iri, elemental_device_iri, hash_value)

@mcp.tool(name="add_infrared_spectroscopy_device_to_characterization_session", description="Link IR device.")
@mcp_tool_logger
def add_infrared_spectroscopy_device_to_characterization_session(characterization_session_iri: str, infrared_device_iri: str, hash_value: str) -> str:
    return _add_infrared_spectroscopy_device_to_characterization_session(characterization_session_iri, infrared_device_iri, hash_value)

@mcp.tool(name="add_hnmr_data_to_species", description="Link HNMRData to species.")
@mcp_tool_logger
def add_hnmr_data_to_species(species_iri: str, hnmr_data_iri: str, hash_value: str) -> str:
    return _add_hnmr_data_to_species(species_iri, hnmr_data_iri, hash_value)

@mcp.tool(name="add_elemental_analysis_data_to_species", description="Link EA data to species.")
@mcp_tool_logger
def add_elemental_analysis_data_to_species(species_iri: str, elemental_data_iri: str, hash_value: str) -> str:
    return _add_elemental_analysis_data_to_species(species_iri, elemental_data_iri, hash_value)


@mcp.tool(name="add_molecular_formula_to_species", description="Link MolecularFormula.")
@mcp_tool_logger
def add_molecular_formula_to_species(species_iri: str, molecular_formula_iri: str, hash_value: str) -> str:
    return _add_molecular_formula_to_species(species_iri, molecular_formula_iri, hash_value)

@mcp.tool(name="add_chemical_formula_to_species", description="Link ChemicalFormula.")
@mcp_tool_logger
def add_chemical_formula_to_species(species_iri: str, chemical_formula_iri: str, hash_value: str) -> str:
    return _add_chemical_formula_to_species(species_iri, chemical_formula_iri, hash_value)

@mcp.tool(name="add_ccdc_number_to_species", description="Link CCDCNumber.")
@mcp_tool_logger
def add_ccdc_number_to_species(species_iri: str, ccdc_number_iri: str, hash_value: str) -> str:
    return _add_ccdc_number_to_species(species_iri, ccdc_number_iri, hash_value)


@mcp.tool(name="add_atomic_weight_to_element", description="Link AtomicWeight to Element.")
@mcp_tool_logger
def add_atomic_weight_to_element(element_iri: str, atomic_weight_iri: str, hash_value: str) -> str:
    return _add_atomic_weight_to_element(element_iri, atomic_weight_iri, hash_value)

@mcp.tool(name="add_element_to_weight_percentage", description="Link Element to WeightPercentage.")
@mcp_tool_logger
def add_element_to_weight_percentage(weight_percentage_iri: str, element_iri: str, hash_value: str) -> str:
    return _add_element_to_weight_percentage(weight_percentage_iri, element_iri, hash_value)

# =========================
# Delete
# =========================
@mcp.tool(name="delete_entity", description="Remove all triples touching an IRI.")
@mcp_tool_logger
def delete_entity(entity_iri: str, hash_value: str) -> str: return _delete_entity(entity_iri, hash_value)

@mcp.tool(name="delete_triple", description="Remove one triple; set is_object_literal if needed.")
@mcp_tool_logger
def delete_triple(subject_iri: str, predicate_uri: str, object_iri_or_literal: str, hash_value: str, is_object_literal: bool = False) -> str:
    return _delete_triple(subject_iri, predicate_uri, object_iri_or_literal, hash_value, is_object_literal)

if __name__ == "__main__":
    mcp.run(transport="stdio")
