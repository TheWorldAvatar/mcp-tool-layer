#!/usr/bin/env python3
"""
CCDC operations backed by a local, licensed Cambridge Structural Database installation.

Functions provided:
- search_ccdc_by_mop_name(name: str, exact: bool=False) -> list[tuple[str, str]]
    Search by compound name and return a list of tuples: (CSD refcode, CCDC deposition number).

- get_res_cif_file_by_ccdc(deposition_number: str, out_dir: str) -> dict
    Fetch a single structure by CCDC deposition number and write .res and .cif files into out_dir.
    Returns a dict with paths. Requires exactly one hit and a 3D structure.

Requires ccdc Python package and a valid local CSD license.
"""

from __future__ import annotations

import os
from typing import List, Tuple, Dict
import json
import subprocess
import sys

# Try native CCDC; if unavailable (e.g., running under WSL), fall back to Windows proxy
try:
    from ccdc.search import TextNumericSearch  # type: ignore
    from ccdc.io import CrystalWriter  # type: ignore
    _HAVE_CCDC = True
except Exception:
    TextNumericSearch = None  # type: ignore
    CrystalWriter = None  # type: ignore
    _HAVE_CCDC = False

from models.locations import DATA_CCDC_DIR


# Hardcoded mapping between MOP names and CCDC numbers
# Format: {mop_name_lowercase: ccdc_number}
# This serves as a fallback when CCDC API searches fail or for known mappings
# IMPORTANT: All keys must be lowercase since lookup uses .lower()
HARDCODED_MOP_CCDC_MAPPING = {
    # IRMOP series
    "[me2nh2]5[v6o6(och3)9(so4)4]": "1590347",
    "irmop-50": "273613",
    "irmop-51": "273616",
    "irmop-52": "273620",
    "irmop-53": "273621",
    "mop-54": "273623",
    # VMOP series (both Greek and ASCII variants for robustness)
    "vmop-α": "1590349",
    "vmop-a": "1590349",
    "vmop-β": "1590348",
    "vmop-b": "1590348",
    "vmop-14": "1479720",
    "zrt-1": "950330",
    "zrt-2": "950331",
    "zrt-3": "950332",
    "zrt-4": "950333",
    # MOP series with alkoxy-functionalized isophthalic acids
    "mop-pria": "1497171",
    "mop-eia": "1497172",
    "mop-mia": "1497173",
    # Nickel-seamed pyrogallol[4]arene nanocapsules (JACS 2017, 10.1021_jacs.7b00037)
    "nanocapsule i": "1521975",
    "nanocapsule i [ni24(c40h35o16)6(dmf)2(h2o)40]": "1521975",
    "nanocapsule ii": "1521976",
    "nanocapsule ii [ni24(c40h36o16)6(dmf)4(h2o)24(py)20]": "1521976",
}


def _lookup_hardcoded_ccdc(name: str) -> List[Tuple[str, str]]:
    """Look up CCDC number from hardcoded mapping.
    
    Args:
        name: MOP name (case-insensitive)
        
    Returns:
        List of (refcode, ccdc_number) tuples, or empty list if not found.
        Refcode is set to the normalized name since we don't have actual CSD refcodes.
    """
    normalized = name.strip().lower()
    ccdc = HARDCODED_MOP_CCDC_MAPPING.get(normalized)
    if ccdc:
        # Use normalized name as pseudo-refcode
        return [(normalized.upper().replace(" ", "_"), ccdc)]
    return []

    
