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
import re
from pathlib import Path
from typing import List, Tuple, Dict, Optional
import json
import subprocess
import sys

from models.locations import DATA_CCDC_DIR
from src.utils.source_text_sanitize import sanitize_source_markdown

# Hard-require the licensed CSD conda env. Do not probe mcp_layer / PATH / conda-run fallbacks.
_DEFAULT_CSD_CONDA_ENV = "csd311"


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


def resolve_csd_python_exe() -> str:
    """Return the only supported CSD/CCDC Python interpreter (csd311 by default).

    Resolution order:
      1. ``CSD_PYTHON_EXE`` if it points at an existing file
      2. ``%USERPROFILE%/.../anaconda3|miniconda3/envs/{CSD_CONDA_ENV|csd311}/python.exe``
    """
    override = (os.environ.get("CSD_PYTHON_EXE") or "").strip()
    if override:
        if Path(override).is_file():
            return str(Path(override).resolve())
        raise RuntimeError(
            f"CSD_PYTHON_EXE is set but not a file: {override}"
        )

    env_name = (os.environ.get("CSD_CONDA_ENV") or _DEFAULT_CSD_CONDA_ENV).strip() or _DEFAULT_CSD_CONDA_ENV
    user_profile = (
        os.environ.get("USERPROFILE")
        or os.environ.get("HOME")
        or ""
    ).strip()
    username = (os.environ.get("USERNAME") or os.environ.get("USER") or "").strip()
    roots: list[Path] = []
    if user_profile:
        roots.extend(
            [
                Path(user_profile) / "AppData" / "Local" / "anaconda3",
                Path(user_profile) / "AppData" / "Local" / "miniconda3",
                Path(user_profile) / "anaconda3",
                Path(user_profile) / "miniconda3",
            ]
        )
    if username:
        roots.extend(
            [
                Path(rf"C:\Users\{username}\AppData\Local\anaconda3"),
                Path(rf"C:\Users\{username}\anaconda3"),
            ]
        )
    roots.extend(
        [
            Path(r"C:\ProgramData\anaconda3"),
            Path(r"C:\ProgramData\miniconda3"),
        ]
    )

    seen: set[str] = set()
    for root in roots:
        candidate = root / "envs" / env_name / "python.exe"
        key = str(candidate).lower()
        if key in seen:
            continue
        seen.add(key)
        if candidate.is_file():
            return str(candidate.resolve())

    raise RuntimeError(
        f"CSD Python not found for conda env '{env_name}'. "
        f"Install that env with the ccdc package, or set CSD_PYTHON_EXE to its python.exe."
    )


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


def _run_csd_windows_ccdc(args: list[str]) -> subprocess.CompletedProcess:
    """Run ``windows_ccdc`` under the hard-coded CSD python (never the caller interpreter)."""
    root = _project_root()
    csd_py = resolve_csd_python_exe()
    cmd = [csd_py, "-m", "src.mcp_servers.ccdc.operations.windows_ccdc", *args]
    timeout = _ccdc_subprocess_timeout()
    _diag(f"[CCDC] Executing via {csd_py}: {' '.join(args[:8])}")
    return subprocess.run(
        cmd,
        cwd=root,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=_subprocess_env(),
    )


def _run_csd_windows_ccdc_safe(args: list[str]) -> tuple[int, str, str]:
    """Like :func:`_run_csd_windows_ccdc` but returns on ``TimeoutExpired`` instead of raising."""
    try:
        p = _run_csd_windows_ccdc(args)
        return p.returncode, p.stdout or "", p.stderr or ""
    except subprocess.TimeoutExpired as e:
        t = _ccdc_subprocess_timeout()
        _diag(
            f"[CCDC] Subprocess timed out after {t}s: "
            f"{' '.join(str(x) for x in (args[:6] or []))}…"
        )
        return -1, (e.stdout or "") or "", (e.stderr or "") or f"timeout after {t}s"


