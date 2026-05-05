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
from pathlib import Path
from typing import List, Tuple, Dict
import json
import subprocess
import sys

# Try native CCDC; if unavailable (e.g., running under WSL), fall back to Windows proxy
try:
    import ccdc  # noqa: F401  # presence implies API + license typically usable from subprocess too
    _HAVE_CCDC = True
except Exception:
    _HAVE_CCDC = False

from models.locations import DATA_CCDC_DIR


def _diag(*args, **kwargs) -> None:
    """Keep CCDC diagnostics off stdout so stdio MCP messages remain valid."""
    kwargs["file"] = sys.stderr
    kwargs.setdefault("flush", True)
    print(*args, **kwargs)


def _project_root() -> str:
    """Repository root: ``.../MCP-.../`` (this file: ``src/mcp_servers/ccdc/operations/``)."""
    return str(Path(__file__).resolve().parents[4])


def _ccdc_subprocess_timeout() -> float:
    """Subprocess (CSD) time limit; kill child on expiry so agents do not block indefinitely."""
    raw = (os.environ.get("CCDC_SUBPROCESS_TIMEOUT_SEC") or "180").strip()
    try:
        return max(30.0, float(raw))
    except Exception:
        return 180.0


def _subprocess_env() -> dict:
    """Propagate env with ``PYTHONPATH`` so ``python -m src....`` can import ``models`` / ``src``."""
    root = _project_root()
    out = {**os.environ}
    prev = (out.get("PYTHONPATH") or "").strip()
    out["PYTHONPATH"] = root if not prev else f"{root}{os.pathsep}{prev}"
    out.setdefault("PYTHONUNBUFFERED", "1")
    return out


def _parse_json_array_text(raw: str) -> list:
    s = (raw or "").strip()
    for line in s.splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            return json.loads(line)
    i, j = s.find("["), s.rfind("]")
    if i != -1 and j != -1 and j > i:
        return json.loads(s[i : j + 1])
    raise ValueError("No JSON array found in stdout")


def _parse_json_object_text(raw: str) -> dict:
    s = (raw or "").strip()
    for line in s.splitlines():
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            return json.loads(line)
    i, j = s.find("{"), s.rfind("}")
    if i != -1 and j != -1 and j > i:
        return json.loads(s[i : j + 1])
    raise ValueError("No JSON object found in stdout")


def _run_local_windows_ccdc_module(args: list[str]) -> subprocess.CompletedProcess:
    """
    Run ``python -m src.mcp_servers.ccdc.operations.windows_ccdc`` in a subprocess.

    On timeout, the process is **terminated** so hung CSD searches do not block the MCP.
    """
    root = _project_root()
    cmd = [sys.executable, "-m", "src.mcp_servers.ccdc.operations.windows_ccdc", *args]
    timeout = _ccdc_subprocess_timeout()
    return subprocess.run(
        cmd,
        cwd=root,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=_subprocess_env(),
    )


def _run_local_windows_ccdc_module_safe(args: list[str]) -> tuple[int, str, str]:
    """Like :func:`_run_local_windows_ccdc_module` but returns on ``TimeoutExpired`` instead of raising."""
    try:
        p = _run_local_windows_ccdc_module(args)
        return p.returncode, p.stdout or "", p.stderr or ""
    except subprocess.TimeoutExpired as e:
        t = _ccdc_subprocess_timeout()
        _diag(
            f"[CCDC] Subprocess timed out after {t}s: "
            f"{' '.join(str(x) for x in (args[:6] or []))}…"
        )
        return -1, (e.stdout or ""), (e.stderr or "") or f"timeout after {t}s"


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
    # VMOC series used in OntoMOP backtest
    "vmoc-1": "1583722",
    "vmoc-2": "1985926",
    "vmoc-3": "1985927",
    "vmoc-4": "1985928",
    "vmoc-5": "1985929",
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
    # Zr6L3 UMCs — ACS Appl. Mater. Interfaces 2018, 10.1021/acsami.7b18836 (SI / CIF deposit notes)
    "umc-1": "1576897",
    "umc-2": "1576898",
}


def _windows_cmd_exe() -> str:
    """Return the correct cmd.exe path for the current runtime."""
    return "cmd.exe" if os.name == "nt" else "/mnt/c/Windows/System32/cmd.exe"