def search_ccdc_by_mop_name(name: str, exact: bool = True) -> List[Tuple[str, str]]:
    """Search CCDC by compound name.

    Args:
        name: Compound name to search.
        exact: When True, use exact match; otherwise search 'anywhere'.

    Returns:
        List of (CSD refcode, CCDC deposition number) tuples. Empty if none found.
    """
    # First, try hardcoded mapping (always check this first for reliability)
    hardcoded_results = _lookup_hardcoded_ccdc(name)
    if hardcoded_results:
        print(f"[CCDC] Found hardcoded mapping for '{name}': {hardcoded_results}")
        return hardcoded_results
    
    if _HAVE_CCDC:
        q = TextNumericSearch()
        q.add_compound_name(name, mode='exact' if exact else 'anywhere')
        results: List[Tuple[str, str]] = []
        for hit in q.search():
            refcode = hit.identifier
            num = str(hit.entry.ccdc_number) if hit.entry.ccdc_number else ""
            results.append((refcode, num))
        if results:
            return results
        # If CCDC search returns nothing, try hardcoded again (redundant but safe)
        return hardcoded_results
    # Fallback: call Windows CLI via cmd.exe and conda (with robust conda path fallbacks)
    def _try_windows_invoke(args: list[str]) -> subprocess.CompletedProcess:
        # Resolve Windows-side execution method from environment; fall back to generic conda
        conda_candidates = [
            os.getenv("CONDA_EXE", "conda"),
            os.getenv("CONDA_BAT", ""),
            os.getenv("CONDA_ENV_PY", ""),
        ]
        last_err = None
        for conda_cmd in conda_candidates:
            print(f"[WSL CCDC] Trying candidate: {conda_cmd} with args: {args}")
            if conda_cmd and conda_cmd.lower().endswith("python.exe"):
                # Directly call env python
                cmd = [
                    "/mnt/c/Windows/System32/cmd.exe",
                    "/C",
                    conda_cmd,
                    "-m",
                    "src.mcp_servers.ccdc.operations.windows_ccdc",
                ] + args
            elif conda_cmd:
                cmd = [
                    "/mnt/c/Windows/System32/cmd.exe",
                    "/C",
                    conda_cmd,
                    "run",
                    "-n",
                    os.getenv("CSD_CONDA_ENV", "csd311"),
                    "python",
                    "-m",
                    "src.mcp_servers.ccdc.operations.windows_ccdc",
                ] + args
            else:
                # fallback to plain conda
                cmd = [
                    "/mnt/c/Windows/System32/cmd.exe",
                    "/C",
                    "conda",
                    "run",
                    "-n",
                    os.getenv("CSD_CONDA_ENV", "csd311"),
                    "python",
                    "-m",
                    "src.mcp_servers.ccdc.operations.windows_ccdc",
                ] + args
            print(f"[WSL CCDC] Executing: {' '.join(cmd)}")
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            print(f"[WSL CCDC] Return code: {proc.returncode}")
            print(f"[WSL CCDC] STDOUT ({len(proc.stdout)} bytes):\n{proc.stdout[:500]}")
            print(f"[WSL CCDC] STDERR ({len(proc.stderr)} bytes):\n{proc.stderr[:500]}")
            if proc.returncode == 0:
                if proc.stdout.strip():
                    return proc
                # if no stdout, try next candidate
            last_err = proc
        raise RuntimeError(f"Windows CCDC invocation failed. Last error: {(last_err.stdout or '')}\n{(last_err.stderr or '')}")

    proc = _try_windows_invoke(["search", "--name", name] + (["--exact"] if exact else []))
    raw = (proc.stdout or "").strip()
    # Robustly extract JSON array from mixed stdout
    def _extract_json_array(s: str):
        # try line-wise first
        for line in s.splitlines():
            line = line.strip()
            if line.startswith("[") and line.endswith("]"):
                return json.loads(line)
        # fallback: slice first '[' to last ']'
        i, j = s.find('['), s.rfind(']')
        if i != -1 and j != -1 and j > i:
            return json.loads(s[i:j+1])
        raise ValueError("No JSON array found in stdout")
    try:
        data = _extract_json_array(raw)
        results = [(str(r[0]), str(r[1])) for r in data]
        if results:
            return results
    except Exception as e:
        print(f"[CCDC] Windows CLI search failed: {e}, trying hardcoded mapping")
    
    # If Windows CLI fails or returns empty, fall back to hardcoded
    return hardcoded_results


def _normalize_doi_input(doi_like: str) -> str:
    """Normalize various DOI inputs to the CCDC-acceptable form '10.xxx/yyy'.

    Accepts:
      - pipeline form with underscores: e.g., '10.1021_ic050460z'
      - full URLs: e.g., 'https://doi.org/10.1021/ic050460z'
      - optional leading '@' characters (will be stripped)

    Returns the normalized DOI string with '/'. Raises ValueError if unrecognized.
    """
    if not doi_like:
        raise ValueError("Empty DOI input")
    s = doi_like.strip()
    if s.startswith('@'):
        s = s[1:]
    # Strip URL prefix if present
    lowers = s.lower()
    if lowers.startswith('http://') or lowers.startswith('https://'):
        # Keep part after domain, typically '/10.xxx/...'
        try:
            # Find '/10.' and slice from there
            idx = s.find('/10.')
            if idx == -1:
                raise ValueError("No DOI path found in URL")
            s = s[idx+1:]  # drop leading '/'
        except Exception as e:
            raise ValueError(f"Invalid DOI URL: {doi_like}") from e
    # Convert pipeline underscore form into slash form
    if '_' in s and '/' not in s:
        s = s.replace('_', '/')
    # Basic validation: must look like 10.xxxx/...
    if not (s.startswith('10.') and '/' in s):
        raise ValueError(f"Unrecognized DOI format: {doi_like}")
    return s


