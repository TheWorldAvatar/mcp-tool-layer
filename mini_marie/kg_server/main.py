"""
MOPs Knowledge Graph MCP Server

FastMCP server providing tools to query MOPs synthesis knowledge graph.
Uses SPARQL queries from kg_operations.py
"""

from fastmcp import FastMCP
import logging
import sys
from pathlib import Path
from functools import wraps
import threading

# Import all operations
from mini_marie.kg_server.kg_operations import (
    lookup_synthesis_iri,
    lookup_mop_iri,
    lookup_by_ccdc,
    get_all_mops,
    get_kg_statistics,
    get_synthesis_recipe,
    get_synthesis_steps,
    get_synthesis_temperatures,
    get_synthesis_temperatures_ordered,
    get_synthesis_durations,
    get_synthesis_durations_ordered,
    get_synthesis_products,
    get_mop_building_units,
    get_common_chemicals,
    get_synthesis_step_index,
    get_synthesis_step_temperatures,
    get_synthesis_step_temperature_rates,
    get_synthesis_step_transferred_amounts,
    get_synthesis_step_vessels,
    # Characterisation
    list_characterisation_species,
    list_syntheses_with_characterisation,
    list_characterisation_devices,
    get_characterisation_for_synthesis,
    get_characterisation_by_ccdc,
    get_common_ir_materials,
    warm_label_index,
    fuzzy_lookup_synthesis_name,
    fuzzy_lookup_mop_name,
    fuzzy_lookup_chemical_name,
    fuzzy_lookup_ir_material,
    get_syntheses_producing_mop,
    find_mops_by_cbu_formula_contains,
    get_synthesis_document_context,
    get_synthesis_inheritance,
    get_synthesis_yield,
    get_synthesis_equipment,
    get_synthesis_step_parameters,
    get_synthesis_step_vessel_environments,
    get_synthesis_drying_conditions,
    get_synthesis_evaporation_conditions,
    get_synthesis_separation_solvents,
    get_hnmr_for_synthesis,
    get_common_hnmr_solvents,
    format_results_as_tsv,
)