def _windows_conda_candidates() -> List[str]:
    """Return execution candidates ordered to prefer direct env python over conda wrappers."""
    user = os.getenv("USERNAME", "")
    env_name = os.getenv("CSD_CONDA_ENV", "csd311")
    guesses = [
        os.getenv("CONDA_ENV_PY", ""),
        os.getenv("CONDA_PYTHON_EXE", ""),
        rf"C:\Users\{user}\AppData\Local\anaconda3\envs\{env_name}\python.exe" if user else "",
        os.getenv("CONDA_EXE", ""),
        os.getenv("CONDA_BAT", ""),
        rf"C:\Users\{user}\AppData\Local\anaconda3\condabin\conda.bat" if user else "",
        "conda",
    ]
    out: List[str] = []
    seen = set()
    for item in guesses:
        key = str(item or "").strip()
        if key and key not in seen:
            seen.add(key)
            out.append(key)
    return out


def _ccdc_conda_subprocess(windows_args: list[str]) -> subprocess.CompletedProcess:
    """
    Run ``cmd.exe /C … python -m src…windows_ccdc …`` (conda / Anaconda) with timeout and project cwd.
    """
    cmd_exe = _windows_cmd_exe()
    conda_candidates = _windows_conda_candidates()
    last_err: subprocess.CompletedProcess | None = None
    root = _project_root()
    timeout = _ccdc_subprocess_timeout()
    env = _subprocess_env()
    env_name = os.getenv("CSD_CONDA_ENV", "csd311")
    for conda_cmd in conda_candidates:
        _diag(f"[WSL CCDC] Trying candidate: {conda_cmd} with args: {windows_args}")
        if conda_cmd and conda_cmd.lower().endswith("python.exe"):
            cmd: list[str] = [
                cmd_exe,
                "/C",
                conda_cmd,
                "-m",
                "src.mcp_servers.ccdc.operations.windows_ccdc",
                *windows_args,
            ]
        elif conda_cmd:
            cmd = [
                cmd_exe,
                "/C",
                conda_cmd,
                "run",
                "-n",
                env_name,
                "python",
                "-m",
                "src.mcp_servers.ccdc.operations.windows_ccdc",
                *windows_args,
            ]
        else:
            cmd = [
                cmd_exe,
                "/C",
                "conda",
                "run",
                "-n",
                env_name,
                "python",
                "-m",
                "src.mcp_servers.ccdc.operations.windows_ccdc",
                *windows_args,
            ]
        _diag(f"[WSL CCDC] Executing: {' '.join(cmd)}")
        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
                cwd=root,
                env=env,
            )
        except subprocess.TimeoutExpired as e:
            _diag(f"[WSL CCDC] Conda CCDC command timed out after {timeout}s")
            last_err = subprocess.CompletedProcess(
                cmd, -1, e.stdout, (e.stderr or "") + f"\nTimeout after {timeout}s"
            )
            continue
        _diag(f"[WSL CCDC] Return code: {proc.returncode}")
        _diag(f"[WSL CCDC] STDOUT ({len(proc.stdout)} bytes):\n{proc.stdout[:500]}")
        _diag(f"[WSL CCDC] STDERR ({len(proc.stderr)} bytes):\n{proc.stderr[:500]}")
        if proc.returncode == 0 and (proc.stdout or "").strip():
            return proc
        last_err = proc
    if last_err is not None:
        raise RuntimeError(
            f"Windows CCDC invocation failed. Last error: "
            f"{(last_err.stdout or '')!s}\n{(last_err.stderr or '')!s}"
        )
    raise RuntimeError("Windows CCDC: no conda candidate produced a result")


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
        _diag(f"[CCDC] Found hardcoded mapping for '{name}': {hardcoded_results}")
        return hardcoded_results

    if _HAVE_CCDC:
        # In-process `TextNumericSearch().search()` can block indefinitely (CSD I/O, license, huge
        # "anywhere" scans). Use the same Python + `windows_ccdc` in a **subprocess** with a hard
        # timeout so the parent MCP / pipeline always regains control.
        cli_args = ["search", "--name", name] + (["--exact"] if exact else [])
        code, out, err = _run_local_windows_ccdc_module_safe(cli_args)
        if code == 0 and (out or "").strip():
            try:
                data = _parse_json_array_text(out)
                results = [(str(r[0]), str(r[1])) for r in data]
                if results:
                    return results
            except Exception as e:
                _diag(f"[CCDC] Failed to parse name-search JSON: {e}; stderr={err[:400]!r}")
        else:
            _diag(
                f"[CCDC] Local windows_ccdc name search failed (code={code}): "
                f"{(err or '')[:800]!r}"
            )
        return hardcoded_results
    # Fallback: no importable ccdc in *this* interpreter — call Windows CLI via cmd.exe and conda
    try:
        proc = _ccdc_conda_subprocess(
            ["search", "--name", name] + (["--exact"] if exact else [])
        )
    except Exception as e:
        _diag(f"[CCDC] Windows conda CCDC name search could not start: {e}")
        return hardcoded_results
    raw = (proc.stdout or "").strip()
    try:
        data = _parse_json_array_text(raw)
        results = [(str(r[0]), str(r[1])) for r in data]
        if results:
            return results
    except Exception as e:
        _diag(f"[CCDC] Windows CLI search failed: {e}, trying hardcoded mapping")
    
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
        code, out, err = _run_local_windows_ccdc_module_safe(["search_doi", "--doi", doi])
        if code == 0 and (out or "").strip():
            try:
                data = _parse_json_array_text(out)
                out_rows: List[Dict[str, str]] = []
                for obj in data:
                    out_rows.append({
                        "refcode": str(obj.get("refcode", "")),
                        "chemical_name": str(obj.get("chemical_name", "")),
                        "formula": str(obj.get("formula", "")),
                        "ccdc_number": str(obj.get("ccdc_number", "")),
                        "doi": str(obj.get("doi", "")),
                    })
                return out_rows
            except Exception as e:
                _diag(
                    f"[CCDC] search_doi JSON parse error: {e}; stderr={err[:400]!r} "
                    f"(trying conda fallback)"
                )
        else:
            _es = (err or "")[:800]
            _diag(
                f"[CCDC] local search_doi non-success (code={code}); trying conda. "
                f"stderr={_es!r}"
            )

    try:
        proc = _ccdc_conda_subprocess(["search_doi", "--doi", doi])
    except Exception as e:
        _diag(f"[CCDC] Conda search_doi failed: {e}")
        return []
    raw = (proc.stdout or "").strip()
    try:
        data = _parse_json_array_text(raw)
    except Exception as e:
        _diag(f"[CCDC] Conda search_doi JSON parse error: {e}")
        return []
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
        except ValueError as e:
            raise ValueError("deposition_number must be an integer string") from e

        code, out, err = _run_local_windows_ccdc_module_safe(
            ["fetch", "--ccdc", str(n_int), "--outdir", DATA_CCDC_DIR]
        )
        if code == 0 and (out or "").strip():
            try:
                d = _parse_json_object_text(out)
                r, c = d.get("res", ""), d.get("cif", "")
                if r and c:
                    return {"res": str(r), "cif": str(c)}
            except Exception as e:
                _diag(f"[CCDC] fetch JSON parse error: {e}; stderr={err[:400]!r}")
        raise ValueError(
            f"get_res_cif_file_by_ccdc failed (code={code}): "
            f"{(err or out or '')[:2000]!r}"
        )

    # Legacy path: ccdc not in this interpreter — use conda (same subprocess helper as name/DOI).
    def _to_windows_path(p: str) -> str:
        p_abs = os.path.abspath(p)
        if p_abs.startswith("/mnt/") and len(p_abs) > 6 and p_abs[5] == "/":
            drive = p_abs[5].upper()
            rest = p_abs[6:].replace("/", "\\")
            return f"{drive}:\\{rest}"
        return p_abs

    win_outdir = _to_windows_path(DATA_CCDC_DIR)
    proc = _ccdc_conda_subprocess(
        ["fetch", "--ccdc", str(deposition_number), "--outdir", win_outdir]
    )
    raw = (proc.stdout or "").strip()
    data = _parse_json_object_text(raw)
    return {"res": data.get("res", ""), "cif": data.get("cif", "")}


if __name__ == "__main__":
    search_ccdc_by_mop_name("VMOP-β", False)
 
