#!/usr/bin/env python3
"""
Windows-side CLI for CCDC operations.

This module is intended to be invoked from WSL via:
  cmd.exe /C conda run -n csd311 python -m src.mcp_servers.ccdc.operations.win_cli <command> [args]

Commands:
  - search --name <text> [--exact]
      Prints JSON list of [refcode, ccdc_number]

  - fetch --ccdc <number> --outdir <dir>
      Writes <number>.res and <number>.cif to outdir; prints JSON {"res": path, "cif": path}
"""
from __future__ import annotations

import argparse
import json
import os
from typing import List, Tuple

from ccdc.search import TextNumericSearch
from ccdc.io import CrystalWriter, EntryReader
from models.locations import DATA_CCDC_DIR


def do_search(name: str, exact: bool = False):
    q = TextNumericSearch()
    q.add_compound_name(name, mode='exact' if exact else 'anywhere')
    results: List[Tuple[str, str]] = []
    for h in q.search():
        refcode = h.identifier
        num = str(h.entry.ccdc_number) if h.entry.ccdc_number else ""
        results.append([refcode, num])
    print(json.dumps(results))


def do_fetch(ccdc_number: str, outdir: str):
    try:
        n_int = int(ccdc_number)
    except ValueError:
        raise SystemExit("deposition_number must be an integer string")

    q = TextNumericSearch()
    q.add_ccdc_number(n_int)
    hits = q.search()
    # Filter to exact numeric match and 3D structures
    candidates = []
    for h in hits:
        try:
            entry_num_int = int(h.entry.ccdc_number) if h.entry.ccdc_number else -1
        except Exception:
            continue
        if entry_num_int == n_int and h.entry.has_3d_structure:
            candidates.append(h)
    if not candidates:
        raise SystemExit(f"No valid 3D entries found for {n_int} (hits={len(hits)})")
    # If multiple, choose deterministically by refcode
    if len(candidates) > 1:
        candidates.sort(key=lambda h: (str(h.identifier) or ""))
    hit = candidates[0]
    try:
        entry_num_int = int(hit.entry.ccdc_number)
    except Exception:
        raise SystemExit(f"Hit has non-numeric ccdc_number: {hit.entry.ccdc_number}")
    if entry_num_int != n_int:
        raise SystemExit(f"CCDC mismatch: requested {n_int}, entry {hit.entry.ccdc_number}")
    if not hit.entry.has_3d_structure:
        raise SystemExit(f"Entry {n_int} has no 3D structure")

    # Ensure res/ and cif/ subdirectories under the given outdir
    res_dir = os.path.abspath(os.path.join(DATA_CCDC_DIR, "res"))
    cif_dir = os.path.abspath(os.path.join(DATA_CCDC_DIR, "cif"))
    os.makedirs(res_dir, exist_ok=True)
    os.makedirs(cif_dir, exist_ok=True)
    res_path = os.path.join(res_dir, f"{n_int}.res")
    cif_path = os.path.join(cif_dir, f"{n_int}.cif")

    crys = hit.entry.crystal
    with CrystalWriter(res_path) as w:
        w.write(crys)
    with CrystalWriter(cif_path) as w:
        w.write(crys)

    print(json.dumps({"res": res_path, "cif": cif_path}))
def _normalize_doi_input(doi_like: str) -> str:
    s = (doi_like or "").strip()
    if not s:
        raise SystemExit("Empty DOI input")
    if s.startswith('@'):
        s = s[1:]
    low = s.lower()
    if low.startswith('http://') or low.startswith('https://'):
        i = s.find('/10.')
        if i == -1:
            raise SystemExit("Invalid DOI URL: missing /10.")
        s = s[i+1:]
    if '_' in s and '/' not in s:
        s = s.replace('_', '/')
    if not (s.startswith('10.') and '/' in s):
        raise SystemExit(f"Unrecognized DOI format: {doi_like}")
    return s


def do_search_doi(doi_like: str):
    doi = _normalize_doi_input(doi_like)
    q = TextNumericSearch()
    q.add_doi(doi)
    hits = q.search()
    rows = []
    reader = EntryReader('CSD')
    try:
        for h in hits:
            refcode = str(h.identifier)
            e = reader.entry(refcode)
            rows.append({
                "refcode": e.identifier,
                "chemical_name": str(getattr(e, 'chemical_name', '') or ''),
                "formula": str(getattr(e, 'formula', '') or ''),
                "ccdc_number": str(getattr(e, 'ccdc_number', '') or ''),
                "doi": str(e.publication.doi) if getattr(e, 'publication', None) else ''
            })
    finally:
        try:
            reader.close()
        except Exception:
            pass
    print(json.dumps(rows))



def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)

    sp = sub.add_parser('search')
    sp.add_argument('--name', required=True)
    sp.add_argument('--exact', action='store_true')

    fp = sub.add_parser('fetch')
    fp.add_argument('--ccdc', required=True)
    # outdir is optional and ignored; DATA_CCDC_DIR is always used
    fp.add_argument('--outdir', required=False, default=DATA_CCDC_DIR)

    dp = sub.add_parser('search_doi')
    dp.add_argument('--doi', required=True)

    args = ap.parse_args()
    if args.cmd == 'search':
        do_search(args.name, args.exact)
    elif args.cmd == 'fetch':
        do_fetch(args.ccdc, args.outdir)
    elif args.cmd == 'search_doi':
        do_search_doi(args.doi)
    else:
        ap.error('unknown command')


if __name__ == '__main__':
    main()