# Set up logging
def setup_mops_kg_logger():
    """Set up a dedicated logger for MOPs KG MCP server."""
    logger = logging.getLogger("mops_kg_mcp")
    logger.setLevel(logging.INFO)
    
    # Prevent duplicate handlers
    if logger.handlers:
        return logger
    
    # Formatter
    formatter = logging.Formatter(
        '[%(asctime)s] [%(name)s] [%(funcName)s:%(lineno)d] %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Console handler - only show WARNING and above
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    logger.info("MOPs KG MCP Server initialized")
    return logger

logger = setup_mops_kg_logger()

# Custom decorator for logging tool calls
def mops_kg_tool_logger(func):
    """Decorator to log MOPs KG tool calls."""
    import asyncio
    
    if asyncio.iscoroutinefunction(func):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            tool_name = func.__name__
            logger.info(f"=== MOPs KG Tool Call (ASYNC): {tool_name} ===")
            logger.info(f"Arguments: args={args}, kwargs={kwargs}")
            
            try:
                result = await func(*args, **kwargs)
                result_preview = result[:500] if isinstance(result, str) and len(result) > 500 else result
                logger.info(f"Result preview: {result_preview}")
                logger.info(f"=== MOPs KG Tool Call Complete: {tool_name} ===")
                
                for handler in logger.handlers:
                    handler.flush()
                
                return result
            except Exception as e:
                logger.error(f"=== MOPs KG Tool Call Failed: {tool_name} ===")
                logger.error(f"Error: {str(e)}", exc_info=True)
                
                for handler in logger.handlers:
                    handler.flush()
                
                raise
        
        return async_wrapper
    else:
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            tool_name = func.__name__
            logger.info(f"=== MOPs KG Tool Call (SYNC): {tool_name} ===")
            logger.info(f"Arguments: args={args}, kwargs={kwargs}")
            
            try:
                result = func(*args, **kwargs)
                result_preview = result[:500] if isinstance(result, str) and len(result) > 500 else result
                logger.info(f"Result preview: {result_preview}")
                logger.info(f"=== MOPs KG Tool Call Complete: {tool_name} ===")
                
                for handler in logger.handlers:
                    handler.flush()
                
                return result
            except Exception as e:
                logger.error(f"=== MOPs KG Tool Call Failed: {tool_name} ===")
                logger.error(f"Error: {str(e)}", exc_info=True)
                
                for handler in logger.handlers:
                    handler.flush()
                
                raise
        
        return sync_wrapper

# ============================================================================
# MCP Server Setup
# ============================================================================

mcp = FastMCP(name="mops-kg")

# Warm the local fuzzy index automatically on server startup (no tool call needed).
# This runs in a background thread so the MCP server can accept requests immediately.
def _warmup_index_background():
    try:
        warm_label_index(force=False)
        logger.warning("Local fuzzy index warmed and cached")
    except Exception as e:
        logger.error(f"Failed to warm local fuzzy index: {e}", exc_info=True)

threading.Thread(target=_warmup_index_background, daemon=True).start()

@mcp.prompt(name="instruction")
def instruction_prompt():
    return (
        "You have access to a comprehensive MOPs (Metal-Organic Polyhedra) knowledge graph with 30 research papers.\n\n"
        "**Available Tools:**\n"
        "1. lookup_synthesis_by_name - Find synthesis procedure by name (e.g., 'VMOP-17', 'UMC-1')\n"
        "2. lookup_mop_by_name - Find MOP by name (e.g., 'CIAC-105', 'Cage ZrT-1')\n"
        "3. lookup_mop_by_ccdc - Find MOP by CCDC number\n"
        "4. get_all_mops - List all MOPs in the knowledge graph\n"
        "5. get_synthesis_recipe - Get complete recipe (chemicals, amounts) for a synthesis\n"
        "6. get_synthesis_steps - Get step-by-step procedure\n"
        "7. get_synthesis_temperatures - Get temperature conditions (optionally ordered)\n"
        "8. get_synthesis_durations - Get time/duration requirements (optionally ordered)\n"
        "9. get_synthesis_products - Get MOP products from a synthesis\n"
        "10. get_mop_building_units - Get chemical building units (CBUs) for a MOP\n"
        "11. get_common_chemicals - Get most frequently used chemicals across corpus\n"
        "12. get_kg_statistics - Get overall statistics (counts of MOPs, syntheses, etc.)\n\n"
        "**Atomic Step Tools:**\n"
        "13. get_synthesis_step_index - Step IRIs/labels/order/type for a synthesis\n"
        "14. get_synthesis_step_temperatures_atomic - Step-level temperatures (target/crystallization/any) with optional ordering\n"
        "15. get_synthesis_step_temperature_rates - Step-level heating/cooling rates with optional ordering\n"
        "16. get_synthesis_step_transferred_amounts - Step-level transferred amounts with optional ordering\n"
        "17. get_synthesis_step_vessels - Step-level vessel name/type/environment\n\n"
        "**Characterisation Tools:**\n"
        "18. list_characterisation_species - List species that have characterisation sessions\n"
        "19. list_syntheses_with_characterisation - List syntheses that have characterised species outputs\n"
        "20. list_characterisation_devices - List characterisation devices (EA/HNMR/IR)\n"
        "21. get_characterisation_for_synthesis - Characterisation summary for a synthesis (order + order_by supported)\n"
        "22. get_characterisation_by_ccdc - Characterisation summary for a CCDC number\n\n"
        "**Characterisation Corpus Stats:**\n"
        "23. get_common_ir_materials - Most common IR spectroscopy materials (limit, order)\n\n"
        "**Local Fuzzy Lookup (no SPARQL):**\n"
        "24. fuzzy_lookup_synthesis_name - Fuzzy search synthesis names locally\n"
        "25. fuzzy_lookup_mop_name - Fuzzy search MOP names locally\n"
        "26. fuzzy_lookup_chemical_name - Fuzzy search chemical names locally\n"
        "27. fuzzy_lookup_ir_material - Fuzzy search IR material names locally\n"
        "28. refresh_local_fuzzy_index - Rebuild local fuzzy index cache\n\n"
        "**Bridging Tools (MOP ⇄ Synthesis):**\n"
        "29. get_syntheses_producing_mop - Given a MOP name, list synthesis procedure labels that produce it\n"
        "30. find_mops_by_cbu_formula_contains - Find MOPs whose CBU formula contains a substring (e.g., 'Zr')\n\n"
        "**Provenance / Equipment / Yield (OntoSyn):**\n"
        "31. get_synthesis_document_context - Where in the paper the procedure is described (if present)\n"
        "32. get_synthesis_inheritance - Procedure inheritance links (inherits_from / inherited_by)\n"
        "33. get_synthesis_yield - Yield value(s) if present\n"
        "34. get_synthesis_equipment - Process equipment used (global + per-step)\n"
        "35. get_synthesis_step_parameters - Free-text step parameters (if present)\n"
        "36. get_synthesis_step_vessel_environments - Atmosphere per step (explicit only)\n"
        "37. get_synthesis_drying_conditions - Dry step conditions (temp/pressure/agent)\n"
        "38. get_synthesis_evaporation_conditions - Evaporate step conditions (temp/pressure/target volume/removed species)\n"
        "39. get_synthesis_separation_solvents - Separate step solvents\n\n"
        "**Characterisation extras (OntoSpecies):**\n"
        "40. get_hnmr_for_synthesis - HNMR shifts/solvent/temperature for a synthesis output\n"
        "41. get_common_hnmr_solvents - Most common HNMR solvents across corpus\n\n"
        "**Query Logic:**\n"
        "The knowledge graph follows: Synthesis → Chemical Output → MOP\n"
        "Always start from synthesis when looking for relationships.\n\n"
        "**Important Notes:**\n"
        "- Entity names are case-sensitive\n"
        "- Use lookup functions first to verify entity exists\n"
        "- All queries filter out placeholder MOPs automatically\n"
        "- Results are in TSV format for easy parsing\n"
    )

# ============================================================================
# Lookup Tools
# ============================================================================

@mops_kg_tool_logger
@mcp.tool(name="lookup_synthesis_by_name", description="Find synthesis IRI and verify existence by name")
async def lookup_synthesis_by_name(name: str) -> str:
    """Find synthesis IRI by its label/name."""
    results = lookup_synthesis_iri(name)
    if not results:
        return f"No synthesis found with name: {name}"
    return format_results_as_tsv(results)

@mops_kg_tool_logger
@mcp.tool(name="lookup_mop_by_name", description="Find MOP IRI and basic info by name")
async def lookup_mop_by_name(name: str) -> str:
    """Find MOP IRI by its label/name."""
    results = lookup_mop_iri(name)
    if not results:
        return f"No MOP found with name: {name}"
    return format_results_as_tsv(results)

@mops_kg_tool_logger
@mcp.tool(name="lookup_mop_by_ccdc", description="Find MOP by CCDC number")
async def lookup_mop_by_ccdc(ccdc_number: str) -> str:
    """Find MOPs by CCDC number."""
    results = lookup_by_ccdc(ccdc_number)
    if not results:
        return f"No MOP found with CCDC number: {ccdc_number}"
    return format_results_as_tsv(results)

# ============================================================================
# General Query Tools
# ============================================================================

@mops_kg_tool_logger
@mcp.tool(name="get_all_mops", description="Get all MOPs with CCDC numbers and formulas")
async def get_all_mops_tool(limit: int = 100) -> str:
    """Get all MOPs with their CCDC numbers and formulas."""
    results = get_all_mops(limit=limit)
    return format_results_as_tsv(results)

@mops_kg_tool_logger
@mcp.tool(name="get_kg_statistics", description="Get overall knowledge graph statistics")
async def get_kg_statistics_tool() -> str:
    """Get overall statistics about the knowledge graph."""
    stats = get_kg_statistics()
    stats_list = [{"metric": k, "value": str(v)} for k, v in stats.items()]
    return format_results_as_tsv(stats_list)

# ============================================================================
# Synthesis Query Tools
# ============================================================================

@mops_kg_tool_logger
@mcp.tool(name="get_synthesis_recipe", description="Get complete recipe (chemicals, amounts, suppliers) for a synthesis")
async def get_synthesis_recipe_tool(synthesis_name: str) -> str:
    """Get the complete recipe (chemical inputs) for a synthesis."""
    results = get_synthesis_recipe(synthesis_name)
    if not results:
        return f"No recipe found for synthesis: {synthesis_name}"
    return format_results_as_tsv(results)

@mops_kg_tool_logger
@mcp.tool(name="get_synthesis_steps", description="Get all synthesis steps and their types for a procedure")
async def get_synthesis_steps_tool(synthesis_name: str) -> str:
    """Get all synthesis steps for a procedure."""
    results = get_synthesis_steps(synthesis_name)
    if not results:
        return f"No steps found for synthesis: {synthesis_name}"
    return format_results_as_tsv(results)

@mops_kg_tool_logger
@mcp.tool(name="get_synthesis_temperatures", description="Get temperature conditions for a synthesis (order: asc|desc|none)")
async def get_synthesis_temperatures_tool(synthesis_name: str, order: str = "asc") -> str:
    """Get temperature conditions for a synthesis."""
    # Keep backward compat: default order matches previous behavior (ascending)
    results = get_synthesis_temperatures_ordered(synthesis_name, order=order)
    if not results:
        return f"No temperature data found for synthesis: {synthesis_name}"
    return format_results_as_tsv(results)

@mops_kg_tool_logger
@mcp.tool(name="get_synthesis_durations", description="Get duration/time conditions for synthesis steps (order: asc|desc|none)")
async def get_synthesis_durations_tool(synthesis_name: str, order: str = "asc") -> str:
    """Get duration conditions for a synthesis."""
    results = get_synthesis_durations_ordered(synthesis_name, order=order)
    if not results:
        return f"No duration data found for synthesis: {synthesis_name}"
    return format_results_as_tsv(results)


# ============================================================================
# Atomic Step Query Tools
# ============================================================================

@mops_kg_tool_logger
@mcp.tool(name="get_synthesis_step_index", description="Get step IRIs, labels, optional order and types for a synthesis")
async def get_synthesis_step_index_tool(synthesis_name: str) -> str:
    results = get_synthesis_step_index(synthesis_name)
    if not results:
        return f"No steps found for synthesis: {synthesis_name}"
    return format_results_as_tsv(results)


@mops_kg_tool_logger
@mcp.tool(
    name="get_synthesis_step_temperatures_atomic",
    description="Get step-level temperatures. temperature_kind: target|crystallization|any. order: asc|desc|none",
)
async def get_synthesis_step_temperatures_atomic_tool(
    synthesis_name: str,
    temperature_kind: str = "any",
    order: str = "asc",
) -> str:
    results = get_synthesis_step_temperatures(synthesis_name, temperature_kind=temperature_kind, order=order)
    if not results:
        return f"No step temperature data found for synthesis: {synthesis_name}"
    return format_results_as_tsv(results)


@mops_kg_tool_logger
@mcp.tool(
    name="get_synthesis_step_temperature_rates",
    description="Get step-level heating/cooling rates (order: asc|desc|none)",
)
async def get_synthesis_step_temperature_rates_tool(
    synthesis_name: str,
    order: str = "asc",
) -> str:
    results = get_synthesis_step_temperature_rates(synthesis_name, order=order)
    if not results:
        return f"No step temperature-rate data found for synthesis: {synthesis_name}"
    return format_results_as_tsv(results)


@mops_kg_tool_logger
@mcp.tool(
    name="get_synthesis_step_transferred_amounts",
    description="Get step-level transferred amounts (order: asc|desc|none)",
)
async def get_synthesis_step_transferred_amounts_tool(
    synthesis_name: str,
    order: str = "asc",
) -> str:
    results = get_synthesis_step_transferred_amounts(synthesis_name, order=order)
    if not results:
        return f"No step transferred-amount data found for synthesis: {synthesis_name}"
    return format_results_as_tsv(results)


@mops_kg_tool_logger
@mcp.tool(
    name="get_synthesis_step_vessels",
    description="Get step-level vessel name/type/environment where available",
)
async def get_synthesis_step_vessels_tool(synthesis_name: str) -> str:
    results = get_synthesis_step_vessels(synthesis_name)
    if not results:
        return f"No step vessel data found for synthesis: {synthesis_name}"
    return format_results_as_tsv(results)


# ============================================================================
# Characterisation Tools (OntoSpecies)
# ============================================================================

@mops_kg_tool_logger
@mcp.tool(
    name="list_characterisation_species",
    description="List OntoSpecies species that have characterisation sessions (limit, order: asc|desc|none)",
)
async def list_characterisation_species_tool(limit: int = 100, order: str = "asc") -> str:
    results = list_characterisation_species(limit=limit, order=order)
    if not results:
        return "No characterised species found"
    return format_results_as_tsv(results)


@mops_kg_tool_logger
@mcp.tool(
    name="list_syntheses_with_characterisation",
    description="List syntheses that have characterised species outputs (limit, order: asc|desc|none)",
)
async def list_syntheses_with_characterisation_tool(limit: int = 100, order: str = "asc") -> str:
    results = list_syntheses_with_characterisation(limit=limit, order=order)
    if not results:
        return "No syntheses with characterisation found"
    return format_results_as_tsv(results)


@mops_kg_tool_logger
@mcp.tool(
    name="list_characterisation_devices",
    description="List characterisation devices discovered under characterisation sessions (limit)",
)
async def list_characterisation_devices_tool(limit: int = 100) -> str:
    results = list_characterisation_devices(limit=limit)
    if not results:
        return "No characterisation devices found"
    return format_results_as_tsv(results)


@mops_kg_tool_logger
@mcp.tool(
    name="get_characterisation_for_synthesis",
    description="Get characterisation summary per species output of a synthesis (order: asc|desc|none; order_by: speciesLabel|ccdcVal|wpExp|wpCalc)",
)
async def get_characterisation_for_synthesis_tool(
    synthesis_name: str,
    order: str = "none",
    order_by: str = "speciesLabel",
) -> str:
    results = get_characterisation_for_synthesis(synthesis_name, order=order, order_by=order_by)
    if not results:
        return f"No characterisation found for synthesis: {synthesis_name}"
    return format_results_as_tsv(results)


@mops_kg_tool_logger
@mcp.tool(
    name="get_characterisation_by_ccdc",
    description="Get characterisation summary for a CCDC number (order: asc|desc|none)",
)
async def get_characterisation_by_ccdc_tool(ccdc_number: str, order: str = "none") -> str:
    results = get_characterisation_by_ccdc(ccdc_number, order=order)
    if not results:
        return f"No characterisation found for CCDC number: {ccdc_number}"
    return format_results_as_tsv(results)


@mops_kg_tool_logger
@mcp.tool(
    name="get_common_ir_materials",
    description="Get most commonly used materials for IR spectroscopy across the corpus (limit, order: asc|desc|none)",
)
async def get_common_ir_materials_tool(limit: int = 20, order: str = "desc") -> str:
    results = get_common_ir_materials(limit=limit, order=order)
    if not results:
        return "No IR spectroscopy material usage found"
    return format_results_as_tsv(results)


# ============================================================================
# Local fuzzy lookup tools (backed by on-disk cached lists; no SPARQL)
# ============================================================================

@mops_kg_tool_logger
@mcp.tool(
    name="refresh_local_fuzzy_index",
    description="Rebuild the local fuzzy index cache from the KG (force=true).",
)
async def refresh_local_fuzzy_index_tool() -> str:
    warm_label_index(force=True)
    return "Local fuzzy index refreshed"


@mops_kg_tool_logger
@mcp.tool(
    name="fuzzy_lookup_synthesis_name",
    description="Fuzzy search synthesis names locally (limit, cutoff 0-1).",
)
async def fuzzy_lookup_synthesis_name_tool(query: str, limit: int = 10, cutoff: float = 0.6) -> str:
    results = fuzzy_lookup_synthesis_name(query, limit=limit, cutoff=cutoff)
    if not results:
        return "No close matches found"
    return format_results_as_tsv(results)


@mops_kg_tool_logger
@mcp.tool(
    name="fuzzy_lookup_mop_name",
    description="Fuzzy search MOP names locally (limit, cutoff 0-1).",
)
async def fuzzy_lookup_mop_name_tool(query: str, limit: int = 10, cutoff: float = 0.6) -> str:
    results = fuzzy_lookup_mop_name(query, limit=limit, cutoff=cutoff)
    if not results:
        return "No close matches found"
    return format_results_as_tsv(results)


@mops_kg_tool_logger
@mcp.tool(
    name="fuzzy_lookup_chemical_name",
    description="Fuzzy search chemical input names locally (limit, cutoff 0-1).",
)
async def fuzzy_lookup_chemical_name_tool(query: str, limit: int = 10, cutoff: float = 0.6) -> str:
    results = fuzzy_lookup_chemical_name(query, limit=limit, cutoff=cutoff)
    if not results:
        return "No close matches found"
    return format_results_as_tsv(results)


@mops_kg_tool_logger
@mcp.tool(
    name="fuzzy_lookup_ir_material",
    description="Fuzzy search IR material names locally (limit, cutoff 0-1).",
)
async def fuzzy_lookup_ir_material_tool(query: str, limit: int = 10, cutoff: float = 0.6) -> str:
    results = fuzzy_lookup_ir_material(query, limit=limit, cutoff=cutoff)
    if not results:
        return "No close matches found"
    return format_results_as_tsv(results)


@mops_kg_tool_logger
@mcp.tool(
    name="get_syntheses_producing_mop",
    description="Given a MOP name (label), list synthesis labels that produce it.",
)
async def get_syntheses_producing_mop_tool(mop_name: str) -> str:
    results = get_syntheses_producing_mop(mop_name)
    if not results:
        return f"No syntheses found producing MOP: {mop_name}"
    return format_results_as_tsv(results)


@mops_kg_tool_logger
@mcp.tool(
    name="find_mops_by_cbu_formula_contains",
    description="Find MOPs by whether any CBU formula contains a substring (e.g., 'Zr'). Params: substring, limit, order.",
)
async def find_mops_by_cbu_formula_contains_tool(
    substring: str,
    limit: int = 100,
    order: str = "asc",
) -> str:
    results = find_mops_by_cbu_formula_contains(substring=substring, limit=limit, order=order)
    if not results:
        return f"No MOPs found with CBU formula containing: {substring}"
    return format_results_as_tsv(results)


@mops_kg_tool_logger
@mcp.tool(name="get_synthesis_document_context", description="Get document context (section/anchor) for a synthesis")
async def get_synthesis_document_context_tool(synthesis_name: str) -> str:
    results = get_synthesis_document_context(synthesis_name)
    if not results:
        return f"No document context found for synthesis: {synthesis_name}"
    return format_results_as_tsv(results)


@mops_kg_tool_logger
@mcp.tool(name="get_synthesis_inheritance", description="Get inheritance links for a synthesis (inherits_from / inherited_by)")
async def get_synthesis_inheritance_tool(synthesis_name: str) -> str:
    results = get_synthesis_inheritance(synthesis_name)
    if not results:
        return f"No inheritance links found for synthesis: {synthesis_name}"
    return format_results_as_tsv(results)


@mops_kg_tool_logger
@mcp.tool(name="get_synthesis_yield", description="Get yield value(s) for a synthesis if present")
async def get_synthesis_yield_tool(synthesis_name: str) -> str:
    results = get_synthesis_yield(synthesis_name)
    if not results:
        return f"No yield found for synthesis: {synthesis_name}"
    return format_results_as_tsv(results)


@mops_kg_tool_logger
@mcp.tool(name="get_synthesis_equipment", description="Get process equipment used (global + per-step)")
async def get_synthesis_equipment_tool(synthesis_name: str) -> str:
    results = get_synthesis_equipment(synthesis_name)
    if not results:
        return f"No equipment found for synthesis: {synthesis_name}"
    return format_results_as_tsv(results)


@mops_kg_tool_logger
@mcp.tool(name="get_synthesis_step_parameters", description="Get free-text step parameters (ontosyn:hasParameter)")
async def get_synthesis_step_parameters_tool(synthesis_name: str) -> str:
    results = get_synthesis_step_parameters(synthesis_name)
    if not results:
        return f"No step parameters found for synthesis: {synthesis_name}"
    return format_results_as_tsv(results)


@mops_kg_tool_logger
@mcp.tool(name="get_synthesis_step_vessel_environments", description="Get atmosphere/vessel environment per step (explicit only)")
async def get_synthesis_step_vessel_environments_tool(synthesis_name: str) -> str:
    results = get_synthesis_step_vessel_environments(synthesis_name)
    if not results:
        return f"No vessel environment data found for synthesis: {synthesis_name}"
    return format_results_as_tsv(results)


@mops_kg_tool_logger
@mcp.tool(name="get_synthesis_drying_conditions", description="Get Dry step conditions (temp/pressure/agent)")
async def get_synthesis_drying_conditions_tool(synthesis_name: str) -> str:
    results = get_synthesis_drying_conditions(synthesis_name)
    if not results:
        return f"No drying conditions found for synthesis: {synthesis_name}"
    return format_results_as_tsv(results)


@mops_kg_tool_logger
@mcp.tool(name="get_synthesis_evaporation_conditions", description="Get Evaporate step conditions (temp/pressure/target volume/removed species)")
async def get_synthesis_evaporation_conditions_tool(synthesis_name: str) -> str:
    results = get_synthesis_evaporation_conditions(synthesis_name)
    if not results:
        return f"No evaporation conditions found for synthesis: {synthesis_name}"
    return format_results_as_tsv(results)


@mops_kg_tool_logger
@mcp.tool(name="get_synthesis_separation_solvents", description="Get Separate step solvents (extraction/phase separation media)")
async def get_synthesis_separation_solvents_tool(synthesis_name: str) -> str:
    results = get_synthesis_separation_solvents(synthesis_name)
    if not results:
        return f"No separation solvents found for synthesis: {synthesis_name}"
    return format_results_as_tsv(results)


@mops_kg_tool_logger
@mcp.tool(name="get_hnmr_for_synthesis", description="HNMR shifts/solvent/temperature for species outputs of a synthesis")
async def get_hnmr_for_synthesis_tool(synthesis_name: str) -> str:
    results = get_hnmr_for_synthesis(synthesis_name)
    if not results:
        return f"No HNMR data found for synthesis: {synthesis_name}"
    return format_results_as_tsv(results)


@mops_kg_tool_logger
@mcp.tool(name="get_common_hnmr_solvents", description="Most common HNMR solvents across the corpus (limit, order)")
async def get_common_hnmr_solvents_tool(limit: int = 20, order: str = "desc") -> str:
    results = get_common_hnmr_solvents(limit=limit, order=order)
    if not results:
        return "No HNMR solvent usage found"
    return format_results_as_tsv(results)

@mops_kg_tool_logger
@mcp.tool(name="get_synthesis_products", description="Get MOP products from a synthesis (Synthesis → Output → MOP)")
async def get_synthesis_products_tool(synthesis_name: str) -> str:
    """Get MOP products from a synthesis."""
    results = get_synthesis_products(synthesis_name)
    if not results:
        return f"No MOP products found for synthesis: {synthesis_name}"
    return format_results_as_tsv(results)

# ============================================================================
# MOP Query Tools
# ============================================================================

@mops_kg_tool_logger
@mcp.tool(name="get_mop_building_units", description="Get chemical building units (CBUs) for a specific MOP")
async def get_mop_building_units_tool(mop_name: str) -> str:
    """Get chemical building units for a specific MOP."""
    results = get_mop_building_units(mop_name)
    if not results:
        return f"No CBU data found for MOP: {mop_name}"
    return format_results_as_tsv(results)

# ============================================================================
# Corpus-Wide Query Tools
# ============================================================================

@mops_kg_tool_logger
@mcp.tool(name="get_common_chemicals", description="Get most commonly used chemicals across all syntheses")
async def get_common_chemicals_tool(limit: int = 20) -> str:
    """Get most commonly used chemicals across all syntheses."""
    results = get_common_chemicals(limit=limit)
    return format_results_as_tsv(results)

if __name__ == "__main__":
    mcp.run(transport="stdio")

