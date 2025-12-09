import argparse, json, hashlib, asyncio
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from models.locations import DATA_DIR
try:
    from tqdm import tqdm
except Exception:
    class tqdm:  # type: ignore
        def __init__(self, *args, **kwargs): pass
        def update(self, *args, **kwargs): pass
        def close(self): pass

def _find_stitched_md(hash_dir: Path):
    cands = list(hash_dir.glob("*.md"))
    for p in cands:
        if "stitch" in p.name.lower():
            return p
    return cands[0] if cands else None

def _read(p: Path) -> str:
    if not p: return ""
    try:
        b = p.read_bytes()
    except Exception:
        return ""
    try: return b.decode("utf-8","replace")
    except Exception: return b.decode("latin-1","replace")

def _strip_fences(s: str) -> str:
    if not s:
        return ""
    lines = s.splitlines()
    out = []
    in_fence = False
    for ln in lines:
        if ln.strip().startswith("```"):
            in_fence = not in_fence
            continue
        out.append(ln)
    return "\n".join(out).strip() or s

def _load_hint_text(hv: str, entity_stem: str, hint_dir: Path | None = None) -> str:
    base_dir = hint_dir if hint_dir else (Path(DATA_DIR) / hv / "mcp_run_ontomops")
    cand = base_dir / f"extraction_{entity_stem}.txt"
    if cand.exists():
        try:
            raw = cand.read_text(encoding="utf-8", errors="replace")
        except Exception:
            raw = cand.read_text(errors="replace")
        return _strip_fences(raw)
    return ""

# ---------------- Extraction integration ----------------
try:
    # Import extractor to generate focused paper content per task
    from src.agents.mops.dynamic_mcp.modules.extraction import extract_content as mcp_extract
except Exception:
    mcp_extract = None  # type: ignore

GBU_GOAL = (
    "Extract only facts needed to determine the two GBUs: connector counts n for each unit, "
    "planarity evidence (angles θ, plane RMSD, mean angular deviation), explicit statements of planarity/linearity, and any mention of dinuclear paddlewheel [M2] priors. "
    "Use concise bullet points with quoted source snippets where possible."
)
AM_GOAL = (
    "Extract only facts needed to determine am_label counts: vertex/linker role counts and multiplicities, unique-site multiplicities, and any explicit totals associated with the molecular polyhedron."
)
SYMB_GOAL = (
    "Extract only facts needed to determine the polyhedral shape symbol: polyhedral names and aliases (map to RCSR codes), and vertex-orbit evidence (single 12 orbit vs two 6+6 orbits, vertex figures)."
)
PG_GOAL = (
    "Extract only facts needed to determine the molecular point group (Schoenflies): explicit PG mentions (normalize), and any binding implications from the polyhedral name/symbol. Do not use crystal space group."
)

def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")

def _extract_task_texts(hv: str, entity_stem: str, paper_full: str, *, out_dir: Path) -> dict:
    results = {"gbu": paper_full, "am": paper_full, "symb": paper_full, "pg": paper_full}
    if not mcp_extract or not paper_full:
        # extractor unavailable or no paper content; fall back to full paper
        return results
    try:
        gbu_txt = asyncio.run(mcp_extract(paper_full, GBU_GOAL))
        _write_text(out_dir / f"extraction_{entity_stem}_gbu.md", gbu_txt)
        results["gbu"] = gbu_txt or paper_full
    except Exception:
        pass
    try:
        am_txt = asyncio.run(mcp_extract(paper_full, AM_GOAL))
        _write_text(out_dir / f"extraction_{entity_stem}_am.md", am_txt)
        results["am"] = am_txt or paper_full
    except Exception:
        pass
    try:
        symb_txt = asyncio.run(mcp_extract(paper_full, SYMB_GOAL))
        _write_text(out_dir / f"extraction_{entity_stem}_symbol.md", symb_txt)
        results["symb"] = symb_txt or paper_full
    except Exception:
        pass
    try:
        pg_txt = asyncio.run(mcp_extract(paper_full, PG_GOAL))
        _write_text(out_dir / f"extraction_{entity_stem}_pg.md", pg_txt)
        results["pg"] = pg_txt or paper_full
    except Exception:
        pass
    return results

