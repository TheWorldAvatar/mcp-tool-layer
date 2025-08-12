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

from models.Chemical import (
    Chemical, ChemicalInput, ChemicalOutput,
    SynthesisStep, SynthesisProcedure
)
from src.utils.global_logger import get_logger, mcp_tool_logger

log = get_logger(__name__)
mcp = FastMCP(name="mops_chemical_output")

# ----------------------------
# Helpers: paths, locking, IO
# ----------------------------
def get_chemical_file_path(task_name: str) -> str:
    return os.path.join(SANDBOX_TASK_DIR, f"{task_name}_chemical.json")

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
        # Ensure temporary file is always cleaned up
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass

# ----------------------------
# Persistence (versioned)
# ----------------------------
def _empty_chemical() -> Chemical:
    return Chemical(synthesisProcedures=[])

def load_existing_chemical(task_name: str) -> Chemical:
    path = get_chemical_file_path(task_name)
    if not os.path.exists(path):
        return _empty_chemical()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return Chemical.from_dict(data)
    except Exception as e:
        log.exception("Error loading existing chemical: %s", e)
        # fail-safe: do not blow up the session
        return _empty_chemical()

def save_chemical(task_name: str, chemical: Chemical) -> None:
    path = get_chemical_file_path(task_name)
    atomic_write_json(path, chemical.to_dict())

# ----------------------------
# Normalization utilities
# ----------------------------
_name_space_re = re.compile(r"\s+")
_dash_re = re.compile(r"\s*-\s*")

def norm_title(s: str) -> str:
    """
    Normalize titles for lookup: trim, unify spaces/dashes, lowercase.
    """
    s = s.strip()
    s = _dash_re.sub("-", s)
    s = _name_space_re.sub(" ", s)
    return s.lower()

def find_procedure(chemical: Chemical, procedure_name: str) -> Optional[SynthesisProcedure]:
    target = norm_title(procedure_name)
    for proc in chemical.synthesisProcedures:
        if norm_title(proc.procedureName) == target:
            return proc
    return None

def resolve_step_index(requested_index: int, steps_len: int) -> Optional[int]:
    """
    Accept both 0-based and 1-based indices.
    - If steps_len == 0, always out-of-range.
    - If requested_index == 0 -> 0 (first step).
    - If requested_index in [1..steps_len] -> requested_index-1
    """
    if steps_len <= 0:
        return None
    if requested_index == 0:
        return 0
    if 1 <= requested_index <= steps_len:
        return requested_index - 1
    return None

# ----------------------------
# MCP prompt
# ----------------------------
@mcp.prompt(name="instruction", description="This prompt provides detailed instructions for the chemical output server")
def instruction_prompt():
    return """
    CHEMICAL EXTRACTION INSTRUCTIONS

    This server helps you extract and structure chemical synthesis procedures from scientific papers.

    ** Important **: 
    
    1. Both solvents and reagents are input chemicals. 
    2. Strictly separate different MOPs, do not mix them up. 

    WORKFLOW:
    1) init_chemical_object(task_name)
    2) For each synthesis procedure:
       - upsert_synthesis_procedure(task_name, "Procedure Name")
       - add_synthesis_step(task_name, "Procedure Name")  # repeat as needed
       - add_input_chemical(...) / add_output_chemical(...) against a step index (0- or 1-based)
    3) get_chemical_summary(task_name) to inspect
    4) mops_chemical_output(task_name) to write final JSON

    Notes:
    - Procedure title matching is case/whitespace/dash-insensitive.
    - Step indices may be 0- or 1-based; the API accepts both.
    - All persistence is atomic and file-locked to avoid lost updates.
    - CCDC number is the index for MOPs in the CCDC database, usually is a 6-7 digits number.
    """

# ----------------------------
# MCP tools
# ----------------------------
@mcp.tool(name="init_chemical_object", description="Initialize an empty chemical object for a task.", tags=["chemical_init"])
@mcp_tool_logger
def init_chemical_object_tool(task_name: str) -> str:
    try:
        chem = _empty_chemical()
        save_chemical(task_name, chem)
        return f"Initialized empty chemical object for task: {task_name}"
    except Exception as e:
        log.exception("init_chemical_object failed")
        return f"Error initializing chemical object: {str(e)}"