def search_ccdc_by_doi(doi_like: str) -> List[Dict[str, str]]:
    """Search CCDC entries by DOI and return detailed metadata.

    Returns list of dicts with keys: refcode, chemical_name, formula, ccdc_number, doi.
    """
    doi = _normalize_doi_input(doi_like)
    if _HAVE_CCDC:
        from ccdc.search import TextNumericSearch  # type: ignore
        from ccdc.io import EntryReader  # type: ignore
        q = TextNumericSearch()
        q.add_doi(doi)
        hits = q.search()
        rows: List[Dict[str, str]] = []
        # Use EntryReader('CSD') to retrieve details
        from ccdc.io import EntryReader  # type: ignore
        reader = EntryReader('CSD')
        try:
            for h in hits:
                refcode = str(h.identifier)
                entry = reader.entry(refcode)
                rows.append({
                    'refcode': entry.identifier,
                    'chemical_name': str(getattr(entry, 'chemical_name', '') or ''),
                    'formula': str(getattr(entry, 'formula', '') or ''),
                    'ccdc_number': str(getattr(entry, 'ccdc_number', '') or ''),
                    'doi': str(entry.publication.doi) if getattr(entry, 'publication', None) else ''
                })
        finally:
            try:
                reader.close()
            except Exception:
                pass
        return rows

    # Fallback to Windows-side CLI
    def _try_windows_invoke(args: list[str]) -> subprocess.CompletedProcess:
        conda_candidates = [
            os.getenv("CONDA_EXE", "conda"),
            os.getenv("CONDA_BAT", ""),
            os.getenv("CONDA_ENV_PY", ""),
        ]
        last_err = None
        for conda_cmd in conda_candidates:
            print(f"[WSL CCDC] Trying candidate: {conda_cmd} with args: {args}")
            if conda_cmd and conda_cmd.lower().endswith("python.exe"):
                cmd = [
                    "/mnt/c/Windows/System32/cmd.exe",
                    "/C",
                    conda_cmd,
                    "-m",
                    "src.mcp_servers.ccdc.operations.windows_ccdc",
                ] + args
            elif conda_cmd:
                cmd = [
                    "/mnt/c/Windows/System32/cmd.exe",
                    "/C",
                    conda_cmd,
                    "run",
                    "-n",
                    os.getenv("CSD_CONDA_ENV", "csd311"),
                    "python",
                    "-m",
                    "src.mcp_servers.ccdc.operations.windows_ccdc",
                ] + args
            else:
                cmd = [
                    "/mnt/c/Windows/System32/cmd.exe",
                    "/C",
                    "conda",
                    "run",
                    "-n",
                    os.getenv("CSD_CONDA_ENV", "csd311"),
                    "python",
                    "-m",
                    "src.mcp_servers.ccdc.operations.windows_ccdc",
                ] + args
            print(f"[WSL CCDC] Executing: {' '.join(cmd)}")
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            print(f"[WSL CCDC] Return code: {proc.returncode}")
            print(f"[WSL CCDC] STDOUT ({len(proc.stdout)} bytes):\n{proc.stdout[:500]}")
            print(f"[WSL CCDC] STDERR ({len(proc.stderr)} bytes):\n{proc.stderr[:500]}")
            if proc.returncode == 0 and proc.stdout.strip():
                return proc
            last_err = proc
        raise RuntimeError(f"Windows CCDC invocation failed. Last error: {(last_err.stdout or '')}\n{(last_err.stderr or '')}")

    proc = _try_windows_invoke(["search_doi", "--doi", _normalize_doi_input(doi_like)])
    raw = (proc.stdout or "").strip()
    # Extract JSON array
    def _extract_json_array(s: str):
        for line in s.splitlines():
            line = line.strip()
            if line.startswith("[") and line.endswith("]"):
                return json.loads(line)
        i, j = s.find('['), s.rfind(']')
        if i != -1 and j != -1 and j > i:
            return json.loads(s[i:j+1])
        raise ValueError("No JSON array found in stdout")
    data = _extract_json_array(raw)
    # Ensure typing as List[Dict[str, str]]
    out: List[Dict[str, str]] = []
    for obj in data:
        out.append({
            'refcode': str(obj.get('refcode', '')),
            'chemical_name': str(obj.get('chemical_name', '')),
            'formula': str(obj.get('formula', '')),
            'ccdc_number': str(obj.get('ccdc_number', '')),
            'doi': str(obj.get('doi', '')),
        })
    return out