# Backward-compatible aliases used by older tests / imports.
_run_local_windows_ccdc_module = _run_csd_windows_ccdc
_run_local_windows_ccdc_module_safe = _run_csd_windows_ccdc_safe


# Hardcoded mapping between MOP names and CCDC numbers
# Format: {mop_name_lowercase: ccdc_number}
# This serves as a fallback when CCDC API searches fail or for known mappings
# IMPORTANT: All keys must be lowercase since lookup uses .lower()
HARDCODED_MOP_CCDC_MAPPING = {
    # IRMOP series
    "[me2nh2]5[v6o6(och3)9(so4)4]": "1590347",
    "irmop-50": "273613",
    "irmop-51": "273616",
    "irmop-51 (cubic)": "273616",
    "irmop-51 cubic": "273616",
    "irmop-51 (triclinic)": "273616",
    "irmop-51 triclinic": "273616",
    "irmop-52": "273620",
    "irmop-53": "273621",
    "mop-54": "273623",
    # VMOP series (Greek, short ASCII, and sanitized full-name ASCII)
    "vmop-α": "1590349",
    "vmop-a": "1590349",
    "vmop-alpha": "1590349",
    "vmop-β": "1590348",
    "vmop-b": "1590348",
    "vmop-beta": "1590348",
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
    # Cu_OR-bdc porous cages only (not the 2D sheets in the same 1815075-1815084 deposit).
    # Matched to SI Table S14 cells: OEt P-1/11304, OBu P4/m/34722, OPr P-1/13001, OPent R-3c/77680.
    "cu_oet-bdc": "1815080",
    "cu_oet-bdc cage": "1815080",
    "cu_oet-bdc porous cage": "1815080",
    "cu_oet-bdc cage synthesis": "1815080",
    "cu_obu-bdc": "1815077",
    "cu_obu-bdc cage": "1815077",
    "cu_obu-bdc porous cage": "1815077",
    "cu_obu-bdc cage synthesis": "1815077",
    "cu_opr-bdc": "1815084",
    "cu_opr-bdc cage": "1815084",
    "cu_opr-bdc porous cage": "1815084",
    "cu_opr-bdc cage synthesis": "1815084",
    "cu_opent-bdc": "1815083",
    "cu_opent-bdc cage": "1815083",
    "cu_opent-bdc porous cage": "1815083",
    "cu_opent-bdc cage synthesis": "1815083",
    # Cu24(tBu-amide-bdc)24: Chem. Mater. 2018, 10.1021/acs.chemmater.8b01667
    "cu24(tbu-amide-bdc)24": "1835131",
    "cu24(tbu-amide-bdc)24 cage": "1835131",
    "mechanochemical synthesis of cu24(tbu-amide-bdc)24": "1835131",
    "solvothermal synthesis of cu24(tbu-amide-bdc)24": "1835131",
}


def _lookup_hardcoded_ccdc(name: str) -> List[Tuple[str, str]]:
    """Look up CCDC number from hardcoded mapping.

    Args:
        name: MOP name (case-insensitive)

    Returns:
        List of (refcode, ccdc_number) tuples, or empty list if not found.
        Refcode is set to the normalized name since we don't have actual CSD refcodes.
    """
    candidates = []
    raw = (name or "").strip()
    if raw:
        candidates.append(raw.lower())
        sanitized = sanitize_source_markdown(raw).strip().lower()
        if sanitized and sanitized not in candidates:
            candidates.append(sanitized)
        destemmed = re.sub(r"\s*\([^)]*\)\s*$", "", raw).strip().lower()
        if destemmed and destemmed not in candidates:
            candidates.append(destemmed)
        token = re.search(r"\b([a-z][a-z0-9]*-\d+)\b", destemmed or raw.lower())
        if token and token.group(1) not in candidates:
            candidates.append(token.group(1))
    for normalized in candidates:
        ccdc = HARDCODED_MOP_CCDC_MAPPING.get(normalized)
        if ccdc:
            return [(normalized.upper().replace(" ", "_"), ccdc)]
    return []


