"""
MCP Server for managing synthesis steps in MOPs (Metal-Organic Polyhedra).

This server provides tools for:
- Initializing synthesis step objects
- Adding various types of synthesis steps (Add, HeatChill, Filter, etc.)
- Retrieving and managing step data
- Outputting final JSON files

The server handles the complex step schema generation and management
based on the Step.py models.
"""

import os
import json
import tempfile
from typing import Dict, Any, List, Optional
from models.locations import SANDBOX_TASK_DIR

# Cross-platform file locking
try:
    import fcntl
    HAVE_FCNTL = True
except Exception:
    HAVE_FCNTL = False
    import msvcrt
from fastmcp import FastMCP
from src.utils.global_logger import get_logger, mcp_tool_logger

# Add the project root to the path for imports
import sys
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, project_root)

from models.Step import (
    Synthesis, SynthesisProduct, ChemicalInfo, AddStep, HeatChillStep, 
    FilterStep, StirStep, CrystallizationStep, DryStep, EvaporateStep,
    DissolveStep, SeparateStep, TransferStep, SonicateStep, step_schema
)

# Initialize FastMCP server
log = get_logger(__name__)
mcp = FastMCP(name="mops_step")

# Global storage for synthesis objects
synthesis_objects: Dict[str, Synthesis] = {}

# ----------------------------
# Helpers: paths, locking, IO
# ----------------------------
def get_step_file_path(task_name: str) -> str:
    return os.path.join(SANDBOX_TASK_DIR, task_name, f"{task_name}_step.json")

