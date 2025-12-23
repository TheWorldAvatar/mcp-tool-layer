import sys
import os
import argparse
import logging
from pathlib import Path
from datetime import datetime
from functools import wraps
import importlib.util
from typing import Any, Dict, List, Set, Optional, Union

from fastmcp import FastMCP
from models.locations import DATA_LOG_DIR

# =========================
# Logger Setup
# =========================

def setup_ontospecies_logger():
    """Set up a dedicated logger for OntoSpecies MCP server with its own log file."""
    log_dir = Path(DATA_LOG_DIR)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "ontospecies_mcp.log"

    logger = logging.getLogger("ontospecies_mcp_server")
    logger.setLevel(logging.INFO)

    # Prevent duplicate handlers
    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        '[%(asctime)s] [%(name)s] [%(funcName)s:%(lineno)d] %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    logger.info(f"OntoSpecies MCP Server logger initialized. Log file: {log_file}")
    return logger

logger = setup_ontospecies_logger()

# =========================
# Decorator for tool logging
# =========================

def ontospecies_tool_logger(func):
    """Decorator to log OntoSpecies MCP tool calls to dedicated log file. Supports both sync and async functions."""
    import asyncio

    if asyncio.iscoroutinefunction(func):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            tool_name = func.__name__
            logger.info(f"=== OntoSpecies Tool Call (ASYNC): {tool_name} ===")
            logger.info(f"Arguments: args={args}, kwargs={kwargs}")
            print(f"[OntoSpecies LOG] Tool: {tool_name}, Args: {args}, Kwargs: {kwargs}", file=sys.stderr)
            try:
                result = await func(*args, **kwargs)
                result_preview = str(result)[:500] if isinstance(result, str) and len(result) > 500 else result
                logger.info(f"Result preview: {result_preview}")
                logger.info(f"=== OntoSpecies Tool Call Complete: {tool_name} ===")
                for handler in logger.handlers:
                    handler.flush()
                print(f"[OntoSpecies LOG] Tool {tool_name} completed successfully", file=sys.stderr)
                return result
            except Exception as e:
                logger.error(f"=== OntoSpecies Tool Call Failed: {tool_name} ===")
                logger.error(f"Error: {str(e)}", exc_info=True)
                for handler in logger.handlers:
                    handler.flush()
                print(f"[OntoSpecies LOG] Tool {tool_name} failed: {str(e)}", file=sys.stderr)
                raise
        return async_wrapper
    else:
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            tool_name = func.__name__
            logger.info(f"=== OntoSpecies Tool Call (SYNC): {tool_name} ===")
            logger.info(f"Arguments: args={args}, kwargs={kwargs}")
            print(f"[OntoSpecies LOG] Tool: {tool_name}, Args: {args}, Kwargs: {kwargs}", file=sys.stderr)
            try:
                result = func(*args, **kwargs)
                result_preview = str(result)[:500] if isinstance(result, str) and len(result) > 500 else result
                logger.info(f"Result preview: {result_preview}")
                logger.info(f"=== OntoSpecies Tool Call Complete: {tool_name} ===")
                for handler in logger.handlers:
                    handler.flush()
                print(f"[OntoSpecies LOG] Tool {tool_name} completed successfully", file=sys.stderr)
                return result
            except Exception as e:
                logger.error(f"=== OntoSpecies Tool Call Failed: {tool_name} ===")
                logger.error(f"Error: {str(e)}", exc_info=True)
                for handler in logger.handlers:
                    handler.flush()
                print(f"[OntoSpecies LOG] Tool {tool_name} failed: {str(e)}", file=sys.stderr)
                raise
        return sync_wrapper

# =========================
# MCP Server Setup
# =========================

mcp = FastMCP(name="ontospecies")

# =========================
# Script C Loader
# =========================

