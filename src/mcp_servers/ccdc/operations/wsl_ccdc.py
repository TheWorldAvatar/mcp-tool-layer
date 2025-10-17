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

    
def search_ccdc_by_mop_name(name: str, exact: bool = False) -> List[Tuple[str, str]]:
    """Search CCDC by compound name.

    Args:
        name: Compound name to search.
        exact: When True, use exact match; otherwise search 'anywhere'.

    Returns:
        List of (CSD refcode, CCDC deposition number) tuples. Empty if none found.
    """
    if _HAVE_CCDC:
        q = TextNumericSearch()
        q.add_compound_name(name, mode='exact' if exact else 'anywhere')
        results: List[Tuple[str, str]] = []
        for hit in q.search():
            refcode = hit.identifier
            num = str(hit.entry.ccdc_number) if hit.entry.ccdc_number else ""
            results.append((refcode, num))
        return results
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
    data = _extract_json_array(raw)
    return [(str(r[0]), str(r[1])) for r in data]


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
    import argparse
    import sys as _sys
    # Demo mode when no arguments are provided
    if len(_sys.argv) == 1:
        demo_name = "IRMOP-50"
        demo_ccdc = "1590349"
        print("[WSL demo] search_ccdc_by_mop_name(exact=True):", demo_name)
        print(json.dumps(search_ccdc_by_mop_name(demo_name, exact=True), indent=2))
        print("[WSL demo] get_res_cif_file_by_ccdc:", demo_ccdc)
        print(json.dumps(get_res_cif_file_by_ccdc(demo_ccdc), indent=2))
        _sys.exit(0)

    ap = argparse.ArgumentParser(description="WSL-facing CCDC interface. Proxies to Windows when needed.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("search", help="Search by compound name")
    sp.add_argument("--name", required=True, help="Compound name to search")
    sp.add_argument("--exact", action="store_true", help="Use exact match mode")

    fp = sub.add_parser("fetch", help="Fetch by CCDC number and write .res/.cif")
    fp.add_argument("--ccdc", required=True, help="CCDC deposition number (numeric)")

    args = ap.parse_args()
    if args.cmd == "search":
        rows = search_ccdc_by_mop_name(args.name, args.exact)
        print(json.dumps(rows))
    elif args.cmd == "fetch":
        paths = get_res_cif_file_by_ccdc(args.ccdc)
        print(json.dumps(paths))


