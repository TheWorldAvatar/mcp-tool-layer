from typing import Any, Callable, List, Dict, Optional, TypeVar
import asyncio
import logging
import sys
from pathlib import Path

import pubchempy as pcp
from mcp.server.fastmcp import FastMCP

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.mcp_servers.pubchem.curated_names import curated_name_records
from src.mcp_servers.pubchem.name_dedup import (
    as_record_dict,
    as_record_list,
    finalize_pubchem_list,
    finalize_pubchem_record,
)
from src.mcp_servers.pubchem.retry import (
    call_pubchem,
    env_float,
    env_int,
    lookup_error_record,
)

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

_T = TypeVar("_T")
_PUBCHEM_TIMEOUT_SECONDS = env_float("PUBCHEM_MCP_TIMEOUT_SECONDS", 20.0)
_PUBCHEM_ATTEMPTS = env_int("PUBCHEM_MCP_ATTEMPTS", 5)


def _call_pubchem(label: str, callback: Callable[[], _T]) -> _T | None:
    return call_pubchem(
        label,
        callback,
        timeout_seconds=_PUBCHEM_TIMEOUT_SECONDS,
        attempts=_PUBCHEM_ATTEMPTS,
    )

# Initialize FastMCP server
mcp = FastMCP("pubchem")


@mcp.prompt(name="instruction")
def instruction_prompt() -> str:
    """Provide concise guidance for agents using PubChem."""
    return (
        "Use PubChem tools to verify chemical identity. "
        "Each tool already returns cid, formula, and a compact identity-name list "
        "after junk filtering and LLM collapse. "
        "Copy those names; do not paste raw synonym dumps or expand them again. "
        "If a tool returns ok=false, matched=false, or an error, the lookup is unresolved. "
        "Do not invent lookup values. "
        "Prefer the most specific available query and do not invent missing values."
    )


def compound_to_dict(compound):
    """Convert a PubChem compound to a dictionary with relevant information."""
    if not compound:
        return {}
    
    result = {
        "cid": compound.cid,
        "iupac_name": compound.iupac_name,
        "molecular_formula": compound.molecular_formula,
        "molecular_weight": compound.molecular_weight,
        "canonical_smiles": getattr(compound, "connectivity_smiles", None),
        "isomeric_smiles": getattr(compound, "smiles", None),
        "inchi": compound.inchi,
        "inchikey": compound.inchikey,
        "xlogp": compound.xlogp,
        "exact_mass": compound.exact_mass,
        "monoisotopic_mass": compound.monoisotopic_mass,
        "tpsa": compound.tpsa,
        "complexity": compound.complexity,
        "charge": compound.charge,
        "h_bond_donor_count": compound.h_bond_donor_count,
        "h_bond_acceptor_count": compound.h_bond_acceptor_count,
        "rotatable_bond_count": compound.rotatable_bond_count,
        "heavy_atom_count": compound.heavy_atom_count,
        "atom_stereo_count": compound.atom_stereo_count,
        "defined_atom_stereo_count": compound.defined_atom_stereo_count,
        "undefined_atom_stereo_count": compound.undefined_atom_stereo_count,
        "bond_stereo_count": compound.bond_stereo_count,
        "defined_bond_stereo_count": compound.defined_bond_stereo_count,
        "undefined_bond_stereo_count": compound.undefined_bond_stereo_count,
        "covalent_unit_count": compound.covalent_unit_count,
    }
    
    # Add synonyms if available
    if hasattr(compound, 'synonyms') and compound.synonyms:
        result["synonyms"] = compound.synonyms
    
    return result


def search_by_name(name: str, max_results: int = 5) -> List[Dict[str, Any]]:
    """Search compounds by name. Always a list so FastMCP output validation cannot crash."""
    curated = curated_name_records(name)
    if curated:
        logging.info("Using curated ligand mapping for %r (not in PubChem)", name)
        return curated
    try:
        records = _call_pubchem(
            f"get_compounds(name={name!r})",
            lambda: [
                compound_to_dict(compound)
                for compound in pcp.get_compounds(
                    name, "name", record_type="2d", max_records=max_results
                )
            ],
        )
        return finalize_pubchem_list(records, query=name)
    except Exception as e:
        logging.error("Error searching by name %r: %s", name, e)
        return as_record_list([lookup_error_record(name, e)], query=name)


def search_by_smiles(smiles: str, max_results: int = 5) -> List[Dict[str, Any]]:
    """Search compounds by SMILES."""
    try:
        records = _call_pubchem(
            f"get_compounds(smiles={smiles!r})",
            lambda: [
                compound_to_dict(compound)
                for compound in pcp.get_compounds(
                    smiles, "smiles", record_type="2d", max_records=max_results
                )
            ],
        )
        return finalize_pubchem_list(records, query=smiles)
    except Exception as e:
        logging.error("Error searching by SMILES %r: %s", smiles, e)
        return as_record_list([lookup_error_record(smiles, e)], query=smiles)


