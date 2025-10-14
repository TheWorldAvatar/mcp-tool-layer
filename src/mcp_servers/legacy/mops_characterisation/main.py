from fastmcp import FastMCP
from models.locations import SANDBOX_TASK_DIR
from pydantic import BaseModel
import json, os, re, tempfile, uuid, sys
from typing import Iterator, Tuple, Optional
from contextlib import contextmanager

# Cross-platform file locking
try:
    import fcntl
    HAVE_FCNTL = True
except Exception:
    HAVE_FCNTL = False
    import msvcrt

from models.Characterisation import (
    Characterisation, CharacterisationDevice, CharacterisationItem,
    HNMRDevice, ElementalAnalysisDevice, InfraredSpectroscopyDevice,
    HNMRData, ElementalAnalysisData, InfraredSpectroscopyData
)
from src.utils.global_logger import get_logger, mcp_tool_logger

log = get_logger(__name__)
mcp = FastMCP(name="mops_characterisation_output")

# ----------------------------
# Helpers: paths, locking, IO
# ----------------------------
def get_characterisation_file_path(task_name: str) -> str:
    return os.path.join(SANDBOX_TASK_DIR, f"{task_name}_characterisation.json")

@contextmanager
def locked_file(path: str, mode: str):
    """
    Open + lock a file. Ensures exclusive access across multi-process calls.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # 'a+' ensures file exists; then reopen with desired mode if needed
    if not os.path.exists(path):
        with open(path, "a", encoding="utf-8"):
            pass
    f = open(path, mode, encoding="utf-8")
    try:
        if HAVE_FCNTL:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        else:
            msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
        yield f
    finally:
        try:
            if HAVE_FCNTL:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            else:
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        except Exception:
            pass
        f.close()

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

# ----------------------------
# Persistence (versioned)
# ----------------------------
def _empty_characterisation() -> Characterisation:
    return Characterisation()

def load_existing_characterisation(task_name: str) -> Characterisation:
    path = get_characterisation_file_path(task_name)
    if not os.path.exists(path):
        return _empty_characterisation()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return Characterisation.from_dict(data)
    except Exception as e:
        log.exception("Error loading existing characterisation: %s", e)
        # fail-safe: do not blow up the session
        return _empty_characterisation()

def save_characterisation(task_name: str, char: Characterisation) -> None:
    path = get_characterisation_file_path(task_name)
    atomic_write_json(path, char.to_dict())

# ----------------------------
# MCP prompt
# ----------------------------
@mcp.prompt(name="instruction", description="This prompt provides detailed instructions for the characterisation output server")
def instruction_prompt():
    return """
    CHARACTERISATION EXTRACTION INSTRUCTIONS

    This server helps you extract and structure characterisation information from scientific papers.
    Characterisation includes three main techniques: HNMR, Elemental Analysis, and Infrared Spectroscopy.

    EXTRACTION APPROACH (Two Steps):
    
    Step 1: Extract the names of the characterization devices from the general synthesis section.
    - Look for device names mentioned in the synthesis text
    - Extract HNMR device name, frequency, and solvents
    - Extract Elemental Analysis device name
    - Extract IR device name and solvents
    
    Step 2: Extract detailed characterization data for each synthesis procedure.
    - Extract data for each and every product separately
    - Include HNMR shifts, solvent, and temperature
    - Include Elemental Analysis percentages, formula, and measurement device
    - Include IR material and bands
    - Use "N/A" for missing string data, 0 for missing numeric data

    WORKFLOW:
    1) init_characterisation_object(task_name)
    2) For each set of characterisation data mentioned in the paper:
       - add_characterisation_device(task_name, hnmr_device_name, hnmr_frequency, hnmr_solvents, 
         ea_device_name, ir_device_name, ir_solvents)
       - add_characterisation_item(task_name, product_names, ccdc_number, hnmr_shifts, 
         hnmr_solvent, hnmr_temperature, ea_calculated, ea_experimental, ea_formula, 
         ea_device, ir_material, ir_bands)
    3) get_characterisation_summary(task_name) to inspect
    4) mops_characterisation_output(task_name) to write final JSON

    Notes:
    - Each characterisation device can have multiple characterisation items
    - CCDC numbers are used as unique identifiers for products
    - Focus on the synthesis section of the paper
    - All persistence is atomic and file-locked to avoid lost updates.
    """

# ----------------------------
# MCP tools
# ----------------------------
@mcp.tool(name="init_characterisation_object", description="Initialize an empty characterisation object for a task.", tags=["characterisation_init"])
@mcp_tool_logger
def init_characterisation_object_tool(task_name: str) -> str:
    try:
        char = _empty_characterisation()
        save_characterisation(task_name, char)
        return f"Initialized empty characterisation object for task: {task_name}"
    except Exception as e:
        log.exception("init_characterisation_object failed")
        return f"Error initializing characterisation object: {str(e)}"

@mcp.tool(name="add_characterisation_device", description="Add a characterisation device with HNMR, Elemental Analysis, and IR devices.", tags=["characterisation_add"])
@mcp_tool_logger
def add_characterisation_device_tool(
    task_name: str, hnmr_device_name: str, hnmr_frequency: str, hnmr_solvents: list[str],
    ea_device_name: str, ir_device_name: str, ir_solvents: list[str]
) -> str:
    try:
        path = get_characterisation_file_path(task_name)
        with locked_file(path, "r+") as f:
            try:
                data = json.load(f)
                char = Characterisation.from_dict(data)
            except Exception:
                char = _empty_characterisation()

            # Create new characterisation device
            hnmr_device = HNMRDevice(
                deviceName=hnmr_device_name,
                frequency=hnmr_frequency,
                solventNames=hnmr_solvents
            )
            
            ea_device = ElementalAnalysisDevice(
                deviceName=ea_device_name
            )
            
            ir_device = InfraredSpectroscopyDevice(
                deviceName=ir_device_name,
                solventNames=ir_solvents
            )
            
            new_device = CharacterisationDevice(
                HNMRDevice=hnmr_device,
                ElementalAnalysisDevice=ea_device,
                InfraredSpectroscopyDevice=ir_device,
                Characterisation=[]
            )
            
            char.Devices.append(new_device)

            # atomic save after lock
            f.seek(0); f.truncate()
            json.dump(char.to_dict(), f, indent=2, ensure_ascii=False)
            f.flush()  # ensure write before unlock
            
        return f"Added characterisation device to task: {task_name}"
    except Exception as e:
        log.exception("add_characterisation_device failed")
        return f"Error adding characterisation device: {str(e)}"

@mcp.tool(name="add_characterisation_item", description="Add a characterisation item to the most recent device.", tags=["characterisation_add"])
@mcp_tool_logger
def add_characterisation_item_tool(
    task_name: str, product_names: list[str], ccdc_number: str,
    hnmr_shifts: str, hnmr_solvent: str, hnmr_temperature: str,
    ea_calculated: str, ea_experimental: str, ea_formula: str, ea_device: str,
    ir_material: str, ir_bands: str
) -> str:
    try:
        path = get_characterisation_file_path(task_name)
        with locked_file(path, "r+") as f:
            try:
                data = json.load(f)
                char = Characterisation.from_dict(data)
            except Exception:
                char = _empty_characterisation()

            if not char.Devices:
                return f"No characterisation devices found in task: {task_name}. Please add a device first."

            # Get the most recent device
            device = char.Devices[-1]
            
            # Create characterisation data
            hnmr_data = HNMRData(
                shifts=hnmr_shifts,
                solvent=hnmr_solvent,
                temperature=hnmr_temperature
            )
            
            ea_data = ElementalAnalysisData(
                weightPercentageCalculated=ea_calculated,
                weightPercentageExperimental=ea_experimental,
                chemicalFormula=ea_formula,
                measurementDevice=ea_device
            )
            
            ir_data = InfraredSpectroscopyData(
                material=ir_material,
                bands=ir_bands
            )
            
            # Create characterisation item
            char_item = CharacterisationItem(
                productNames=product_names,
                productCCDCNumber=ccdc_number,
                HNMR=hnmr_data,
                ElementalAnalysis=ea_data,
                InfraredSpectroscopy=ir_data
            )
            
            device.Characterisation.append(char_item)

            # atomic save after lock
            f.seek(0); f.truncate()
            json.dump(char.to_dict(), f, indent=2, ensure_ascii=False)
            f.flush()  # ensure write before unlock
            
        return f"Added characterisation item for CCDC {ccdc_number} to task: {task_name}"
    except Exception as e:
        log.exception("add_characterisation_item failed")
        return f"Error adding characterisation item: {str(e)}"

@mcp.tool(name="get_characterisation_summary", description="Get a summary of the current characterisation object structure.", tags=["characterisation_info"])
@mcp_tool_logger
def get_characterisation_summary_tool(task_name: str) -> str:
    try:
        char = load_existing_characterisation(task_name)
        summary = [f"Characterisation object summary for task: {task_name}",
                   f"Total devices: {len(char.Devices)}", ""]
        for i, device in enumerate(char.Devices):
            summary.append(f"Device {i+1}:")
            summary.append(f"  HNMR Device: {device.HNMRDevice.deviceName} ({device.HNMRDevice.frequency})")
            summary.append(f"  Elemental Analysis Device: {device.ElementalAnalysisDevice.deviceName}")
            summary.append(f"  IR Device: {device.InfraredSpectroscopyDevice.deviceName}")
            summary.append(f"  Characterisation Items: {len(device.Characterisation)}")
            for j, item in enumerate(device.Characterisation):
                summary.append(f"    Item {j+1}: {', '.join(item.productNames)} (CCDC: {item.productCCDCNumber})")
            summary.append("")
        return "\n".join(summary)
    except Exception as e:
        log.exception("get_characterisation_summary failed")
        return f"Error getting characterisation summary: {str(e)}"

@mcp.tool(name="mops_characterisation_output", description="Output the final characterisation structure to a file.", tags=["mops_characterisation_output"])
@mcp_tool_logger
def mops_characterisation_output_tool(task_name: str) -> str:
    try:
        char = load_existing_characterisation(task_name)
        output_path = os.path.join(SANDBOX_TASK_DIR, f"{task_name}_characterisation.json")
        atomic_write_json(output_path, char.to_dict())
        return f"Final characterisation structure output to {output_path}"
    except Exception as e:
        log.exception("mops_characterisation_output failed")
        return f"Error outputting characterisation structure: {str(e)}"

# Optional utilities
@mcp.tool(name="list_characterisation_devices", description="List characterisation devices for a task.", tags=["characterisation_info"])
@mcp_tool_logger
def list_characterisation_devices_tool(task_name: str) -> str:
    char = load_existing_characterisation(task_name)
    if not char.Devices:
        return "No characterisation devices found."
    return "\n".join(f"Device {i+1}: {device.HNMRDevice.deviceName} | {device.ElementalAnalysisDevice.deviceName} | {device.InfraredSpectroscopyDevice.deviceName}" 
                     for i, device in enumerate(char.Devices))

@mcp.tool(name="remove_characterisation_device", description="Remove a characterisation device by index.", tags=["characterisation_remove"])
@mcp_tool_logger
def remove_characterisation_device_tool(task_name: str, device_index: int) -> str:
    try:
        path = get_characterisation_file_path(task_name)
        with locked_file(path, "r+") as f:
            try:
                data = json.load(f)
                char = Characterisation.from_dict(data)
            except Exception:
                char = _empty_characterisation()

            if char.remove_device(device_index):
                f.seek(0); f.truncate()
                json.dump(char.to_dict(), f, indent=2, ensure_ascii=False)
                f.flush()
                return f"Removed characterisation device at index {device_index} from task: {task_name}"
            else:
                return f"Characterisation device at index {device_index} not found in task: {task_name}"
    except Exception as e:
        log.exception("remove_characterisation_device failed")
        return f"Error removing characterisation device: {str(e)}"

if __name__ == "__main__":
    mcp.run(transport="stdio")
