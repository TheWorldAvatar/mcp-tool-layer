"""
CAS to SMILES conversion using PubChem REST API.

This module provides functionality to convert CAS registry numbers to SMILES strings
by querying PubChem's compound and substance databases.
"""

import json
import time
import requests
from typing import List, Dict, Set, Optional, Tuple
from src.utils.global_logger import get_logger

logger = get_logger("chemistry_operations", "cas_to_smiles")

BASE = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"

# Create a session for connection reuse
S = requests.Session()
S.headers.update({"Accept-Encoding": "gzip, deflate", "User-Agent": "mcp-chemistry/1.0"})

def _get_json(url: str, timeout=15, retries=2) -> Optional[dict]:
    """Helper function to make JSON requests with retries."""
    for i in range(retries + 1):
        try:
            r = S.get(url, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            if r.status_code >= 500:
                time.sleep(0.4 * (i + 1))
                continue
            return None
        except Exception as e:
            logger.warning(f"Request failed for {url}: {e}")
            if i < retries:
                time.sleep(0.4 * (i + 1))
            continue
    return None

def _chunk(seq, n):
    """Split sequence into chunks of size n."""
    seq = list(seq)
    for i in range(0, len(seq), n):
        yield seq[i:i+n]

# ---------- Fast path: COMPOUND domain first ----------
def cids_by_compound_name(name: str) -> List[int]:
    """Get CIDs by searching compound database with name."""
    url = f"{BASE}/compound/name/{requests.utils.quote(name)}/cids/JSON"
    logger.debug(f"Searching compounds for name: {name}")
    data = _get_json(url)
    if not data:
        return []
    cids = data.get("IdentifierList", {}).get("CID", []) or []
    logger.debug(f"Found {len(cids)} CIDs in compound search")
    return cids

def compound_synonyms_for_cids(cids: List[int]) -> Dict[int, Set[str]]:
    """Get synonyms for compound CIDs in batches."""
    out: Dict[int, Set[str]] = {}
    if not cids: 
        return out
    
    for chunk in _chunk(sorted(set(cids)), 200):  # batch
        ids = ",".join(map(str, chunk))
        url = f"{BASE}/compound/cid/{ids}/synonyms/JSON"
        data = _get_json(url)
        if not data: 
            continue
        for info in data.get("InformationList", {}).get("Information", []):
            cid = info.get("CID")
            syns = set(s.strip() for s in info.get("Synonym", []) or [])
            if cid: 
                out[int(cid)] = syns
    
    logger.debug(f"Retrieved synonyms for {len(out)} compounds")
    return out

# ---------- Fallback: SUBSTANCE domain ----------
def sids_by_substance_name(name: str) -> List[int]:
    """Get SIDs by searching substance database with name."""
    url = f"{BASE}/substance/name/{requests.utils.quote(name)}/sids/JSON"
    logger.debug(f"Searching substances for name: {name}")
    data = _get_json(url)
    if not data:
        return []
    sids = data.get("IdentifierList", {}).get("SID", []) or []
    logger.debug(f"Found {len(sids)} SIDs in substance search")
    return sids

def substance_synonyms_for_sids(sids: List[int]) -> Dict[int, Set[str]]:
    """Get synonyms for substance SIDs in batches."""
    out: Dict[int, Set[str]] = {}
    if not sids: 
        return out
    
    for chunk in _chunk(sorted(set(sids)), 200):
        ids = ",".join(map(str, chunk))
        url = f"{BASE}/substance/sid/{ids}/synonyms/JSON"
        data = _get_json(url)
        if not data: 
            continue
        for info in data.get("InformationList", {}).get("Information", []):
            sid = info.get("SID")
            syns = set(s.strip() for s in info.get("Synonym", []) or [])
            if sid: 
                out[int(sid)] = syns
    
    logger.debug(f"Retrieved synonyms for {len(out)} substances")
    return out

def cids_for_sids(sids: List[int]) -> List[int]:
    """Convert SIDs to CIDs in batches."""
    if not sids: 
        return []
    
    all_cids: Set[int] = set()
    for chunk in _chunk(sorted(set(sids)), 200):
        ids = ",".join(map(str, chunk))
        url = f"{BASE}/substance/sid/{ids}/cids/JSON"
        data = _get_json(url)
        if not data: 
            continue
        for info in data.get("InformationList", {}).get("Information", []):
            for cid in info.get("CID", []) or []:
                all_cids.add(int(cid))
    
    result = sorted(all_cids)
    logger.debug(f"Converted {len(sids)} SIDs to {len(result)} unique CIDs")
    return result

# ---------- Final properties ----------
def fetch_cid_properties(cids: List[int]) -> List[dict]:
    """Fetch SMILES and other properties for CIDs in batches."""
    props = []
    if not cids:
        return props
        
    for chunk in _chunk(sorted(set(cids)), 100):
        ids = ",".join(map(str, chunk))
        url = f"{BASE}/compound/cid/{ids}/property/CanonicalSMILES,IsomericSMILES,MolecularFormula,IUPACName/JSON"
        data = _get_json(url)
        if not data: 
            continue
        props.extend(data.get("PropertyTable", {}).get("Properties", []) or [])
    
    logger.debug(f"Retrieved properties for {len(props)} compounds")
    return props

def cas_to_smiles(cas: str) -> dict:
    """
    Convert CAS registry number to SMILES strings using PubChem API.
    
    This function uses a two-stage approach:
    1. First tries the compound database (faster, usually 1-3 API calls)
    2. Falls back to substance database if needed (3-4 API calls)
    
    Args:
        cas: CAS registry number (e.g., "50446-44-1")
        
    Returns:
        Dictionary containing:
        - cas: Input CAS number
        - path: Which search path was used ("compound" or "substance")
        - cids: List of matching compound IDs
        - sids_exact: List of exact-matching substance IDs (substance path only)
        - cid_properties: List of chemical properties including SMILES
        - success: Boolean indicating if any results were found
        - smiles_list: Extracted list of canonical SMILES strings
    """
    logger.info(f"Starting CAS to SMILES conversion for: {cas}")
    
    # 1) Try compound first (usually 1–3 calls total)
    logger.debug("Trying compound database first...")
    cids = cids_by_compound_name(cas)
    if cids:
        syns_map = compound_synonyms_for_cids(cids)
        exact_cids = [cid for cid, syns in syns_map.items() if cas in syns]
        if exact_cids:
            logger.info(f"Found exact match in compound database: {len(exact_cids)} CIDs")
            properties = fetch_cid_properties(exact_cids)
            smiles_list = [prop.get("CanonicalSMILES") for prop in properties if prop.get("CanonicalSMILES")]
            
            return {
                "cas": cas,
                "path": "compound",
                "cids": sorted(exact_cids),
                "cid_properties": properties,
                "success": True,
                "smiles_list": smiles_list
            }
        logger.debug("Compound database had hits but no exact CAS match, trying substance database...")

    # 2) Substance path with batching (≈3–4 calls total)
    logger.debug("Trying substance database...")
    sids = sids_by_substance_name(cas)
    syns_map = substance_synonyms_for_sids(sids)
    exact_sids = [sid for sid, syns in syns_map.items() if cas in syns]
    cids = cids_for_sids(exact_sids)
    properties = fetch_cid_properties(cids)
    smiles_list = [prop.get("CanonicalSMILES") for prop in properties if prop.get("CanonicalSMILES")]
    
    success = len(exact_sids) > 0 or len(cids) > 0
    if success:
        logger.info(f"Found match in substance database: {len(exact_sids)} SIDs → {len(cids)} CIDs")
    else:
        logger.warning(f"No exact matches found for CAS: {cas}")
    
    return {
        "cas": cas,
        "path": "substance",
        "sids_exact": exact_sids,
        "cids": cids,
        "cid_properties": properties,
        "success": success,
        "smiles_list": smiles_list
    }

def example_usage():
    """Example usage with CAS number 50446-44-1."""
    cas_number = "50446-44-1"
    print(f"Example: Converting CAS {cas_number} to SMILES...")
    
    result = cas_to_smiles(cas_number)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    
    if result["success"]:
        print(f"\nFound {len(result['smiles_list'])} SMILES:")
        for i, smiles in enumerate(result['smiles_list'], 1):
            print(f"{i}. {smiles}")
    else:
        print("No SMILES found for this CAS number.")

if __name__ == "__main__":
    example_usage()