def _load_task_hints(hv: str, entity_stem: str, *, hint_dir: Path | None = None) -> dict:
    """Load pre-extracted task-specific hints if present; otherwise load a single hint file and reuse for all tasks."""
    base = hint_dir if hint_dir else (Path(DATA_DIR) / hv / "cbu_derivation")
    gbu_p  = base / f"extraction_{entity_stem}_gbu.md"
    am_p   = base / f"extraction_{entity_stem}_am.md"
    sym_p  = base / f"extraction_{entity_stem}_symbol.md"
    pg_p   = base / f"extraction_{entity_stem}_pg.md"
    def read_opt(p: Path) -> str:
        try: return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""
        except Exception: return ""
    gbu_t = read_opt(gbu_p)
    am_t  = read_opt(am_p)
    sym_t = read_opt(sym_p)
    pg_t  = read_opt(pg_p)
    if any([gbu_t, am_t, sym_t, pg_t]):
        return {
            "gbu": gbu_t or "",
            "am": am_t or "",
            "symb": sym_t or "",
            "pg": pg_t or "",
        }
    # Fallback: single generic hint file under mcp_run_ontomops
    single = _load_hint_text(hv, entity_stem, hint_dir)
    return {"gbu": single, "am": single, "symb": single, "pg": single}

def _find_ccdc_files(ccdc: str) -> tuple[Path, Path]:
    if not ccdc:
        return (Path(""), Path(""))
    cif = Path(DATA_DIR) / "ontologies" / "ccdc" / "cif" / f"{ccdc}.cif"
    res = Path(DATA_DIR) / "ontologies" / "ccdc" / "res" / f"{ccdc}.res"
    return (cif if cif.exists() else Path("") , res if res.exists() else Path(""))

def _cbus_from_integrated(data: dict) -> list[dict]:
    cbus = data.get("cbus")
    if isinstance(cbus, list) and cbus:
        out = []
        for item in cbus:
            v = str((item or {}).get("cbu_formula") or "").strip()
            if v:
                out.append({"cbu_formula": v})
        if out: return out
    m = ((data.get("metal_cbu") or {}).get("formula") or "").strip()
    o = ((data.get("organic_cbu") or {}).get("formula") or "").strip()
    res = []
    if m: res.append({"cbu_formula": m})
    if o: res.append({"cbu_formula": o})
    return res