def load_script_c_module(script_c_path: str):
    """Dynamically load Script C as a module from the given file path."""
    module_name = "script_c"
    spec = importlib.util.spec_from_file_location(module_name, script_c_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load Script C from {script_c_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

# =========================
# MCP Tool Wrappers
# =========================

def make_json_serializable(obj: Any) -> Any:
    """Convert sets to lists, and ensure all outputs are JSON-serializable."""
    if isinstance(obj, set):
        return list(obj)
    elif isinstance(obj, dict):
        return {k: make_json_serializable(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [make_json_serializable(x) for x in obj]
    else:
        return obj

def main():
    parser = argparse.ArgumentParser(description="OntoSpecies MCP Server")
    parser.add_argument("--script-c", required=True, help="Path to Script C module file")
    parser.add_argument("--labels-dir", required=False, help="Override LABELS_DIR in Script C")
    args = parser.parse_args()

    script_c = load_script_c_module(args.script_c)

    # Optionally override LABELS_DIR
    if args.labels_dir:
        if hasattr(script_c, "LABELS_DIR"):
            setattr(script_c, "LABELS_DIR", args.labels_dir)
            logger.info(f"Overriding Script C LABELS_DIR to: {args.labels_dir}")
        else:
            logger.warning("Script C does not define LABELS_DIR; cannot override.")

    # ========== MCP Tool Registration ==========

    @ontospecies_tool_logger
    @mcp.tool(
        name="execute_sparql",
        description="Execute a SPARQL query against the OntoSpecies endpoint. Returns the raw JSON result."
    )
    def execute_sparql(query: str, endpoint: Optional[str] = None, timeout: int = 30) -> Dict[str, Any]:
        """Executes a SPARQL query and returns the JSON-decoded results."""
        if endpoint is None:
            return make_json_serializable(script_c.execute_sparql(query, timeout=timeout))
        return make_json_serializable(script_c.execute_sparql(query, endpoint=endpoint, timeout=timeout))

    @ontospecies_tool_logger
    @mcp.tool(
        name="list_Species",
        description="List Species IRIs and labels. Returns a list of dicts with 'iri' and 'label'."
    )
    def list_Species(limit: int = 20, order: str = "label") -> List[Dict[str, Optional[str]]]:
        return make_json_serializable(script_c.list_Species(limit=limit, order=order))

    @ontospecies_tool_logger
    @mcp.tool(
        name="list_Solvent",
        description="List Solvent IRIs and labels. Returns a list of dicts with 'iri' and 'label'."
    )
    def list_Solvent(limit: int = 20, order: str = "label") -> List[Dict[str, Optional[str]]]:
        return make_json_serializable(script_c.list_Solvent(limit=limit, order=order))

    @ontospecies_tool_logger
    @mcp.tool(
        name="list_Element",
        description="List Element IRIs and labels. Returns a list of dicts with 'iri' and 'label'."
    )
    def list_Element(limit: int = 20, order: str = "label") -> List[Dict[str, Optional[str]]]:
        return make_json_serializable(script_c.list_Element(limit=limit, order=order))

    @ontospecies_tool_logger
    @mcp.tool(
        name="lookup_Species_iri_by_label",
        description="Lookup Species IRIs by label (exact, normalized). Returns a list of IRIs."
    )
    def lookup_Species_iri_by_label(label: str) -> List[str]:
        result = script_c.lookup_Species_iri_by_label(label)
        return list(result) if isinstance(result, set) else make_json_serializable(result)

    @ontospecies_tool_logger
    @mcp.tool(
        name="lookup_Solvent_iri_by_label",
        description="Lookup Solvent IRIs by label (exact, normalized). Returns a list of IRIs."
    )
    def lookup_Solvent_iri_by_label(label: str) -> List[str]:
        result = script_c.lookup_Solvent_iri_by_label(label)
        return list(result) if isinstance(result, set) else make_json_serializable(result)

    @ontospecies_tool_logger
    @mcp.tool(
        name="lookup_Element_iri_by_label",
        description="Lookup Element IRIs by label (exact, normalized). Returns a list of IRIs."
    )
    def lookup_Element_iri_by_label(label: str) -> List[str]:
        result = script_c.lookup_Element_iri_by_label(label)
        return list(result) if isinstance(result, set) else make_json_serializable(result)

    @ontospecies_tool_logger
    @mcp.tool(
        name="fuzzy_lookup_Species",
        description="Fuzzy lookup for Species class. Returns a list of dict(label, iri, score)."
    )
    def fuzzy_lookup_Species(query: str, limit: int = 10, cutoff: float = 0.6) -> List[Dict[str, Any]]:
        return make_json_serializable(script_c.fuzzy_lookup_Species(query, limit=limit, cutoff=cutoff))

    @ontospecies_tool_logger
    @mcp.tool(
        name="fuzzy_lookup_Solvent",
        description="Fuzzy lookup for Solvent class. Returns a list of dict(label, iri, score)."
    )
    def fuzzy_lookup_Solvent(query: str, limit: int = 10, cutoff: float = 0.6) -> List[Dict[str, Any]]:
        return make_json_serializable(script_c.fuzzy_lookup_Solvent(query, limit=limit, cutoff=cutoff))

    @ontospecies_tool_logger
    @mcp.tool(
        name="fuzzy_lookup_Element",
        description="Fuzzy lookup for Element class. Returns a list of dict(label, iri, score)."
    )
    def fuzzy_lookup_Element(query: str, limit: int = 10, cutoff: float = 0.6) -> List[Dict[str, Any]]:
        return make_json_serializable(script_c.fuzzy_lookup_Element(query, limit=limit, cutoff=cutoff))

    @ontospecies_tool_logger
    @mcp.tool(
        name="get_Species_property",
        description="Get value(s) of a property for a Species instance. Returns a list of dicts with 'value' and 'type'."
    )
    def get_Species_property(subject_iri: str, property_iri: str) -> List[Dict[str, str]]:
        return make_json_serializable(script_c.get_Species_property(subject_iri, property_iri))

    @ontospecies_tool_logger
    @mcp.tool(
        name="get_Solvent_property",
        description="Get value(s) of a property for a Solvent instance. Returns a list of dicts with 'value' and 'type'."
    )
    def get_Solvent_property(subject_iri: str, property_iri: str) -> List[Dict[str, str]]:
        return make_json_serializable(script_c.get_Solvent_property(subject_iri, property_iri))

    @ontospecies_tool_logger
    @mcp.tool(
        name="get_Element_property",
        description="Get value(s) of a property for an Element instance. Returns a list of dicts with 'value' and 'type'."
    )
    def get_Element_property(subject_iri: str, property_iri: str) -> List[Dict[str, str]]:
        return make_json_serializable(script_c.get_Element_property(subject_iri, property_iri))

    @ontospecies_tool_logger
    @mcp.tool(
        name="fuzzy_lookup_classes",
        description="Returns the set of classLocalName for which fuzzy lookup is available."
    )
    def fuzzy_lookup_classes() -> List[str]:
        result = script_c.fuzzy_lookup_classes()
        return list(result) if isinstance(result, set) else make_json_serializable(result)

    # ========== MCP Server Run ==========
    mcp.run(transport="stdio")

if __name__ == "__main__":
    main()