def get_res_cif_file_by_ccdc(deposition_number: str) -> Dict[str, str]:
    """Fetch a structure by CCDC deposition number; write .res and .cif.

    Preconditions:
      - deposition_number must be numeric string (e.g., '1955203').
      - CSD must be locally available and licensed.

    Behavior:
      - Performs a numeric CCDC search; requires exactly one hit with a 3D structure.
      - Writes <num>.res and <num>.cif into out_dir.

    Returns:
      dict with keys: 'res', 'cif' (absolute file paths).
      Raises ValueError for validation/search errors.
    """
    if _HAVE_CCDC:
        try:
            n_int = int(deposition_number)
        except ValueError:
            raise ValueError("deposition_number must be an integer string")

        q = TextNumericSearch()
        q.add_ccdc_number(n_int)
        hits = q.search()
        if len(hits) != 1:
            raise ValueError(f"Expected exactly one hit for {n_int}, got {len(hits)}")

        hit = hits[0]
        try:
            entry_num_int = int(hit.entry.ccdc_number)
        except Exception:
            raise ValueError(f"Hit has non-numeric ccdc_number: {hit.entry.ccdc_number}")
        if entry_num_int != n_int:
            raise ValueError(f"CCDC mismatch: requested {n_int}, entry {hit.entry.ccdc_number}")

        if not hit.entry.has_3d_structure:
            raise ValueError(f"Entry {n_int} has no 3D structure")

        # Always write under DATA_CCDC_DIR/res and DATA_CCDC_DIR/cif
        res_dir = os.path.join(DATA_CCDC_DIR, "res")
        cif_dir = os.path.join(DATA_CCDC_DIR, "cif")
        os.makedirs(res_dir, exist_ok=True)
        os.makedirs(cif_dir, exist_ok=True)
        res_path = os.path.abspath(os.path.join(res_dir, f"{n_int}.res"))
        cif_path = os.path.abspath(os.path.join(cif_dir, f"{n_int}.cif"))

        crys = hit.entry.crystal
        with CrystalWriter(res_path) as w:
            w.write(crys)
        with CrystalWriter(cif_path) as w:
            w.write(crys)

        return {"res": res_path, "cif": cif_path}

    # Fallback: call Windows CLI. Use DATA_CCDC_DIR as canonical output root.
    def _to_windows_path(p: str) -> str:
        p_abs = os.path.abspath(p)
        if p_abs.startswith("/mnt/") and len(p_abs) > 6 and p_abs[5] == '/':
            drive = p_abs[5].upper()
            rest = p_abs[6:].replace('/', '\\')
            return f"{drive}:\\{rest}"
        return p_abs

    # Ignore caller-provided out_dir; always route to DATA_CCDC_DIR
    win_outdir = _to_windows_path(DATA_CCDC_DIR)
    def _try_windows_invoke(args: list[str]) -> subprocess.CompletedProcess:
        conda_candidates = [
            "conda",
            r"C:\\Users\\xz378\\AppData\\Local\\anaconda3\\condabin\\conda.bat",
            r"C:\\Users\\xz378\\AppData\\Local\\anaconda3\\envs\\csd311\\python.exe",
        ]
        last_err = None
        for conda_cmd in conda_candidates:
            print(f"[WSL CCDC] Trying candidate: {conda_cmd} with args: {args}")
            if conda_cmd.lower().endswith("python.exe"):
                cmd = [
                    "/mnt/c/Windows/System32/cmd.exe",
                    "/C",
                    conda_cmd,
                    "-m",
                    "src.mcp_servers.ccdc.operations.windows_ccdc",
                ] + args
            else:
                cmd = [
                    "/mnt/c/Windows/System32/cmd.exe",
                    "/C",
                    conda_cmd,
                    "run",
                    "-n",
                    "csd311",
                    "python",
                    "-m",
                    "src.mcp_servers.ccdc.operations.windows_ccdc",
                ] + args
            print(f"[WSL CCDC] Executing: {' '.join(cmd)}")
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            print(f"[WSL CCDC] Return code: {proc.returncode}")
            print(f"[WSL CCDC] STDOUT ({len(proc.stdout)} bytes):\n{proc.stdout[:500]}")
            print(f"[WSL CCDC] STDERR ({len(proc.stderr)} bytes):\n{proc.stderr[:500]}")
            if proc.returncode == 0:
                if proc.stdout.strip():
                    return proc
            last_err = proc
        raise RuntimeError(f"Windows CCDC invocation failed. Last error: {(last_err.stdout or '')}\n{(last_err.stderr or '')}")

    proc = _try_windows_invoke(["fetch", "--ccdc", str(deposition_number), "--outdir", win_outdir])
    raw = (proc.stdout or "").strip()
    # Robustly extract JSON object from mixed stdout
    def _extract_json_object(s: str):
        for line in s.splitlines():
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                return json.loads(line)
        i, j = s.find('{'), s.rfind('}')
        if i != -1 and j != -1 and j > i:
            return json.loads(s[i:j+1])
        raise ValueError("No JSON object found in stdout")
    data = _extract_json_object(raw)
    return {"res": data.get("res", ""), "cif": data.get("cif", "")}


if __name__ == "__main__":
    search_ccdc_by_mop_name("VMOP-β", False)
 