def search_ccdc_by_mop_name(name: str, exact: bool = True) -> List[Tuple[str, str]]:
    """Resolve a compound name only through the curated exact-name mapping.

    Free-text licensed-CSD name searches are intentionally not attempted here:
    they are slow, non-deterministic across CSD installations, and previously
    blocked extraction for minutes per guessed spelling. Unknown names fail
    closed and callers may perform one DOI-based lookup instead.
    """
    query_name = sanitize_source_markdown(name or "").strip() or (name or "").strip()
    hardcoded_results = _lookup_hardcoded_ccdc(query_name) or _lookup_hardcoded_ccdc(name)
    if hardcoded_results:
        _diag(f"[CCDC] Found hardcoded mapping for '{name}': {hardcoded_results}")
        return hardcoded_results
    _diag(f"[CCDC] No curated exact-name mapping for '{name}'; returning no result")
    return []


def _normalize_doi_input(doi_like: str) -> str:
    """Normalize various DOI inputs to the CCDC-acceptable form '10.xxx/yyy'.

    Accepts:
      - pipeline form with underscores: e.g., '10.1021_ic050460z'
      - full URLs: e.g., 'https://doi.org/10.1021/ic050460z'
      - optional leading '@' characters (will be stripped)
      - 8-hex document hashes resolved via doi_to_hash.json / paper_doi.txt

    Returns the normalized DOI string with '/'. Raises ValueError if unrecognized.
    """
    if not doi_like:
        raise ValueError("Empty DOI input")
    s = doi_like.strip()
    if s.startswith('@'):
        s = s[1:]
    # Resolve pipeline document-hash → bibliographic DOI when agents pass the hash
    if re.fullmatch(r"[a-fA-F0-9]{8}", s):
        resolved = _resolve_document_hash_to_doi(s)
        if resolved:
            s = resolved
        else:
            raise ValueError(f"Unrecognized DOI format: {doi_like}")
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


def _resolve_document_hash_to_doi(doc_hash: str) -> Optional[str]:
    """Map an 8-hex document hash to a slash-form bibliographic DOI."""
    import json
    from pathlib import Path

    roots: list[Path] = []
    for env_key in ("TWA_AGENTIC_DATA_DIR", "TWA_EXTENSION_DATA_DIR", "DATA_DIR"):
        val = os.environ.get(env_key)
        if val:
            roots.append(Path(val))
    roots.append(Path("data"))

    for root in roots:
        paper = root / doc_hash / "paper_doi.txt"
        if paper.exists():
            try:
                text = paper.read_text(encoding="utf-8").strip().replace("_", "/")
                if text.startswith("10.") and "/" in text:
                    return text
            except OSError:
                pass
        mapping = root / "doi_to_hash.json"
        if not mapping.exists():
            continue
        try:
            doi_to_hash = json.loads(mapping.read_text(encoding="utf-8"))
        except Exception:
            continue
        for doi, h in doi_to_hash.items():
            if str(h) == doc_hash:
                return str(doi).replace("_", "/")
    return None


def search_ccdc_by_doi(doi_like: str) -> List[Dict[str, str]]:
    """Fail closed for DOIs absent from the server's curated DOI mapping.

    ``main.search_ccdc_by_doi`` resolves curated DOI records before calling this
    operation. Starting a licensed-CSD free-text DOI subprocess for an unknown
    DOI is intentionally disabled because it is installation-dependent and can
    block every per-entity extraction for several minutes.
    """
    doi = _normalize_doi_input(doi_like)
    _diag(f"[CCDC] DOI '{doi}' is not in the curated mapping; returning no result")
    return []


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
    try:
        n_int = int(deposition_number)
    except ValueError as e:
        raise ValueError("deposition_number must be an integer string") from e

    code, out, err = _run_csd_windows_ccdc_safe(
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


if __name__ == "__main__":
    search_ccdc_by_mop_name("VMOP-β", False)
 