@mcp.tool(name="upsert_synthesis_procedure", description="Create the procedure if missing; otherwise no-op.", tags=["chemical_add"])
@mcp_tool_logger
def upsert_synthesis_procedure_tool(task_name: str, procedure_name: str) -> str:
    try:
        path = get_chemical_file_path(task_name)
        with locked_file(path, "r+") as f:
            try:
                data = json.load(f)
                chem = Chemical.from_dict(data)
            except Exception:
                chem = _empty_chemical()

            existing = find_procedure(chem, procedure_name)
            if existing:
                msg = f"Procedure '{procedure_name}' already exists in task: {task_name}"
            else:
                new_proc = SynthesisProcedure(procedureName=procedure_name, steps=[])
                chem.synthesisProcedures.append(new_proc)
                msg = f"Added synthesis procedure '{procedure_name}' to task: {task_name}"

            # atomic save after lock
            f.seek(0); f.truncate()
            json.dump(chem.to_dict(), f, indent=2, ensure_ascii=False)
            f.flush()  # ensure write before unlock
        return msg
    except Exception as e:
        log.exception("upsert_synthesis_procedure failed")
        return f"Error upserting synthesis procedure: {str(e)}"

# Keep backward compatibility with your original tool name
@mcp.tool(name="add_synthesis_procedure", description="Add a new synthesis procedure to the chemical object.", tags=["chemical_add"])
@mcp_tool_logger
def add_synthesis_procedure_tool(task_name: str, procedure_name: str) -> str:
    return upsert_synthesis_procedure_tool(task_name, procedure_name)

@mcp.tool(name="add_synthesis_step", description="Add a synthesis step to a specific procedure.", tags=["chemical_add"])
@mcp_tool_logger
def add_synthesis_step_tool(task_name: str, procedure_name: str) -> str:
    try:
        path = get_chemical_file_path(task_name)
        with locked_file(path, "r+") as f:
            try:
                data = json.load(f)
                chem = Chemical.from_dict(data)
            except Exception:
                chem = _empty_chemical()

            proc = find_procedure(chem, procedure_name)
            if not proc:
                return f"Procedure '{procedure_name}' not found in task: {task_name}"

            new_step = SynthesisStep(inputChemicals=[], outputChemical=[])


            proc.steps.append(new_step)

            f.seek(0); f.truncate()
            json.dump(chem.to_dict(), f, indent=2, ensure_ascii=False)
            f.flush()

        return f"Added synthesis step to procedure '{procedure_name}' in task: {task_name}"
    except Exception as e:
        log.exception("add_synthesis_step failed")
        return f"Error adding synthesis step: {str(e)}"

@mcp.tool(name="add_input_chemical", description="Add an input chemical to a specific step.", tags=["chemical_add"])
@mcp_tool_logger
def add_input_chemical_tool(
    task_name: str, procedure_name: str, step_index: int,
    chemical_formula: str, chemical_names: list, chemical_amount: str,
    supplier_name: str, purity: str
) -> str:
    try:
        path = get_chemical_file_path(task_name)
        with locked_file(path, "r+") as f:
            try:
                data = json.load(f)
                chem = Chemical.from_dict(data)
            except Exception:
                chem = _empty_chemical()

            proc = find_procedure(chem, procedure_name)
            if not proc:
                return f"Procedure '{procedure_name}' not found in task: {task_name}"

            idx = resolve_step_index(step_index, len(proc.steps))
            if idx is None:
                return f"Step index {step_index} out of range for procedure '{procedure_name}' (steps: {len(proc.steps)})"

            target_step = proc.steps[idx]
            new_input = ChemicalInput(
                chemical=[{
                    "chemicalFormula": chemical_formula,
                    "chemicalName": chemical_names,
                    "chemicalAmount": chemical_amount
                }],
                supplierName=supplier_name,
                purity=purity
            )
            target_step.inputChemicals.append(new_input)

            f.seek(0); f.truncate()
            json.dump(chem.to_dict(), f, indent=2, ensure_ascii=False)
            f.flush()

        return f"Added input chemical '{chemical_formula}' to step {idx} of procedure '{procedure_name}' in task: {task_name}"
    except Exception as e:
        log.exception("add_input_chemical failed")
        return f"Error adding input chemical: {str(e)}"

