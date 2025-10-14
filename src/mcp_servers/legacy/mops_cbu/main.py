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

from models.CBU import (
    CBU, CBUProcedure
)
from src.utils.global_logger import get_logger, mcp_tool_logger

log = get_logger(__name__)
mcp = FastMCP(name="mops_cbu_output")

# ----------------------------
# Helpers: paths, locking, IO
# ----------------------------
def get_cbu_file_path(task_name: str) -> str:
    return os.path.join(SANDBOX_TASK_DIR, f"{task_name}_cbu.json")

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
def _empty_cbu() -> CBU:
    return CBU()

def load_existing_cbu(task_name: str) -> CBU:
    path = get_cbu_file_path(task_name)
    if not os.path.exists(path):
        return _empty_cbu()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return CBU.from_dict(data)
    except Exception as e:
        log.exception("Error loading existing CBU: %s", e)
        # fail-safe: do not blow up the session
        return _empty_cbu()

def save_cbu(task_name: str, cbu: CBU) -> None:
    path = get_cbu_file_path(task_name)
    atomic_write_json(path, cbu.to_dict())

# ----------------------------
# Normalization utilities
# ----------------------------
def find_procedure(cbu: CBU, ccdc_number: str) -> Optional[CBUProcedure]:
    for proc in cbu.synthesisProcedures:
        if proc.mopCCDCNumber == ccdc_number:
            return proc
    return None

# ----------------------------
# MCP prompt
# ----------------------------
@mcp.prompt(name="instruction", description="This prompt provides detailed instructions for the CBU output server")
def instruction_prompt():
    return """
    CBU EXTRACTION INSTRUCTIONS

    This server helps you extract and structure CBU (Chemical Building Unit) information from scientific papers.
    Each MOP has exactly two CBUs, one organic and one inorganic.

    WORKFLOW:
    1) init_cbu_object(task_name)
    2) For each MOP mentioned in the paper:
       - add_cbu_procedure(task_name, ccdc_number, cbu_formula1, cbu_names1, cbu_formula2, cbu_names2)
    3) get_cbu_summary(task_name) to inspect
    4) mops_cbu_output(task_name) to write final JSON

    Notes:
    - Each MOP must have exactly two CBUs
    - CCDC numbers are used as unique identifiers
    - All persistence is atomic and file-locked to avoid lost updates.
    """

# ----------------------------
# MCP tools
# ----------------------------
@mcp.tool(name="init_cbu_object", description="Initialize an empty CBU object for a task.", tags=["cbu_init"])
@mcp_tool_logger
def init_cbu_object_tool(task_name: str) -> str:
    try:
        cbu = _empty_cbu()
        save_cbu(task_name, cbu)
        return f"Initialized empty CBU object for task: {task_name}"
    except Exception as e:
        log.exception("init_cbu_object failed")
        return f"Error initializing CBU object: {str(e)}"

@mcp.tool(name="add_cbu_procedure", description="Add a CBU procedure with exactly two CBUs for a MOP.", tags=["cbu_add"])
@mcp_tool_logger
def add_cbu_procedure_tool(
    task_name: str, ccdc_number: str, 
    cbu_formula1: str, cbu_names1: list[str],
    cbu_formula2: str, cbu_names2: list[str]
) -> str:
    try:
        path = get_cbu_file_path(task_name)
        with locked_file(path, "r+") as f:
            try:
                data = json.load(f)
                cbu = CBU.from_dict(data)
            except Exception:
                cbu = _empty_cbu()

            # Check if procedure already exists
            existing = find_procedure(cbu, ccdc_number)
            if existing:
                return f"CBU procedure with CCDC {ccdc_number} already exists in task: {task_name}"

            # Create new CBU procedure
            new_proc = CBUProcedure(
                mopCCDCNumber=ccdc_number,
                cbuFormula1=cbu_formula1,
                cbuSpeciesNames1=cbu_names1,
                cbuFormula2=cbu_formula2,
                cbuSpeciesNames2=cbu_names2
            )
            
            cbu.synthesisProcedures.append(new_proc)

            # atomic save after lock
            f.seek(0); f.truncate()
            json.dump(cbu.to_dict(), f, indent=2, ensure_ascii=False)
            f.flush()  # ensure write before unlock
            
        return f"Added CBU procedure for CCDC {ccdc_number} to task: {task_name}"
    except Exception as e:
        log.exception("add_cbu_procedure failed")
        return f"Error adding CBU procedure: {str(e)}"

@mcp.tool(name="get_cbu_summary", description="Get a summary of the current CBU object structure.", tags=["cbu_info"])
@mcp_tool_logger
def get_cbu_summary_tool(task_name: str) -> str:
    try:
        cbu = load_existing_cbu(task_name)
        summary = [f"CBU object summary for task: {task_name}",
                   f"Total procedures: {len(cbu.synthesisProcedures)}", ""]
        for i, proc in enumerate(cbu.synthesisProcedures):
            summary.append(f"Procedure {i+1}: CCDC {proc.mopCCDCNumber}")
            summary.append(f"  CBU 1: {proc.cbuFormula1} - {', '.join(proc.cbuSpeciesNames1)}")
            summary.append(f"  CBU 2: {proc.cbuFormula2} - {', '.join(proc.cbuSpeciesNames2)}")
            summary.append("")
        return "\n".join(summary)
    except Exception as e:
        log.exception("get_cbu_summary failed")
        return f"Error getting CBU summary: {str(e)}"

@mcp.tool(name="mops_cbu_output", description="Output the final CBU structure to a file.", tags=["mops_cbu_output"])
@mcp_tool_logger
def mops_cbu_output_tool(task_name: str) -> str:
    try:
        cbu = load_existing_cbu(task_name)
        output_path = os.path.join(SANDBOX_TASK_DIR, f"{task_name}.json")
        atomic_write_json(output_path, cbu.to_dict())
        return f"Final CBU structure output to {output_path}"
    except Exception as e:
        log.exception("mops_cbu_output failed")
        return f"Error outputting CBU structure: {str(e)}"

# Optional utilities
@mcp.tool(name="list_cbu_procedures", description="List CCDC numbers for a task.", tags=["cbu_info"])
@mcp_tool_logger
def list_cbu_procedures_tool(task_name: str) -> str:
    cbu = load_existing_cbu(task_name)
    if not cbu.synthesisProcedures:
        return "No CBU procedures found."
    return "\n".join(f"CCDC {p.mopCCDCNumber}" for p in cbu.synthesisProcedures)

@mcp.tool(name="remove_cbu_procedure", description="Remove a CBU procedure by CCDC number.", tags=["cbu_remove"])
@mcp_tool_logger
def remove_cbu_procedure_tool(task_name: str, ccdc_number: str) -> str:
    try:
        path = get_cbu_file_path(task_name)
        with locked_file(path, "r+") as f:
            try:
                data = json.load(f)
                cbu = CBU.from_dict(data)
            except Exception:
                cbu = _empty_cbu()

            if cbu.remove_procedure(ccdc_number):
                f.seek(0); f.truncate()
                json.dump(cbu.to_dict(), f, indent=2, ensure_ascii=False)
                f.flush()
                return f"Removed CBU procedure with CCDC {ccdc_number} from task: {task_name}"
            else:
                return f"CBU procedure with CCDC {ccdc_number} not found in task: {task_name}"
    except Exception as e:
        log.exception("remove_cbu_procedure failed")
        return f"Error removing CBU procedure: {str(e)}"

if __name__ == "__main__":
    mcp.run(transport="stdio")