def search_by_cid(cid: int) -> Dict[str, Any]:
    """Get compound by CID. Always a dict so FastMCP output validation cannot crash."""
    query = str(cid)
    try:
        record = _call_pubchem(
            f"from_cid({cid})",
            lambda: compound_to_dict(pcp.Compound.from_cid(cid)),
        )
        return finalize_pubchem_record(record, query=query)
    except Exception as e:
        logging.error("Error fetching compound with CID %s: %s", cid, e)
        return as_record_dict(lookup_error_record(query, e), query=query)


@mcp.tool()
async def search_pubchem_by_name(name: str, max_results: int = 5) -> List[Dict[str, Any]]:
    logging.info(f"Searching for compounds with name: {name}, max_results: {max_results}")
    """
    Search for chemical compounds on PubChem using a compound name.

    Args:
        name: Name of the chemical compound
        max_results: Maximum number of results to return (default: 5)

    Returns:
        Slim records with cid, formula, and a deduplicated names list.
    """
    try:
        results = await asyncio.to_thread(search_by_name, name, max_results)
        return as_record_list(results, query=name)
    except Exception as e:
        return as_record_list([lookup_error_record(name, e)], query=name)


@mcp.tool()
async def search_pubchem_by_smiles(smiles: str, max_results: int = 5) -> List[Dict[str, Any]]:
    logging.info(f"Searching for compounds with SMILES: {smiles}, max_results: {max_results}")
    """
    Search for chemical compounds on PubChem using a SMILES string.

    Args:
        smiles: SMILES notation of the chemical compound
        max_results: Maximum number of results to return (default: 5)

    Returns:
        Slim records with cid, formula, and a deduplicated names list.
    """
    try:
        results = await asyncio.to_thread(search_by_smiles, smiles, max_results)
        return as_record_list(results, query=smiles)
    except Exception as e:
        return as_record_list([lookup_error_record(smiles, e)], query=smiles)


@mcp.tool()
async def get_pubchem_compound_by_cid(cid: int) -> Dict[str, Any]:
    logging.info(f"Fetching compound with CID: {cid}")
    """
    Fetch detailed information about a chemical compound using its PubChem CID.

    Args:
        cid: PubChem Compound ID (CID)

    Returns:
        Slim record with cid, formula, and a deduplicated names list.
    """
    query = str(cid)
    try:
        result = await asyncio.to_thread(search_by_cid, cid)
        return as_record_dict(result, query=query)
    except Exception as e:
        return as_record_dict(lookup_error_record(query, e), query=query)


@mcp.tool()
async def search_pubchem_advanced(
    name: Optional[str] = None,
    smiles: Optional[str] = None,
    formula: Optional[str] = None,
    cid: Optional[int] = None,
    max_results: int = 5
) -> List[Dict[str, Any]]:
    logging.info(f"Performing advanced search with parameters: {locals()}")
    """
    Perform an advanced search for compounds on PubChem.

    Args:
        name: Name of the chemical compound
        smiles: SMILES notation of the chemical compound
        formula: Molecular formula
        cid: PubChem Compound ID
        max_results: Maximum number of results to return (default: 5)

    Returns:
        Slim records with cid, formula, and a deduplicated names list.
    """
    query = name or smiles or formula or (str(cid) if cid is not None else "")
    try:
        if cid is not None:
            result = await asyncio.to_thread(search_by_cid, cid)
            return as_record_list([result], query=query)
        if smiles is not None:
            return as_record_list(
                await asyncio.to_thread(search_by_smiles, smiles, max_results),
                query=smiles,
            )
        if name is not None:
            return as_record_list(
                await asyncio.to_thread(search_by_name, name, max_results),
                query=name,
            )
        if formula is not None:
            def _fetch_formula() -> List[Dict[str, Any]]:
                return [
                    compound_to_dict(compound)
                    for compound in pcp.get_compounds(formula, "formula", max_records=max_results)
                ]

            records = await asyncio.to_thread(
                _call_pubchem, f"get_compounds(formula={formula!r})", _fetch_formula
            )
            return finalize_pubchem_list(records, query=formula)
        return [{"error": "At least one search parameter (name, smiles, formula, or cid) must be provided"}]
    except Exception as e:
        return as_record_list([lookup_error_record(query, e)], query=query)

if __name__ == "__main__":
    logging.info("Starting PubChem MCP server")
    # Initialize and run the server
    mcp.run(transport='stdio')