@mcp.tool(name="add_output_chemical", description="Add an output chemical to a specific step.", tags=["chemical_add"])
@mcp_tool_logger
def add_output_chemical_tool(
    task_name: str, procedure_name: str, step_index: int,
    chemical_formula: str, chemical_names: list, yield_amount: str, ccdc_number: str
) -> str:
    try:
        path = get_chemical_file_path(task_name)
        with locked_file(path, "r+") as f:
            try:
                data = json.load(f)
                chem = Chemical.from_dict(data)
            except Exception:
                chem = _empty_chemical()

            proc = find_procedure(chem, procedure_name)
            if not proc:
                return f"Procedure '{procedure_name}' not found in task: {task_name}"

            idx = resolve_step_index(step_index, len(proc.steps))
            if idx is None:
                return f"Step index {step_index} out of range for procedure '{procedure_name}' (steps: {len(proc.steps)})"

            target_step = proc.steps[idx]
            new_output = ChemicalOutput(
                chemicalFormula=chemical_formula,
                names=chemical_names,
                yield_amount=yield_amount,
                CCDCNumber=ccdc_number
            )
            target_step.outputChemical.append(new_output)

            f.seek(0); f.truncate()
            json.dump(chem.to_dict(), f, indent=2, ensure_ascii=False)
            f.flush()

        return f"Added output chemical '{chemical_formula}' to step {idx} of procedure '{procedure_name}' in task: {task_name}"
    except Exception as e:
        log.exception("add_output_chemical failed")
        return f"Error adding output chemical: {str(e)}"

@mcp.tool(name="get_chemical_summary", description="Get a summary of the current chemical object structure.", tags=["chemical_info"])
@mcp_tool_logger
def get_chemical_summary_tool(task_name: str) -> str:
    try:
        chem = load_existing_chemical(task_name)
        summary = [f"Chemical object summary for task: {task_name}",
                   f"Total procedures: {len(chem.synthesisProcedures)}", ""]
        for i, proc in enumerate(chem.synthesisProcedures):
            summary.append(f"Procedure {i+1}: {proc.procedureName}")
            summary.append(f"  Steps: {len(proc.steps)}")
            for j, step in enumerate(proc.steps):
                summary.append(f"    Step {j+1}:")
                summary.append(f"      Input chemicals: {len(step.inputChemicals)}")
                summary.append(f"      Output chemicals: {len(step.outputChemical)}")
            summary.append("")
        return "\n".join(summary)
    except Exception as e:
        log.exception("get_chemical_summary failed")
        return f"Error getting chemical summary: {str(e)}"

@mcp.tool(name="mops_chemical_output", description="Output the final chemical structure to a file.", tags=["mops_chemical_output"])
@mcp_tool_logger
def mops_chemical_output_tool(task_name: str) -> str:
    try:
        chem = load_existing_chemical(task_name)
        output_path = os.path.join(SANDBOX_TASK_DIR, f"{task_name}_chemical.json")
        atomic_write_json(output_path, chem.to_dict())
 
        
        return f"Final chemical structure output to {output_path}"
    except Exception as e:
        log.exception("mops_chemical_output failed")
        return f"Error outputting chemical structure: {str(e)}"

# Optional utilities (nice to have)
@mcp.tool(name="list_procedures", description="List procedure names for a task.", tags=["chemical_info"])
@mcp_tool_logger
def list_procedures_tool(task_name: str) -> str:
    chem = load_existing_chemical(task_name)
    if not chem.synthesisProcedures:
        return "No procedures found."
    return "\n".join(p.procedureName for p in chem.synthesisProcedures)

@mcp.tool(name="ensure_procedures", description="Idempotently create multiple procedures.", tags=["chemical_add"])
@mcp_tool_logger
def ensure_procedures_tool(task_name: str, procedure_names: list[str]) -> str:
    created, existing = [], []
    for name in procedure_names:
        msg = upsert_synthesis_procedure_tool(task_name, name)
        (existing if "already exists" in msg else created).append(name)
    return f"Created: {created}; Existing: {existing}"

if __name__ == "__main__":
    mcp.run(transport="stdio")