def atomic_write_json(path: str, data: dict) -> None:
    """
    Write JSON atomically to avoid torn writes.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(data, out, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass

def get_synthesis_object(task_name: str) -> Synthesis:
    """Get or create a synthesis object for the given task."""
    if task_name not in synthesis_objects:
        synthesis_objects[task_name] = Synthesis()
    return synthesis_objects[task_name]

@mcp.tool(name="init_synthesis_object", description="Initialize a new synthesis object for the given task.", tags=["synthesis_init"])
@mcp_tool_logger
def init_synthesis_object_tool(task_name: str) -> str:
    """Initialize a new synthesis object for the given task."""
    try:
        synthesis = get_synthesis_object(task_name)
        synthesis.Synthesis = []  # Clear any existing data
        
        # Create task directory if it doesn't exist
        task_dir = os.path.join(SANDBOX_TASK_DIR, task_name)
        os.makedirs(task_dir, exist_ok=True)
        
        return f"✅ Synthesis object initialized for task: {task_name}"
    except Exception as e:
        return f"❌ Error initializing synthesis object: {str(e)}"

@mcp.tool(name="add_synthesis_product", description="Add a new synthesis object for the given task.", tags=["synthesis_add"])
@mcp_tool_logger
def add_synthesis_product_tool(task_name: str, product_names: List[str], product_ccdc_number: str) -> str:
    """Add a new synthesis product to the synthesis object."""
    try:
        synthesis = get_synthesis_object(task_name)
        
        # Create a new synthesis product
        product = SynthesisProduct(
            productNames=product_names,
            productCCDCNumber=product_ccdc_number,
            steps=[]
        )
        
        synthesis.add_product(product)
        
        return f"✅ Added synthesis product: {', '.join(product_names)} (CCDC: {product_ccdc_number})"
    except Exception as e:
        return f"❌ Error adding synthesis product: {str(e)}"

@mcp.tool(name="add_add_step", description="Add an Add step to the most recent synthesis product.", tags=["step_add"])
@mcp_tool_logger
def add_add_step_tool(
    task_name: str, used_vessel_name: str, used_vessel_type: str,
    chemical_names: List[List[str]], chemical_amounts: List[str], step_number: int,
    atmosphere: str, duration: str, stir: bool, target_ph: Optional[float],
    is_layered: bool, comment: str
) -> str:
    """Add an Add step to the most recent synthesis product."""
    try:
        synthesis = get_synthesis_object(task_name)
        
        if not synthesis.Synthesis:
            return "❌ No synthesis products found. Add a product first."
        
        # Get the most recent product
        product = synthesis.Synthesis[-1]
        
        # Create chemical info objects
        added_chemicals = []
        for names, amount in zip(chemical_names, chemical_amounts):
            chemical = ChemicalInfo(
                chemicalName=names,
                chemicalAmount=amount
            )
            added_chemicals.append(chemical)
        
        # Create the Add step
        add_step = AddStep(
            usedVesselName=used_vessel_name,
            usedVesselType=used_vessel_type,
            addedChemical=added_chemicals,
            stepNumber=step_number,
            atmosphere=atmosphere,
            duration=duration,
            stir=stir,
            targetPH=target_ph,
            isLayered=is_layered,
            comment=comment
        )
        
        # Add the step to the product
        product.steps.append({"Add": add_step.to_dict()})
        
        return f"✅ Added Add step {step_number} to product: {', '.join(product.productNames)}"
    except Exception as e:
        return f"❌ Error adding Add step: {str(e)}"

@mcp.tool(name="add_heat_chill_step", description="Add a HeatChill step to the most recent synthesis product.", tags=["step_add"])
@mcp_tool_logger
def add_heat_chill_step_tool(
    task_name: str, duration: str, used_device: str, target_temperature: str,
    heating_cooling_rate: str, under_vacuum: bool, used_vessel_name: str,
    used_vessel_type: str, sealed_vessel: bool, step_number: int, comment: str,
    atmosphere: str, stir: bool
) -> str:
    """Add a HeatChill step to the most recent synthesis product."""
    try:
        synthesis = get_synthesis_object(task_name)
        
        if not synthesis.Synthesis:
            return "❌ No synthesis products found. Add a product first."
        
        # Get the most recent product
        product = synthesis.Synthesis[-1]
        
        # Create the HeatChill step
        heat_chill_step = HeatChillStep(
            duration=duration,
            usedDevice=used_device,
            targetTemperature=target_temperature,
            heatingCoolingRate=heating_cooling_rate,
            underVacuum=under_vacuum,
            usedVesselName=used_vessel_name,
            usedVesselType=used_vessel_type,
            sealedVessel=sealed_vessel,
            stepNumber=step_number,
            comment=comment,
            atmosphere=atmosphere,
            stir=stir
        )
        
        # Add the step to the product
        product.steps.append({"HeatChill": heat_chill_step.to_dict()})
        
        return f"✅ Added HeatChill step {step_number} to product: {', '.join(product.productNames)}"
    except Exception as e:
        return f"❌ Error adding HeatChill step: {str(e)}"

@mcp.tool(name="add_filter_step", description="Add a Filter step to the most recent synthesis product.", tags=["step_add"])
@mcp_tool_logger
def add_filter_step_tool(
    task_name: str, washing_solvent_names: List[List[str]], 
    washing_solvent_amounts: List[str], vacuum_filtration: bool,
    number_of_filtrations: int, used_vessel_name: str, used_vessel_type: str,
    step_number: int, comment: str, atmosphere: str
) -> str:
    """Add a Filter step to the most recent synthesis product."""
    try:
        synthesis = get_synthesis_object(task_name)
        
        if not synthesis.Synthesis:
            return "❌ No synthesis products found. Add a product first."
        
        # Get the most recent product
        product = synthesis.Synthesis[-1]
        
        # Create washing solvent chemical info objects
        washing_solvents = []
        for names, amount in zip(washing_solvent_names, washing_solvent_amounts):
            solvent = ChemicalInfo(
                chemicalName=names,
                chemicalAmount=amount
            )
            washing_solvents.append(solvent)
        
        # Create the Filter step
        filter_step = FilterStep(
            washingSolvent=washing_solvents,
            vacuumFiltration=vacuum_filtration,
            numberOfFiltrations=number_of_filtrations,
            usedVesselName=used_vessel_name,
            usedVesselType=used_vessel_type,
            stepNumber=step_number,
            comment=comment,
            atmosphere=atmosphere
        )
        
        # Add the step to the product
        product.steps.append({"Filter": filter_step.to_dict()})
        
        return f"✅ Added Filter step {step_number} to product: {', '.join(product.productNames)}"
    except Exception as e:
        return f"❌ Error adding Filter step: {str(e)}"

@mcp.tool(name="add_stir_step", description="Add a Stir step to the most recent synthesis product.", tags=["step_add"])
@mcp_tool_logger
def add_stir_step_tool(
    task_name: str, duration: str, used_vessel_name: str, used_vessel_type: str,
    step_number: int, atmosphere: str, temperature: str, wait: bool
) -> str:
    """Add a Stir step to the most recent synthesis product."""
    try:
        synthesis = get_synthesis_object(task_name)
        
        if not synthesis.Synthesis:
            return "❌ No synthesis products found. Add a product first."
        
        # Get the most recent product
        product = synthesis.Synthesis[-1]
        
        # Create the Stir step
        stir_step = StirStep(
            duration=duration,
            usedVesselName=used_vessel_name,
            usedVesselType=used_vessel_type,
            stepNumber=step_number,
            atmosphere=atmosphere,
            temperature=temperature,
            wait=wait
        )
        
        # Add the step to the product
        product.steps.append({"Stir": stir_step.to_dict()})
        
        return f"✅ Added Stir step {step_number} to product: {', '.join(product.productNames)}"
    except Exception as e:
        return f"❌ Error adding Stir step: {str(e)}"

@mcp.tool(name="add_crystallization_step", description="Add a Crystallization step to the most recent synthesis product.", tags=["step_add"])
@mcp_tool_logger
def add_crystallization_step_tool(
    task_name: str, used_vessel_name: str, used_vessel_type: str,
    target_temperature: str, step_number: int, duration: str, atmosphere: str, comment: str
) -> str:
    """Add a Crystallization step to the most recent synthesis product."""
    try:
        synthesis = get_synthesis_object(task_name)
        
        if not synthesis.Synthesis:
            return "❌ No synthesis products found. Add a product first."
        
        # Get the most recent product
        product = synthesis.Synthesis[-1]
        
        # Create the Crystallization step
        crystallization_step = CrystallizationStep(
            usedVesselName=used_vessel_name,
            usedVesselType=used_vessel_type,
            targetTemperature=target_temperature,
            stepNumber=step_number,
            duration=duration,
            atmosphere=atmosphere,
            comment=comment
        )
        
        # Add the step to the product
        product.steps.append({"Crystallization": crystallization_step.to_dict()})
        
        return f"✅ Added Crystallization step {step_number} to product: {', '.join(product.productNames)}"
    except Exception as e:
        return f"❌ Error adding Crystallization step: {str(e)}"

@mcp.tool(name="add_dry_step", description="Add a Dry step to the most recent synthesis product.", tags=["step_add"])
@mcp_tool_logger
def add_dry_step_tool(
    task_name: str, duration: str, used_vessel_name: str, used_vessel_type: str,
    pressure: str, temperature: str, step_number: int, atmosphere: str,
    drying_agent_names: List[List[str]], comment: str
) -> str:
    """Add a Dry step to the most recent synthesis product."""
    try:
        synthesis = get_synthesis_object(task_name)
        
        if not synthesis.Synthesis:
            return "❌ No synthesis products found. Add a product first."
        
        # Get the most recent product
        product = synthesis.Synthesis[-1]
        
        # Create drying agent chemical info objects
        drying_agents = []
        for names in drying_agent_names:
            agent = ChemicalInfo(
                chemicalName=names,
                chemicalAmount="N/A"  # Drying agents typically don't have amounts
            )
            drying_agents.append(agent)
        
        # Create the Dry step
        dry_step = DryStep(
            duration=duration,
            usedVesselName=used_vessel_name,
            usedVesselType=used_vessel_type,
            pressure=pressure,
            temperature=temperature,
            stepNumber=step_number,
            atmosphere=atmosphere,
            dryingAgent=drying_agents,
            comment=comment
        )
        
        # Add the step to the product
        product.steps.append({"Dry": dry_step.to_dict()})
        
        return f"✅ Added Dry step {step_number} to product: {', '.join(product.productNames)}"
    except Exception as e:
        return f"❌ Error adding Dry step: {str(e)}"

@mcp.tool(name="add_evaporate_step", description="Add an Evaporate step to the most recent synthesis product.", tags=["step_add"])
@mcp_tool_logger
def add_evaporate_step_tool(
    task_name: str, duration: str, used_vessel_name: str, used_vessel_type: str,
    pressure: str, temperature: str, step_number: int, rotary_evaporator: bool,
    atmosphere: str, removed_species_names: List[List[str]], target_volume: str, comment: str
) -> str:
    """Add an Evaporate step to the most recent synthesis product."""
    try:
        synthesis = get_synthesis_object(task_name)
        
        if not synthesis.Synthesis:
            return "❌ No synthesis products found. Add a product first."
        
        # Get the most recent product
        product = synthesis.Synthesis[-1]
        
        # Create removed species chemical info objects
        removed_species = []
        for names in removed_species_names:
            species = ChemicalInfo(
                chemicalName=names,
                chemicalAmount="N/A"  # Removed species typically don't have amounts
            )
            removed_species.append(species)
        
        # Create the Evaporate step
        evaporate_step = EvaporateStep(
            duration=duration,
            usedVesselName=used_vessel_name,
            usedVesselType=used_vessel_type,
            pressure=pressure,
            temperature=temperature,
            stepNumber=step_number,
            rotaryEvaporator=rotary_evaporator,
            atmosphere=atmosphere,
            removedSpecies=removed_species,
            targetVolume=target_volume,
            comment=comment
        )
        
        # Add the step to the product
        product.steps.append({"Evaporate": evaporate_step.to_dict()})
        
        return f"✅ Added Evaporate step {step_number} to product: {', '.join(product.productNames)}"
    except Exception as e:
        return f"❌ Error adding Evaporate step: {str(e)}"

@mcp.tool(name="add_dissolve_step", description="Add a Dissolve step to the most recent synthesis product.", tags=["step_add"])
@mcp_tool_logger
def add_dissolve_step_tool(
    task_name: str, duration: str, used_vessel_name: str, used_vessel_type: str,
    solvent_names: List[List[str]], solvent_amounts: List[str], step_number: int,
    atmosphere: str, comment: str
) -> str:
    """Add a Dissolve step to the most recent synthesis product."""
    try:
        synthesis = get_synthesis_object(task_name)
        
        if not synthesis.Synthesis:
            return "❌ No synthesis products found. Add a product first."
        
        # Get the most recent product
        product = synthesis.Synthesis[-1]
        
        # Create solvent chemical info objects
        solvents = []
        for names, amount in zip(solvent_names, solvent_amounts):
            solvent = ChemicalInfo(
                chemicalName=names,
                chemicalAmount=amount
            )
            solvents.append(solvent)
        
        # Create the Dissolve step
        dissolve_step = DissolveStep(
            duration=duration,
            usedVesselName=used_vessel_name,
            usedVesselType=used_vessel_type,
            solvent=solvents,
            stepNumber=step_number,
            atmosphere=atmosphere,
            comment=comment
        )
        
        # Add the step to the product
        product.steps.append({"Dissolve": dissolve_step.to_dict()})
        
        return f"✅ Added Dissolve step {step_number} to product: {', '.join(product.productNames)}"
    except Exception as e:
        return f"❌ Error adding Dissolve step: {str(e)}"

@mcp.tool(name="add_separate_step", description="Add a Separate step to the most recent synthesis product.", tags=["step_add"])
@mcp_tool_logger
def add_separate_step_tool(
    task_name: str, duration: str, used_vessel_name: str, used_vessel_type: str,
    solvent_names: List[List[str]], solvent_amounts: List[str], step_number: int,
    separation_type: str, atmosphere: str, comment: str
) -> str:
    """Add a Separate step to the most recent synthesis product."""
    try:
        synthesis = get_synthesis_object(task_name)
        
        if not synthesis.Synthesis:
            return "❌ No synthesis products found. Add a product first."
        
        # Get the most recent product
        product = synthesis.Synthesis[-1]
        
        # Create solvent chemical info objects
        solvents = []
        for names, amount in zip(solvent_names, solvent_amounts):
            solvent = ChemicalInfo(
                chemicalName=names,
                chemicalAmount=amount
            )
            solvents.append(solvent)
        
        # Create the Separate step
        separate_step = SeparateStep(
            duration=duration,
            usedVesselName=used_vessel_name,
            usedVesselType=used_vessel_type,
            solvent=solvents,
            stepNumber=step_number,
            separationType=separation_type,
            atmosphere=atmosphere,
            comment=comment
        )
        
        # Add the step to the product
        product.steps.append({"Separate": separate_step.to_dict()})
        
        return f"✅ Added Separate step {step_number} to product: {', '.join(product.productNames)}"
    except Exception as e:
        return f"❌ Error adding Separate step: {str(e)}"

@mcp.tool(name="add_transfer_step", description="Add a Transfer step to the most recent synthesis product.", tags=["step_add"])
@mcp_tool_logger
def add_transfer_step_tool(
    task_name: str, duration: str, used_vessel_name: str, used_vessel_type: str,
    target_vessel_name: str, target_vessel_type: str, step_number: int,
    is_layered: bool, transferred_amount: str, comment: str, atmosphere: str
) -> str:
    """Add a Transfer step to the most recent synthesis product."""
    try:
        synthesis = get_synthesis_object(task_name)
        
        if not synthesis.Synthesis:
            return "❌ No synthesis products found. Add a product first."
        
        # Get the most recent product
        product = synthesis.Synthesis[-1]
        
        # Create the Transfer step
        transfer_step = TransferStep(
            duration=duration,
            usedVesselName=used_vessel_name,
            usedVesselType=used_vessel_type,
            targetVesselName=target_vessel_name,
            targetVesselType=target_vessel_type,
            stepNumber=step_number,
            isLayered=is_layered,
            transferedAmount=transferred_amount,
            comment=comment,
            atmosphere=atmosphere
        )
        
        # Add the step to the product
        product.steps.append({"Transfer": transfer_step.to_dict()})
        
        return f"✅ Added Transfer step {step_number} to product: {', '.join(product.productNames)}"
    except Exception as e:
        return f"❌ Error adding Transfer step: {str(e)}"

@mcp.tool(name="add_sonicate_step", description="Add a Sonicate step to the most recent synthesis product.", tags=["step_add"])
@mcp_tool_logger
def add_sonicate_step_tool(
    task_name: str, duration: str, used_vessel_name: str, used_vessel_type: str,
    step_number: int, atmosphere: str
) -> str:
    """Add a Sonicate step to the most recent synthesis product."""
    try:
        synthesis = get_synthesis_object(task_name)
        
        if not synthesis.Synthesis:
            return "❌ No synthesis products found. Add a product first."
        
        # Get the most recent product
        product = synthesis.Synthesis[-1]
        
        # Create the Sonicate step
        sonicate_step = SonicateStep(
            duration=duration,
            usedVesselName=used_vessel_name,
            usedVesselType=used_vessel_type,
            stepNumber=step_number,
            atmosphere=atmosphere
        )
        
        # Add the step to the product
        product.steps.append({"Sonicate": sonicate_step.to_dict()})
        
        return f"✅ Added Sonicate step {step_number} to product: {', '.join(product.productNames)}"
    except Exception as e:
        return f"❌ Error adding Sonicate step: {str(e)}"

@mcp.tool(name="get_synthesis_summary", description="Get a summary of the current synthesis object for the given task.", tags=["synthesis_retrieve"])
@mcp_tool_logger
def get_synthesis_summary_tool(task_name: str) -> str:
    """Get a summary of the current synthesis object for the given task."""
    try:
        synthesis = get_synthesis_object(task_name)
        
        if not synthesis.Synthesis:
            return f"📋 Synthesis object for {task_name} is empty. No products added yet."
        
        summary = f"📋 Synthesis Summary for {task_name}:\n"
        summary += f"Total products: {len(synthesis.Synthesis)}\n\n"
        
        for i, product in enumerate(synthesis.Synthesis, 1):
            summary += f"Product {i}:\n"
            summary += f"  Names: {', '.join(product.productNames)}\n"
            summary += f"  CCDC: {product.productCCDCNumber}\n"
            summary += f"  Steps: {len(product.steps)}\n"
            
            for j, step in enumerate(product.steps, 1):
                step_type = list(step.keys())[0]
                step_data = step[step_type]
                step_number = step_data.get('stepNumber', 'N/A')
                summary += f"    Step {j}: {step_type} (Step #{step_number})\n"
            
            summary += "\n"
        
        return summary
    except Exception as e:
        return f"❌ Error getting synthesis summary: {str(e)}"

@mcp.tool(name="mops_step_output", description="Output the synthesis object to a JSON file for the given task.", tags=["synthesis_output"])
@mcp_tool_logger
def mops_step_output_tool(task_name: str) -> str:
    """Output the synthesis object to a JSON file for the given task."""
    try:
        synthesis = get_synthesis_object(task_name)
        
        if not synthesis.Synthesis:
            return f"❌ No synthesis data to output for {task_name}"
        
        # Create task directory if it doesn't exist
        task_dir = os.path.join(SANDBOX_TASK_DIR, task_name)
        os.makedirs(task_dir, exist_ok=True)
        
        # Use atomic write like the CBU server
        output_path = os.path.join(task_dir, f"{task_name}_step.json")
        atomic_write_json(output_path, synthesis.to_dict())
        
        return f"✅ Synthesis step data written to: {output_path}"
            
    except Exception as e:
        return f"❌ Error outputting synthesis step data: {str(e)}"

@mcp.prompt(name="instruction", description="This prompt provides detailed instructions for the synthesis step server")
def instruction_prompt():
    return """
    SYNTHESIS STEP EXTRACTION INSTRUCTIONS

    This server helps you extract and structure synthesis step information from scientific papers.
    Synthesis steps include various types like Add, HeatChill, Filter, Stir, Crystallization, etc.

    EXTRACTION APPROACH:

    1. First, identify the synthesis products and their CCDC numbers
    2. For each product, extract the synthesis steps in chronological order
    3. Each step should be properly categorized and include all required fields
    4. Use the appropriate vessel types and atmosphere conditions

    STEP TYPES AND REQUIREMENTS:

    Add Step:
    - Vessel information (name and type)
    - Chemical names and amounts
    - Step number, atmosphere, duration
    - Stirring, pH target, layering, comments

    HeatChill Step:
    - Duration, device, target temperature
    - Heating/cooling rate, vacuum conditions
    - Vessel information, sealing, stirring
    - Step number, atmosphere, comments

    Filter Step:
    - Washing solvents and amounts
    - Vacuum filtration, number of filtrations
    - Vessel information, step number, atmosphere, comments

    Stir Step:
    - Duration, vessel information
    - Step number, atmosphere, temperature, wait status

    Crystallization Step:
    - Vessel information, target temperature
    - Duration, step number, atmosphere, comments

    Dry Step:
    - Duration, vessel information, pressure, temperature
    - Step number, atmosphere, drying agents, comments

    Evaporate Step:
    - Duration, vessel information, pressure, temperature
    - Rotary evaporator usage, atmosphere
    - Removed species, target volume, comments

    Dissolve Step:
    - Duration, vessel information, solvents
    - Step number, atmosphere, comments

    Separate Step:
    - Duration, vessel information, solvents
    - Separation type, step number, atmosphere, comments

    Transfer Step:
    - Duration, source and target vessel information
    - Step number, layering, transferred amount, atmosphere, comments

    Sonicate Step:
    - Duration, vessel information
    - Step number, atmosphere

    WORKFLOW:
    1) init_synthesis_object(task_name)
    2) For each synthesis product:
       - add_synthesis_product(task_name, product_names, product_ccdc_number)
       - For each step in the synthesis:
         - Use the appropriate add_*_step tool based on step type
         - Include all required fields for each step type
    3) get_synthesis_summary(task_name) to inspect
    4) mops_step_output(task_name) to write final JSON

    VESSEL TYPES (use exactly as listed):
    - Teflon-lined stainless-steel vessel
    - glass vial
    - quartz tube
    - round bottom flask
    - glass scintillation vial
    - pyrex tube
    - schlenk flask

    ATMOSPHERE OPTIONS (use exactly as listed):
    - N2
    - Ar
    - Air
    - N/A

    SEPARATION TYPES (use exactly as listed):
    - extraction
    - washing
    - column
    - centrifuge

    Notes:
    - All persistence is atomic and file-locked to avoid lost updates
    - Chemical names should be arrays of strings
    - Use "N/A" for missing string data, 0 for numeric data
    - Ensure step numbers are sequential and unique within each product
    """

if __name__ == "__main__":
    mcp.run(transport="stdio")
