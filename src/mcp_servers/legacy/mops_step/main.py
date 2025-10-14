from fastmcp import FastMCP
from models.locations import SANDBOX_TASK_DIR
from src.utils.global_logger import get_logger, mcp_tool_logger

from typing import Optional, List
from contextlib import contextmanager
import json, os, tempfile

# Cross-platform file locking
try:
    import fcntl
    HAVE_FCNTL = True
except Exception:
    HAVE_FCNTL = False
    import msvcrt

from models.Step import (
    SynthesisDocument, ProductSynthesis
)
from src.mcp_servers.mops_step.operations.synthesis_operations import (
    build_step_add,
    build_step_heat_chill,
    build_step_dry,
    build_step_filter,
    build_step_sonicate,
    build_step_stir,
    build_step_crystallization,
    build_step_evaporate,
    build_step_dissolve,
    build_step_separate,
    append_step_to_product,
    summarize_document,
)

log = get_logger(__name__)
mcp = FastMCP(name="mops_step_output")


# ----------------------------
# Helpers: paths, locking, IO
# ----------------------------
def get_step_file_path(task_name: str) -> str:
    return os.path.join(SANDBOX_TASK_DIR, f"{task_name}_step.json")


@contextmanager
def locked_file(path: str, mode: str):
    """
    Open + lock a file. Ensures exclusive access across multi-process calls.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # Ensure file exists first
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
# Persistence
# ----------------------------
def _empty_doc() -> SynthesisDocument:
    return SynthesisDocument(synthesis=[])


def load_existing_step_doc(task_name: str) -> SynthesisDocument:
    path = get_step_file_path(task_name)
    if not os.path.exists(path):
        return _empty_doc()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return SynthesisDocument.from_dict(data)
    except Exception as e:
        log.exception("Error loading existing Step doc: %s", e)
        return _empty_doc()


def save_step_doc(task_name: str, doc: SynthesisDocument) -> None:
    path = get_step_file_path(task_name)
    atomic_write_json(path, doc.to_dict())


# ----------------------------
# Utilities
# ----------------------------
def find_product(doc: SynthesisDocument, ccdc_number: str) -> Optional[ProductSynthesis]:
    for p in doc.synthesis:
        if p.productCCDCNumber == ccdc_number:
            return p
    return None


def parse_chem_entries_with_amount(items: List[dict]) -> List[dict]:
    return items or []


def parse_chem_entries(items: List[dict]) -> List[dict]:
    return items or []


# ----------------------------
# MCP prompt
# ----------------------------
@mcp.prompt(name="instruction", description="This prompt provides detailed instructions for the Step output server")
def instruction_prompt():
    return (
        """
        STEP EXTRACTION INSTRUCTIONS

        This server helps you construct a structured Step object for synthesis procedures.
        Strategy: divide and conquer. First create the root document, then add a product, then append steps one-by-one.

        WORKFLOW:
        1) init_step_object(task_name)
        2) add_product_synthesis(task_name, product_names, product_ccdc_number)
        3) Append steps to that product using add_step_<type>(...) tools
        4) get_step_summary(task_name) to inspect
        5) mops_step_output(task_name) to write final JSON

        Notes:
        - Product is identified by product CCDC number
        - For nested arrays like added chemicals or solvents, pass a list of objects
          with keys: chemicalName (array of strings) and chemicalAmount (string) when required
        - All persistence is atomic and file-locked to avoid lost updates
        """
    )


# ----------------------------
# MCP tools: init and product
# ----------------------------
@mcp.tool(name="init_step_object", description="Initialize an empty Step object for a task.", tags=["step_init"])
@mcp_tool_logger
def init_step_object_tool(task_name: str) -> str:
    try:
        doc = _empty_doc()
        save_step_doc(task_name, doc)
        return f"Initialized empty Step object for task: {task_name}"
    except Exception as e:
        log.exception("init_step_object failed")
        return f"Error initializing Step object: {str(e)}"


@mcp.tool(name="add_product_synthesis", description="Add a product synthesis container to the Step object.", tags=["step_add"])
@mcp_tool_logger
def add_product_synthesis_tool(task_name: str, product_names: list[str], product_ccdc_number: str) -> str:
    try:
        path = get_step_file_path(task_name)
        with locked_file(path, "r+") as f:
            try:
                data = json.load(f)
                doc = SynthesisDocument.from_dict(data)
            except Exception:
                doc = _empty_doc()

            existing = find_product(doc, product_ccdc_number)
            if existing:
                msg = f"Product with CCDC {product_ccdc_number} already exists in task: {task_name}"
            else:
                new_prod = ProductSynthesis(productNames=list(product_names), productCCDCNumber=product_ccdc_number, steps=[])
                doc.synthesis.append(new_prod)
                msg = f"Added product (CCDC {product_ccdc_number}) to task: {task_name}"

            f.seek(0); f.truncate()
            json.dump(doc.to_dict(), f, indent=2, ensure_ascii=False)
            f.flush()
        return msg
    except Exception as e:
        log.exception("add_product_synthesis failed")
        return f"Error adding product synthesis: {str(e)}"


# ----------------------------
# MCP tools: step adders
# ----------------------------
@mcp.tool(name="add_step_add", description="Append an Add step to a product.", tags=["step_add"])
@mcp_tool_logger
def add_step_add_tool(
    task_name: str,
    product_ccdc_number: str,
    used_vessel_name: str,
    used_vessel_type: str,
    added_chemicals: list,  # list of {chemicalName: [..], chemicalAmount: ".."}
    step_number: int,
    stir: bool,
    is_layered: bool,
    atmosphere: str,
    duration: str,
    target_ph: float,
    comment: str,
) -> str:
    try:
        path = get_step_file_path(task_name)
        with locked_file(path, "r+") as f:
            try:
                data = json.load(f)
                doc = SynthesisDocument.from_dict(data)
            except Exception:
                doc = _empty_doc()

            prod = find_product(doc, product_ccdc_number)
            if not prod:
                return f"Product with CCDC {product_ccdc_number} not found in task: {task_name}"

            step_obj = build_step_add(
                used_vessel_name=used_vessel_name,
                used_vessel_type=used_vessel_type,
                added_chemicals=parse_chem_entries_with_amount(added_chemicals),
                step_number=step_number,
                stir=stir,
                is_layered=is_layered,
                atmosphere=atmosphere,
                duration=duration,
                target_ph=target_ph,
                comment=comment,
            )
            append_step_to_product(doc, product_ccdc_number, step_obj)

            f.seek(0); f.truncate()
            json.dump(doc.to_dict(), f, indent=2, ensure_ascii=False)
            f.flush()
        return f"Added Add step to product CCDC {product_ccdc_number}"
    except Exception as e:
        log.exception("add_step_add failed")
        return f"Error adding Add step: {str(e)}"


@mcp.tool(name="add_step_heat_chill", description="Append a HeatChill step to a product.", tags=["step_add"])
@mcp_tool_logger
def add_step_heat_chill_tool(
    task_name: str,
    product_ccdc_number: str,
    duration: str,
    used_device: str,
    target_temperature: str,
    heating_cooling_rate: str,
    comment: str,
    under_vacuum: bool,
    used_vessel_type: str,
    used_vessel_name: str,
    sealed_vessel: bool,
    stir: bool,
    step_number: int,
    atmosphere: str,
) -> str:
    try:
        path = get_step_file_path(task_name)
        with locked_file(path, "r+") as f:
            try:
                data = json.load(f)
                doc = SynthesisDocument.from_dict(data)
            except Exception:
                doc = _empty_doc()

            prod = find_product(doc, product_ccdc_number)
            if not prod:
                return f"Product with CCDC {product_ccdc_number} not found in task: {task_name}"

            step_obj = build_step_heat_chill(
                duration=duration,
                used_device=used_device,
                target_temperature=target_temperature,
                heating_cooling_rate=heating_cooling_rate,
                comment=comment,
                under_vacuum=under_vacuum,
                used_vessel_type=used_vessel_type,
                used_vessel_name=used_vessel_name,
                sealed_vessel=sealed_vessel,
                stir=stir,
                step_number=step_number,
                atmosphere=atmosphere,
            )
            append_step_to_product(doc, product_ccdc_number, step_obj)

            f.seek(0); f.truncate()
            json.dump(doc.to_dict(), f, indent=2, ensure_ascii=False)
            f.flush()
        return f"Added HeatChill step to product CCDC {product_ccdc_number}"
    except Exception as e:
        log.exception("add_step_heat_chill failed")
        return f"Error adding HeatChill step: {str(e)}"


@mcp.tool(name="add_step_dry", description="Append a Dry step to a product.", tags=["step_add"])
@mcp_tool_logger
def add_step_dry_tool(
    task_name: str,
    product_ccdc_number: str,
    duration: str,
    used_vessel_name: str,
    used_vessel_type: str,
    pressure: str,
    temperature: str,
    step_number: int,
    atmosphere: str,
    drying_agents: list,  # list of {chemicalName: [..]}
    comment: str,
) -> str:
    try:
        path = get_step_file_path(task_name)
        with locked_file(path, "r+") as f:
            try:
                data = json.load(f)
                doc = SynthesisDocument.from_dict(data)
            except Exception:
                doc = _empty_doc()

            prod = find_product(doc, product_ccdc_number)
            if not prod:
                return f"Product with CCDC {product_ccdc_number} not found in task: {task_name}"

            step_obj = build_step_dry(
                duration=duration,
                used_vessel_name=used_vessel_name,
                used_vessel_type=used_vessel_type,
                pressure=pressure,
                temperature=temperature,
                step_number=step_number,
                atmosphere=atmosphere,
                drying_agents=parse_chem_entries(drying_agents),
                comment=comment,
            )
            append_step_to_product(doc, product_ccdc_number, step_obj)

            f.seek(0); f.truncate()
            json.dump(doc.to_dict(), f, indent=2, ensure_ascii=False)
            f.flush()
        return f"Added Dry step to product CCDC {product_ccdc_number}"
    except Exception as e:
        log.exception("add_step_dry failed")
        return f"Error adding Dry step: {str(e)}"


@mcp.tool(name="add_step_filter", description="Append a Filter step to a product.", tags=["step_add"])
@mcp_tool_logger
def add_step_filter_tool(
    task_name: str,
    product_ccdc_number: str,
    washing_solvent: list,  # list of {chemicalName: [..], chemicalAmount: ".."}
    vacuum_filtration: bool,
    number_of_filtrations: int,
    used_vessel_name: str,
    used_vessel_type: str,
    step_number: int,
    comment: str,
    atmosphere: str,
) -> str:
    try:
        path = get_step_file_path(task_name)
        with locked_file(path, "r+") as f:
            try:
                data = json.load(f)
                doc = SynthesisDocument.from_dict(data)
            except Exception:
                doc = _empty_doc()

            prod = find_product(doc, product_ccdc_number)
            if not prod:
                return f"Product with CCDC {product_ccdc_number} not found in task: {task_name}"

            step_obj = build_step_filter(
                washing_solvent=parse_chem_entries_with_amount(washing_solvent),
                vacuum_filtration=vacuum_filtration,
                number_of_filtrations=number_of_filtrations,
                used_vessel_name=used_vessel_name,
                used_vessel_type=used_vessel_type,
                step_number=step_number,
                comment=comment,
                atmosphere=atmosphere,
            )
            append_step_to_product(doc, product_ccdc_number, step_obj)

            f.seek(0); f.truncate()
            json.dump(doc.to_dict(), f, indent=2, ensure_ascii=False)
            f.flush()
        return f"Added Filter step to product CCDC {product_ccdc_number}"
    except Exception as e:
        log.exception("add_step_filter failed")
        return f"Error adding Filter step: {str(e)}"


@mcp.tool(name="add_step_sonicate", description="Append a Sonicate step to a product.", tags=["step_add"])
@mcp_tool_logger
def add_step_sonicate_tool(
    task_name: str,
    product_ccdc_number: str,
    duration: str,
    used_vessel_name: str,
    used_vessel_type: str,
    step_number: int,
    atmosphere: str,
) -> str:
    try:
        path = get_step_file_path(task_name)
        with locked_file(path, "r+") as f:
            try:
                data = json.load(f)
                doc = SynthesisDocument.from_dict(data)
            except Exception:
                doc = _empty_doc()

            prod = find_product(doc, product_ccdc_number)
            if not prod:
                return f"Product with CCDC {product_ccdc_number} not found in task: {task_name}"

            step_obj = build_step_sonicate(
                duration=duration,
                used_vessel_name=used_vessel_name,
                used_vessel_type=used_vessel_type,
                step_number=step_number,
                atmosphere=atmosphere,
            )
            append_step_to_product(doc, product_ccdc_number, step_obj)

            f.seek(0); f.truncate()
            json.dump(doc.to_dict(), f, indent=2, ensure_ascii=False)
            f.flush()
        return f"Added Sonicate step to product CCDC {product_ccdc_number}"
    except Exception as e:
        log.exception("add_step_sonicate failed")
        return f"Error adding Sonicate step: {str(e)}"


@mcp.tool(name="add_step_stir", description="Append a Stir step to a product.", tags=["step_add"])
@mcp_tool_logger
def add_step_stir_tool(
    task_name: str,
    product_ccdc_number: str,
    duration: str,
    used_vessel_name: str,
    used_vessel_type: str,
    step_number: int,
    atmosphere: str,
    temperature: str,
    wait: bool,
) -> str:
    try:
        path = get_step_file_path(task_name)
        with locked_file(path, "r+") as f:
            try:
                data = json.load(f)
                doc = SynthesisDocument.from_dict(data)
            except Exception:
                doc = _empty_doc()

            prod = find_product(doc, product_ccdc_number)
            if not prod:
                return f"Product with CCDC {product_ccdc_number} not found in task: {task_name}"

            step_obj = build_step_stir(
                duration=duration,
                used_vessel_name=used_vessel_name,
                used_vessel_type=used_vessel_type,
                step_number=step_number,
                atmosphere=atmosphere,
                temperature=temperature,
                wait=wait,
            )
            append_step_to_product(doc, product_ccdc_number, step_obj)

            f.seek(0); f.truncate()
            json.dump(doc.to_dict(), f, indent=2, ensure_ascii=False)
            f.flush()
        return f"Added Stir step to product CCDC {product_ccdc_number}"
    except Exception as e:
        log.exception("add_step_stir failed")
        return f"Error adding Stir step: {str(e)}"


@mcp.tool(name="add_step_crystallization", description="Append a Crystallization step to a product.", tags=["step_add"])
@mcp_tool_logger
def add_step_crystallization_tool(
    task_name: str,
    product_ccdc_number: str,
    used_vessel_name: str,
    used_vessel_type: str,
    target_temperature: str,
    step_number: int,
    duration: str,
    atmosphere: str,
    comment: str,
) -> str:
    try:
        path = get_step_file_path(task_name)
        with locked_file(path, "r+") as f:
            try:
                data = json.load(f)
                doc = SynthesisDocument.from_dict(data)
            except Exception:
                doc = _empty_doc()

            prod = find_product(doc, product_ccdc_number)
            if not prod:
                return f"Product with CCDC {product_ccdc_number} not found in task: {task_name}"

            step_obj = build_step_crystallization(
                used_vessel_name=used_vessel_name,
                used_vessel_type=used_vessel_type,
                target_temperature=target_temperature,
                step_number=step_number,
                duration=duration,
                atmosphere=atmosphere,
                comment=comment,
            )
            append_step_to_product(doc, product_ccdc_number, step_obj)

            f.seek(0); f.truncate()
            json.dump(doc.to_dict(), f, indent=2, ensure_ascii=False)
            f.flush()
        return f"Added Crystallization step to product CCDC {product_ccdc_number}"
    except Exception as e:
        log.exception("add_step_crystallization failed")
        return f"Error adding Crystallization step: {str(e)}"


@mcp.tool(name="add_step_evaporate", description="Append an Evaporate step to a product.", tags=["step_add"])
@mcp_tool_logger
def add_step_evaporate_tool(
    task_name: str,
    product_ccdc_number: str,
    duration: str,
    used_vessel_name: str,
    used_vessel_type: str,
    pressure: str,
    temperature: str,
    step_number: int,
    rotary_evaporator: bool,
    atmosphere: str,
    removed_species: list,  # list of {chemicalName: [..]}
    target_volume: str,
    comment: str,
) -> str:
    try:
        path = get_step_file_path(task_name)
        with locked_file(path, "r+") as f:
            try:
                data = json.load(f)
                doc = SynthesisDocument.from_dict(data)
            except Exception:
                doc = _empty_doc()

            prod = find_product(doc, product_ccdc_number)
            if not prod:
                return f"Product with CCDC {product_ccdc_number} not found in task: {task_name}"

            step_obj = build_step_evaporate(
                duration=duration,
                used_vessel_name=used_vessel_name,
                used_vessel_type=used_vessel_type,
                pressure=pressure,
                temperature=temperature,
                step_number=step_number,
                rotary_evaporator=rotary_evaporator,
                atmosphere=atmosphere,
                removed_species=parse_chem_entries(removed_species),
                target_volume=target_volume,
                comment=comment,
            )
            append_step_to_product(doc, product_ccdc_number, step_obj)

            f.seek(0); f.truncate()
            json.dump(doc.to_dict(), f, indent=2, ensure_ascii=False)
            f.flush()
        return f"Added Evaporate step to product CCDC {product_ccdc_number}"
    except Exception as e:
        log.exception("add_step_evaporate failed")
        return f"Error adding Evaporate step: {str(e)}"


@mcp.tool(name="add_step_dissolve", description="Append a Dissolve step to a product.", tags=["step_add"])
@mcp_tool_logger
def add_step_dissolve_tool(
    task_name: str,
    product_ccdc_number: str,
    duration: str,
    used_vessel_name: str,
    used_vessel_type: str,
    solvent: list,  # list of {chemicalName: [..], chemicalAmount: ".."}
    step_number: int,
    atmosphere: str,
    comment: str,
) -> str:
    try:
        path = get_step_file_path(task_name)
        with locked_file(path, "r+") as f:
            try:
                data = json.load(f)
                doc = SynthesisDocument.from_dict(data)
            except Exception:
                doc = _empty_doc()

            prod = find_product(doc, product_ccdc_number)
            if not prod:
                return f"Product with CCDC {product_ccdc_number} not found in task: {task_name}"

            step_obj = build_step_dissolve(
                duration=duration,
                used_vessel_name=used_vessel_name,
                used_vessel_type=used_vessel_type,
                solvent=parse_chem_entries_with_amount(solvent),
                step_number=step_number,
                atmosphere=atmosphere,
                comment=comment,
            )
            append_step_to_product(doc, product_ccdc_number, step_obj)

            f.seek(0); f.truncate()
            json.dump(doc.to_dict(), f, indent=2, ensure_ascii=False)
            f.flush()
        return f"Added Dissolve step to product CCDC {product_ccdc_number}"
    except Exception as e:
        log.exception("add_step_dissolve failed")
        return f"Error adding Dissolve step: {str(e)}"


@mcp.tool(name="add_step_separate", description="Append a Separate step to a product.", tags=["step_add"])
@mcp_tool_logger
def add_step_separate_tool(
    task_name: str,
    product_ccdc_number: str,
    duration: str,
    used_vessel_name: str,
    used_vessel_type: str,
    solvent: list,  # list of {chemicalName: [..], chemicalAmount: ".."}
    step_number: int,
    separation_type: str,
    atmosphere: str,
    comment: str,
) -> str:
    try:
        path = get_step_file_path(task_name)
        with locked_file(path, "r+") as f:
            try:
                data = json.load(f)
                doc = SynthesisDocument.from_dict(data)
            except Exception:
                doc = _empty_doc()

            prod = find_product(doc, product_ccdc_number)
            if not prod:
                return f"Product with CCDC {product_ccdc_number} not found in task: {task_name}"

            step_obj = build_step_separate(
                duration=duration,
                used_vessel_name=used_vessel_name,
                used_vessel_type=used_vessel_type,
                solvent=parse_chem_entries_with_amount(solvent),
                step_number=step_number,
                separation_type=separation_type,
                atmosphere=atmosphere,
                comment=comment,
            )
            append_step_to_product(doc, product_ccdc_number, step_obj)

            f.seek(0); f.truncate()
            json.dump(doc.to_dict(), f, indent=2, ensure_ascii=False)
            f.flush()
        return f"Added Separate step to product CCDC {product_ccdc_number}"
    except Exception as e:
        log.exception("add_step_separate failed")
        return f"Error adding Separate step: {str(e)}"


@mcp.tool(name="add_step_transfer", description="Append a Transfer step to a product.", tags=["step_add"])
@mcp_tool_logger
def add_step_transfer_tool(
    task_name: str,
    product_ccdc_number: str,
    duration: str,
    used_vessel_name: str,
    used_vessel_type: str,
    target_vessel_name: str,
    target_vessel_type: str,
    step_number: int,
    is_layered: bool,
    transfered_amount: str,
    comment: str,
    atmosphere: str,
) -> str:
    try:
        path = get_step_file_path(task_name)
        with locked_file(path, "r+") as f:
            try:
                data = json.load(f)
                doc = SynthesisDocument.from_dict(data)
            except Exception:
                doc = _empty_doc()

            prod = find_product(doc, product_ccdc_number)
            if not prod:
                return f"Product with CCDC {product_ccdc_number} not found in task: {task_name}"

            step_obj = Step(
                Transfer(
                    duration=duration,
                    usedVesselName=used_vessel_name,
                    usedVesselType=used_vessel_type,
                    targetVesselName=target_vessel_name,
                    targetVesselType=target_vessel_type,
                    stepNumber=int(step_number),
                    isLayered=bool(is_layered),
                    transferedAmount=transfered_amount,
                    comment=comment or "",
                    atmosphere=atmosphere,
                )
            )
            prod.steps.append(step_obj)

            f.seek(0); f.truncate()
            json.dump(doc.to_dict(), f, indent=2, ensure_ascii=False)
            f.flush()
        return f"Added Transfer step to product CCDC {product_ccdc_number}"
    except Exception as e:
        log.exception("add_step_transfer failed")
        return f"Error adding Transfer step: {str(e)}"


# ----------------------------
# MCP tools: info and output
# ----------------------------
@mcp.tool(name="get_step_summary", description="Get a summary of the current Step object structure.", tags=["step_info"])
@mcp_tool_logger
def get_step_summary_tool(task_name: str) -> str:
    try:
        doc = load_existing_step_doc(task_name)
        header = [f"Step object summary for task: {task_name}", ""]
        return "\n".join(header) + summarize_document(doc)
    except Exception as e:
        log.exception("get_step_summary failed")
        return f"Error getting Step summary: {str(e)}"


@mcp.tool(name="mops_step_output", description="Output the final Step structure to a file.", tags=["mops_step_output"])
@mcp_tool_logger
def mops_step_output_tool(task_name: str) -> str:
    try:
        doc = load_existing_step_doc(task_name)
        output_path = get_step_file_path(task_name)
        atomic_write_json(output_path, doc.to_dict())
        return f"Final Step structure output to {output_path}"
    except Exception as e:
        log.exception("mops_step_output failed")
        return f"Error outputting Step structure: {str(e)}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