def _load_gt_map(gt_path: Path) -> dict:
    try:
        data = json.loads(gt_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    m = {}
    if isinstance(data, list):
        for item in data:
            ccdc = str((item or {}).get("ccdc_number") or "").strip()
            if not ccdc:
                continue
            cbus = []
            for c in (item or {}).get("cbus", []) or []:
                v = str((c or {}).get("cbu_formula") or "").strip()
                if v:
                    cbus.append({"cbu_formula": v})
            m[ccdc] = cbus
    return m

def _run_pipeline(hv: str, jf: Path, *, ablation: bool = False, gt_map: dict | None = None, hint: bool = False, hint_dir: Path | None = None) -> tuple[str, Path | None]:
    try:
        data = json.loads(jf.read_text(encoding="utf-8"))
    except Exception as e:
        return (f"[{hv}] Failed reading {jf.name}: {e}", None)

    hash_dir = Path(DATA_DIR) / hv
    # Paper content: prefer hint if requested, else stitched markdown
    paper = ""
    if hint:
        paper = _load_hint_text(hv, jf.stem, hint_dir)
    if not paper:
        paper_path = _find_stitched_md(hash_dir)
        paper = _read(paper_path) if paper_path else ""

    # Task-specific paper content resolution
    if hint:
        # If --hint provided, load pre-extracted task-specific hints (or single hint) as paper content
        task_texts = _load_task_hints(hv, jf.stem, hint_dir=hint_dir)
    else:
        # Otherwise, generate fresh extractions and store them for traceability
        task_texts = _extract_task_texts(hv, jf.stem, paper_full=paper, out_dir=(Path(DATA_DIR) / hv / "cbu_derivation"))
    ccdc = str(data.get("ccdc_number") or "").strip()
    if not ccdc:
        return (f"[{hv}] Skipped {jf.name}: missing ccdc_number in input", None)
    # In ablation mode, require CCDC to exist in ground truth
    if ablation and gt_map is not None and ccdc not in gt_map:
        return (f"[{hv}] Skipped {jf.name}: CCDC {ccdc} not in ground truth", None)

    cif_p, res_p = _find_ccdc_files(ccdc)
    cif = _read(cif_p) if cif_p else ""
    res = _read(res_p) if res_p else ""
    cbus = _cbus_from_integrated(data)
    # Proceed even if CBUs are missing; agents will attempt to infer/validate

    # Agent 1: derive GBUs (echo assembly_model if present)
    from .agent_gbu import call_emit as call_gbu, SYSTEM as SYS_GBU, SCHEMA as SCH_GBU
    try:
        am_init = {"am_label": "", "polyhedral_shape_symbol": "", "symmetry_point_group": ""}
        resp_gbu = call_gbu(SYS_GBU, {"paper": task_texts.get("gbu", paper), "cif": cif, "res": res, "assembly_model": am_init}, SCH_GBU)
    except Exception as e:
        return (f"[{hv}] Agent GBU error for {jf.name}: {e}", None)

    gbus = (resp_gbu or {}).get("gbus", [])
    am = dict(am_init)
    # keep any echoed fields if present
    echoed_am = (resp_gbu or {}).get("assembly_model", {}) or {}
    for k in ("am_label","polyhedral_shape_symbol","symmetry_point_group"):
        v = echoed_am.get(k)
        if isinstance(v, str) and v:
            am[k] = v

    # Agent 2: derive AM label (echo symbol/point group unchanged)
    from .agent_am import call_emit as call_am, SYSTEM as SYS_AM, SCHEMA as SCH_AM
    try:
        resp_am = call_am(SYS_AM, {"paper": task_texts.get("am", paper), "cif": cif, "res": res, "gbus": gbus, "assembly_model": am}, SCH_AM)
    except Exception as e:
        return (f"[{hv}] Agent AM error for {jf.name}: {e}", None)
    # Only update am_label if provided
    am_label_new = (resp_am or {}).get("am_label")
    if isinstance(am_label_new, str):
        am["am_label"] = am_label_new

    # Agent 3: derive polyhedral shape symbol
    from .agent_polyheral_symbol import call_emit as call_symb, SYSTEM as SYS_SYMB, SCHEMA as SCH_SYMB
    try:
        resp_symb = call_symb(SYS_SYMB, {"paper": task_texts.get("symb", paper), "cif": cif, "res": res, "gbus": gbus, "assembly_model": am}, SCH_SYMB)
    except Exception as e:
        return (f"[{hv}] Agent Polyhedral Symbol error for {jf.name}: {e}", None)
    # Only update symbol if provided
    symb_new = (resp_symb or {}).get("polyhedral_shape_symbol")
    if isinstance(symb_new, str):
        am["polyhedral_shape_symbol"] = symb_new

    # Agent 4: derive symmetry point group (may bind with symbol)
    from .agent_point_group import call_emit as call_pg, SYSTEM as SYS_PG, SCHEMA as SCH_PG
    try:
        resp_pg = call_pg(SYS_PG, {"paper": task_texts.get("pg", paper), "cif": cif, "res": res, "gbus": gbus, "assembly_model": am}, SCH_PG)
    except Exception as e:
        return (f"[{hv}] Agent Point Group error for {jf.name}: {e}", None)
    # Only update PG if provided
    pg_new = (resp_pg or {}).get("symmetry_point_group")
    if isinstance(pg_new, str):
        am["symmetry_point_group"] = pg_new

    # Finalize
    from .agent_finalize import _mk_mop, _bind_symbol_pg, _mirror_gbus
    _bind_symbol_pg(am)
    am["gbus"] = gbus
    if ablation and gt_map is not None:
        cbus_final = gt_map.get(ccdc, [])
    else:
        cbus_final = (data or {}).get("cbus", []) or _cbus_from_integrated(data)
    mop = _mk_mop(cbus_final, am.get("am_label", ""))
    final = {
        "mop_formula": mop,
        "assembly_model": am,
        "gbus": gbus,
        "cbus": cbus_final,
        "ccdc_number": str(data.get("ccdc_number") or ""),
        "doi": str(data.get("doi") or ""),
    }
    if "[]" in final["mop_formula"]:
        final["mop_formula"] = ""

    # Ensure CCDC/DOI backfill from integrated input if missing/empty
    if (not str(final.get("ccdc_number") or "").strip()) and ccdc:
        final["ccdc_number"] = ccdc
    doi_in = str(data.get("doi") or "").strip()
    if (not str(final.get("doi") or "").strip()) and doi_in:
        final["doi"] = doi_in

    out_dir = Path(DATA_DIR) / hv / "cbu_derivation" / "full"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / jf.name
    try:
        out_path.write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        return (f"[{hv}] Failed writing output for {jf.name}: {e}", None)
    return (f"[{hv}] OK {jf.name} → {out_path.name}", out_path)

def run_batch(only_hash: str | None = None, *, ablation: bool = False, gt_map: dict | None = None, hint: bool = False, hint_dir: Path | None = None) -> None:
    tasks: list[tuple[str, Path]] = []
    root = Path(DATA_DIR)
    for d in sorted(root.iterdir()):
        if not d.is_dir() or len(d.name) != 8:
            continue
        hv = d.name
        if only_hash and hv != only_hash:
            continue
        integ = d / "cbu_derivation" / "integrated"
        if not integ.exists():
            continue
        for jf in sorted(integ.glob("*.json")):
            tasks.append((hv, jf))
    if not tasks:
        print("No integrated JSON inputs found.")
        return
    print(f"Found {len(tasks)} tasks. Running in parallel...")
    max_workers = max(1, min(8, len(tasks)))
    ok = 0
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = [ex.submit(_run_pipeline, hv, jf, ablation=ablation, gt_map=gt_map, hint=hint, hint_dir=hint_dir) for hv, jf in tasks]
        pbar = tqdm(total=len(futures), desc="Deriving OntoMOPs")
        for fut in as_completed(futures):
            try:
                msg, path = fut.result()
                print(msg)
                if path is not None:
                    ok += 1
            except Exception as e:
                print(f"Worker crashed: {e}")
            finally:
                try:
                    pbar.update(1)
                except Exception:
                    pass
        try:
            pbar.close()
        except Exception:
            pass
    print(f"Done. {ok}/{len(tasks)} succeeded.")

def main():
    ap = argparse.ArgumentParser(description="OntoMOP derivation (batch or single-run)")
    ap.add_argument("--paper", type=Path)
    ap.add_argument("--cif", type=Path)
    ap.add_argument("--res", type=Path)
    ap.add_argument("--cbus", type=Path)
    ap.add_argument("--out", type=Path, default=Path("ontomop_final.json"))
    ap.add_argument("--ccdc", type=str)
    ap.add_argument("--doi", type=str)
    ap.add_argument("--batch", action="store_true")
    ap.add_argument("--ablation", action="store_true", help="Use ground-truth CBUs by CCDC for final output")
    ap.add_argument("--ground-truth", type=Path, default=Path("data/ontologies/full_mop_expanded_concise.json"))
    ap.add_argument("--hint", action="store_true", help="Use entity-specific hint text instead of stitched paper text")
    ap.add_argument("--hint-dir", type=Path, help="Optional directory containing extraction_<entity>.txt files")
    ap.add_argument("--hint-file", type=Path, help="Single-run: direct path to hint file to use as paper content")
    ap.add_argument("--file", type=str, help="Specific 8-char hash or arbitrary id (hashed)")
    args = ap.parse_args()

    if args.batch or (not args.paper and not args.cbus):
        hv = None
        if args.file:
            v = args.file.strip()
            if len(v) == 8 and (Path(DATA_DIR) / v).exists():
                hv = v
            else:
                try:
                    hv = hashlib.sha256(v.encode()).hexdigest()[:8]
                except Exception:
                    hv = None
        gt_map = _load_gt_map(args.ground_truth) if args.ablation else None
        run_batch(only_hash=hv, ablation=args.ablation, gt_map=gt_map, hint=args.hint, hint_dir=args.hint_dir)
        return

    # Single-run pipeline with four agents
    paper = _read(args.paper) if args.paper else ""
    if args.hint and args.hint_file:
        try:
            raw = args.hint_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            raw = args.hint_file.read_text(errors="replace")
        paper = _strip_fences(raw) or paper
    cif   = _read(args.cif) if args.cif else ""
    res   = _read(args.res) if args.res else ""
    cbus  = []

    # GBU
    from .agent_gbu import call_emit as call_gbu, SYSTEM as SYS_GBU, SCHEMA as SCH_GBU
    am = {"am_label": "", "polyhedral_shape_symbol": "", "symmetry_point_group": ""}
    resp_gbu = call_gbu(SYS_GBU, {"paper": paper, "cif": cif, "res": res, "assembly_model": am}, SCH_GBU)
    gbus = (resp_gbu or {}).get("gbus", [])
    echoed_am = (resp_gbu or {}).get("assembly_model", {}) or {}
    for k in ("am_label","polyhedral_shape_symbol","symmetry_point_group"):
        v = echoed_am.get(k)
        if isinstance(v, str) and v:
            am[k] = v

    # AM label
    from .agent_am import call_emit as call_am, SYSTEM as SYS_AM, SCHEMA as SCH_AM
    resp_am = call_am(SYS_AM, {"paper": paper, "cif": cif, "res": res, "gbus": gbus, "assembly_model": am}, SCH_AM)
    am_label_new = (resp_am or {}).get("am_label")
    if isinstance(am_label_new, str):
        am["am_label"] = am_label_new

    # Symbol
    from .agent_polyheral_symbol import call_emit as call_symb, SYSTEM as SYS_SYMB, SCHEMA as SCH_SYMB
    resp_symb = call_symb(SYS_SYMB, {"paper": paper, "cif": cif, "res": res, "gbus": gbus, "assembly_model": am}, SCH_SYMB)
    symb_new = (resp_symb or {}).get("polyhedral_shape_symbol")
    if isinstance(symb_new, str):
        am["polyhedral_shape_symbol"] = symb_new

    # Point group
    from .agent_point_group import call_emit as call_pg, SYSTEM as SYS_PG, SCHEMA as SCH_PG
    resp_pg = call_pg(SYS_PG, {"paper": paper, "cif": cif, "res": res, "gbus": gbus, "assembly_model": am}, SCH_PG)
    pg_new = (resp_pg or {}).get("symmetry_point_group")
    if isinstance(pg_new, str):
        am["symmetry_point_group"] = pg_new

    from .agent_finalize import _mk_mop, _bind_symbol_pg
    _bind_symbol_pg(am)
    am["gbus"] = gbus
    # In ablation mode, allow --ccdc to fetch CBUs from ground truth
    if args.ablation and args.ccdc:
        gt_map = _load_gt_map(args.ground_truth)
        cbus = gt_map.get(str(args.ccdc).strip(), [])

    mop = _mk_mop(cbus, am.get("am_label", ""))
    final = {
        "mop_formula": mop,
        "assembly_model": am,
        "gbus": gbus,
        "cbus": cbus,
        "ccdc_number": str((args.__dict__.get("ccdc") or "")),
        "doi": str((args.__dict__.get("doi") or "")),
    }
    if "[]" in final["mop_formula"]:
        final["mop_formula"] = ""
    args.out.write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(final, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
