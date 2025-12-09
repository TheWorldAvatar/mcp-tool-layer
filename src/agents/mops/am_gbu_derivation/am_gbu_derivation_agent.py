# file: derive_am_gbu_rcsr.py  (fixed)
import os, sys, json, argparse, time
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI
from typing import List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from models.locations import DATA_DIR

def read_text(p: Path, max_bytes: int = 2_000_000) -> str:
    if not p or not isinstance(p, Path): return ""
    try:
        if not p.exists() or not p.is_file(): return ""
    except Exception:
        return ""
    b = p.read_bytes()
    if len(b) > max_bytes: b = b[:max_bytes]
    try: return b.decode("utf-8", errors="replace")
    except: return b.decode("latin-1", errors="replace")

SCHEMA = {
    "name": "AM_GBU_V1",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "mop_formula": {"type": "string"},
            "assembly_model": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "am_label": {"type": "string"},
                    "polyhedral_shape_symbol": {"type": "string"},
                    "symmetry_point_group": {"type": "string"},
                    "gbus": {
                        "type": "array","minItems": 2,"maxItems": 2,
                        "items": {
                            "type": "object","additionalProperties": False,
                            "properties": {
                                "gbu_type_detail": {
                                    "type": "object","additionalProperties": False,
                                    "properties": {
                                        "has_planarity": {"type": "string","enum": ["planar","bent","linear","pyramidal"]},
                                        "has_modularity": {"type": "string","pattern": "^[2-9]$"}
                                    },
                                    "required": ["has_planarity","has_modularity"]
                                }
                            },
                            "required": ["gbu_type_detail"]
                        }
                    }
                },
                "required": ["am_label","polyhedral_shape_symbol","symmetry_point_group","gbus"]
            },
            "gbus": {
                "type": "array","minItems": 2,"maxItems": 2,
                "items": {
                    "type": "object","additionalProperties": False,
                    "properties": {
                        "gbu_type_detail": {
                            "type": "object","additionalProperties": False,
                            "properties": {
                                "has_planarity": {"type": "string","enum": ["planar","bent","linear","pyramidal"]},
                                "has_modularity": {"type": "string","pattern": "^[2-9]$"}
                            },
                            "required": ["has_planarity","has_modularity"]
                        }
                    },
                    "required": ["gbu_type_detail"]
                }
            },
            "cbus": {
                "type": "array","minItems": 1,
                "items": {
                    "type": "object","additionalProperties": False,
                    "properties": {"cbu_formula": {"type": "string"}},
                    "required": ["cbu_formula"]
                }
            },
            "ccdc_number": {"type": "string"},
            "doi": {"type": "string"}
        },
        "required": ["mop_formula","assembly_model","gbus","cbus"]
    }
}

SYSTEM = """
Task
Derive the MOP Assembly Model (AM) and the two Generic Building Units (GBUs) from:
- PAPER_TEXT
- STRUCTURE_FILES (CIF/RES)
- CBU_FORMULAS (array of {cbu_formula})
Return JSON exactly per the given schema. Never add keys. Deterministic. Low reasoning effort.

Record isolation
- Treat each call as independent. Do not reuse metals, symbols, or counts from prior examples or filenames.

Core definitions
- GBU = {has_modularity n, has_planarity ∈ [planar,bent,linear,pyramidal]} only.
- AM = polyhedral template + symmetry hosting exactly two GBU types with multiplicities.

Geometry rules
- 2-connector classification: output "linear" ONLY if a measured connector–connector angle θ ≥ 165° from STRUCTURE_FILES OR PAPER_TEXT explicitly says “linear”. Otherwise output "bent". Never infer “linear” from phrases like “linear edges” (that is topological language).
- n ≥ 3 classification: "planar" if best-fit plane RMSD ≤ 0.30 Å AND mean angular deviation ≤ 15°; else "pyramidal".
- Chemical prior: dinuclear paddlewheel [M2] → 4-planar unless explicit counter-evidence.
- Ignore solvent and masked density (e.g., SQUEEZE) when measuring geometry; use framework atoms only.

Polyhedron selection
- Use PAPER_TEXT cues first; then STRUCTURE_FILES counts/connectivity and vertex-orbit evidence.
- Do NOT derive molecular point group from the crystal space group. Crystal space group ≠ molecular point group.
- Symbol↔point-group binding (must hold on output):
  tet↔Td; oct↔Oh; cub↔Oh; cuo↔Oh; twc↔D3h; ico↔Ih; dod↔Ih; ido↔Ih.
  If one of {polyhedral_shape_symbol, symmetry_point_group} is known, set the other per this table.
- cuo vs twc tie-break when counts are identical:
  • One metal-vertex orbit of 12 with a single vertex figure → cuo and symmetry_point_group="Oh".
  • Two metal-vertex orbits 6+6 with two vertex figures → twc and symmetry_point_group="D3h".
  If inconclusive AND PAPER_TEXT is silent, set both fields "".

CBU verification and correction (minimal, source-anchored)
- Inputs may be wrong. Verify the two CBUs from PAPER_TEXT and STRUCTURE_FILES.
- Metal CBU:
  • Read metal element(s) from PAPER_TEXT and from atomic labels/formula in STRUCTURE_FILES.
  • If input disagrees with BOTH sources, correct to the supported metal identity.
  • If a paddlewheel dimer, format as "[M2]". Do NOT append axial ligands, counterions, or solvent.
- Organic CBU:
  • If PAPER_TEXT provides a canonical linker label (e.g., "[(C6H4)(CO2)2]") and input differs, correct to that canonical string; else keep input.
- Order rule: cbus[0] = metal CBU, cbus[1] = organic CBU. If reversed, swap.
- Completeness gate:
  • If after verification you do not have exactly two CBUs AND PAPER_TEXT does not clearly assert an assembled cage, set:
    am_label = ""; polyhedral_shape_symbol = ""; symmetry_point_group = "";
    keep both gbus arrays as the default pair inferred from chemistry if and only if PAPER_TEXT states node/edge types; otherwise set both gbus arrays to the canonical pair {"planarity":"planar","modularity":"4"} and {"planarity":"bent","modularity":"2"} as a placeholder.
  • Never fabricate metals or ligands.

am_label canonicalization
- Format exactly "(<n>-<planarity>)x<count>(<n>-<planarity>)x<count>".
- planarity lower-case; counts are base-10 integers; no spaces.

mop_formula construction (no extra brackets)
- Let F0 = cbus[0].cbu_formula; F1 = cbus[1].cbu_formula. Use them verbatim; DO NOT add brackets.
- Map the higher-n GBU to F0 (metal) and the other GBU to F1 unless PAPER_TEXT states otherwise.
- Let N0, N1 be multiplicities from am_label. Output mop_formula as: F0 + N0 + F1 + N1, concatenated with no spaces.
  Example: F0="[Cr2]", F1="[(C6H4)(CO2)2]", N0=12, N1=24 → "[Cr2]12[(C6H4)(CO2)2]24".
- If either CBU is missing after verification, set mop_formula = "".

Mirror GBUs
- assembly_model.gbus and top-level gbus must contain the same two {has_planarity,has_modularity} in the same order.

Normalization
- polyhedral_shape_symbol: lower-case RCSR code (e.g., "cuo", "twc"). Leave "" if uncertain.
- symmetry_point_group: canonical Schoenflies (Oh, D3h, Td, …). Never "O_h" or mixed-case variants.
- ccdc_number and doi: copy exact strings from inputs if present; else "".

Final validation before emit (fix, don’t fail)
- If 2-connector is "linear" but no θ ≥ 165° or explicit text, change to "bent".
- Enforce symbol↔point-group binding table; if inconsistent, blank both rather than guessing.
- Check am_label counts vs chosen polyhedron role counts.
- Check mop_formula counts match am_label and respect cbus order; prevent double brackets by using CBU strings verbatim.
- Ensure both gbus arrays are mirrored and ordered consistently.
- If any check fails, correct deterministically per these rules; never add keys or invent species.

"""

def _find_stitched_md(hash_dir: Path):
    cands = list(hash_dir.glob("*.md"))
    for p in cands:
        if "stitch" in p.name.lower(): return p
    return cands[0] if cands else None

def _find_hash_md(hash_dir: Path, hv: str):
    p = hash_dir / f"{hv}.md"
    return p if p.exists() else None

def _find_ccdc_files(ccdc: str) -> tuple[Path, Path]:
    if not ccdc: return (Path(""), Path(""))
    cif = Path(DATA_DIR) / "ontologies" / "ccdc" / "cif" / f"{ccdc}.cif"
    res = Path(DATA_DIR) / "ontologies" / "ccdc" / "res" / f"{ccdc}.res"
    return (cif if cif.exists() else Path(""), res if res.exists() else Path(""))

def _build_client() -> tuple[OpenAI, str, str]:
    load_dotenv(override=True)
    api_key  = os.getenv("REMOTE_API_KEY") or os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("REMOTE_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "https://openrouter.ai/api/v1"
    model    = os.getenv("REMOTE_MODEL") or os.getenv("OPENAI_MODEL") or "openai/gpt-5"
    if not api_key: sys.exit("Set REMOTE_API_KEY or OPENROUTER_API_KEY or OPENAI_API_KEY in .env")

    headers = {}
    if "openrouter.ai" in base_url:
        headers = {
            "HTTP-Referer": os.getenv("APP_URL", "http://localhost"),
            "X-Title": os.getenv("APP_NAME", "AM/GBU Deriver"),
        }
    client = OpenAI(api_key=api_key, base_url=base_url, default_headers=headers)
    return client, base_url, model

def _parse_responses_obj(response) -> Optional[dict]:
    # OpenAI Responses API variants
    if hasattr(response, "output_parsed") and response.output_parsed is not None:
        return response.output_parsed
    # OpenRouter sometimes nests parsed under output->content
    try:
        for item in getattr(response, "output", []):
            for c in getattr(item, "content", []):
                if hasattr(c, "parsed") and c.parsed is not None:
                    return c.parsed
                if hasattr(c, "text") and c.text:
                    return json.loads(c.text)
    except Exception:
        pass
    # Fallback: try output_text
    try:
        if getattr(response, "output_text", ""):
            return json.loads(response.output_text)
    except Exception:
        pass
    return None

def _call_model(client: OpenAI, model: str, paper: str, cif_text: str, res_text: str,
                cbu_json_str: str, temperature: float) -> dict:
    # Prefer Responses API with structured outputs
    try:
        response = client.responses.create(
            model=model,
            temperature=temperature,
            instructions=SYSTEM,
            input=[{
                "role": "user",
                "content": [
                    {"type":"input_text","text": f"PAPER_TEXT:\n{paper}"},
                    {"type":"input_text","text": f"CIF:\n{cif_text}"},
                    {"type":"input_text","text": f"RES:\n{res_text}"},
                    {"type":"input_text","text": f"CBU_FORMULAS_JSON:\n{cbu_json_str}"}
                ],
            }],
            response_format={"type":"json_schema","json_schema":SCHEMA},
            extra_body={"structured_outputs": True},  # important on OpenRouter
        )
        parsed = _parse_responses_obj(response)
        if parsed is not None:
            return parsed
    except Exception as e:
        # fall through to chat completions
        pass

    # Chat Completions fallback
    schema_text = json.dumps(SCHEMA.get("schema", {}), ensure_ascii=False)
    sys_msg = SYSTEM + "\n\nReturn ONE JSON object that strictly conforms to this JSON Schema:\n" + schema_text
    messages = [
        {"role": "system", "content": sys_msg},
        {"role": "user", "content": (
            f"PAPER_TEXT:\n{paper}\n\nCIF:\n{cif_text}\n\nRES:\n{res_text}\n\nCBU_FORMULAS_JSON:\n{cbu_json_str}"
        )},
    ]
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            response_format={"type": "json_object"},
            extra_body={"structured_outputs": True},
        )
    except TypeError:
        resp = client.chat.completions.create(
            model=model, messages=messages, temperature=temperature
        )

    content = ""
    try:
        content = resp.choices[0].message.content or ""
    except Exception:
        content = ""
    if not content.strip():
        # last resort: try to locate tool/parsed payloads
        try:
            raw = resp.model_dump() if hasattr(resp, "model_dump") else resp
            return json.loads(json.dumps(raw))  # let caller inspect
        except Exception:
            raise RuntimeError("Empty completion from model and no parsed payload available.")
    try:
        return json.loads(content)
    except Exception:
        raise RuntimeError(f"Non-JSON chat response (len={len(content)}): {content[:200]!r}")

def _call_model_with_retry(
    client: OpenAI,
    model: str,
    paper: str,
    cif_text: str,
    res_text: str,
    cbu_json_str: str,
    temperature: float,
    max_retries: int = 3,
) -> dict:
    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            return _call_model(client, model, paper, cif_text, res_text, cbu_json_str, temperature)
        except Exception as e:
            last_error = e
            if attempt < max_retries:
                time.sleep(5 * attempt)
            else:
                break
    assert last_error is not None
    raise last_error

def _process_integrated_json(client: OpenAI, model: str, hv: str, integrated_path: Path,
                             temperature: float) -> tuple[str, Optional[Path]]:
    try:
        data = json.loads(integrated_path.read_text(encoding="utf-8"))
    except Exception as e:
        return (f"[{hv}] Failed reading {integrated_path.name}: {e}", None)

    hash_dir = Path(DATA_DIR) / hv
    paper_path = _find_stitched_md(hash_dir)
    paper_text = read_text(paper_path) if paper_path else ""

    ccdc = str(data.get("ccdc_number") or "").strip()
    cif_p, res_p = _find_ccdc_files(ccdc)
    cif_text = read_text(cif_p) if cif_p else ""
    res_text = read_text(res_p) if res_p else ""

    cbu_json_str = json.dumps(data, ensure_ascii=False)

    try:
        result = _call_model_with_retry(
            client, model, paper_text, cif_text, res_text, cbu_json_str, temperature, max_retries=3
        )
    except Exception as e:
        return (f"[{hv}] Inference error for {integrated_path.name}: {e}", None)

    out_dir = Path(DATA_DIR) / hv / "cbu_derivation" / "full"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / integrated_path.name
    # Keep only derived result; optionally backfill CCDC from known input if missing/empty
    final_output = result if isinstance(result, dict) else {"derived_result": result}
    try:
        if (not str(final_output.get("ccdc_number") or "").strip()) and ccdc:
            final_output["ccdc_number"] = ccdc
    except Exception:
        pass
    try:
        out_path.write_text(json.dumps(final_output, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        return (f"[{hv}] Failed writing output for {integrated_path.name}: {e}", None)

    return (f"[{hv}] OK {integrated_path.name} → {out_path.name}", out_path)

def run_batch(temperature: float = 0.0, only_hash: Optional[str] = None) -> None:
    client, base_url, model = _build_client()
    tasks: List[tuple[str, Path]] = []
    root = Path(DATA_DIR)
    for d in sorted(root.iterdir()):
        if not d.is_dir() or len(d.name) != 8: continue
        hv = d.name
        if only_hash and hv != only_hash: continue
        integ = d / "cbu_derivation" / "integrated"
        if not integ.exists(): continue
        for jf in sorted(integ.glob("*.json")):
            tasks.append((hv, jf))

    if not tasks:
        print("No integrated JSON inputs found."); return

    print(f"Found {len(tasks)} tasks. Running in parallel...")
    print(f"Using model: {model} @ {base_url}")
    max_workers = max(1, min(8, len(tasks)))  # avoid hammering provider
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = [ex.submit(_process_integrated_json, client, model, hv, jf, temperature) for hv, jf in tasks]
        ok = 0
        for fut in as_completed(futures):
            try:
                msg, path = fut.result()
                print(msg)
                if path is not None: ok += 1
            except Exception as e:
                print(f"Worker crashed: {e}")
    print(f"Done. {ok}/{len(tasks)} succeeded.")

def main():
    ap = argparse.ArgumentParser(description="Derive AM/GBUs (RCSR-ready) with structured output")
    ap.add_argument("--paper", type=Path)
    ap.add_argument("--cif", type=Path)
    ap.add_argument("--res", type=Path)
    ap.add_argument("--cbu-json", type=Path, help="Single-run mode: path to JSON input")
    ap.add_argument("--temperature", type=float, default=0.0)
    # removed max-tokens limit
    ap.add_argument("--batch", action="store_true")
    ap.add_argument("--file", type=str, help="Specific 8-char hash or arbitrary id (hashed)")
    args = ap.parse_args()

    if args.batch or (not args.paper and not args.cbu_json):
        hv = None
        if args.file:
            v = args.file.strip()
            if len(v) == 8 and (Path(DATA_DIR) / v).exists():
                hv = v
            else:
                try:
                    import hashlib
                    hv = hashlib.sha256(v.encode()).hexdigest()[:8]
                except Exception:
                    hv = None
        run_batch(temperature=args.temperature, only_hash=hv)
        return

    client, base_url, model = _build_client()  # FIX: unpack correctly
    paper = read_text(args.paper) if args.paper else ""
    cif   = read_text(args.cif) if args.cif else ""
    res   = read_text(args.res) if args.res else ""
    if not args.cbu_json: sys.exit("--cbu-json is required for single-run mode")
    cbu_json_str = args.cbu_json.read_text(encoding="utf-8")

    try:
        result = _call_model_with_retry(
            client, model, paper, cif, res, cbu_json_str, args.temperature, max_retries=3
        )
        # Ensure known CCDC number is present if missing or empty in single-run mode
        try:
            src_data = json.loads(cbu_json_str)
        except Exception:
            src_data = {}
        if isinstance(result, dict):
            known_ccdc = str((src_data or {}).get("ccdc_number") or "").strip()
            if (not str(result.get("ccdc_number") or "").strip()) and known_ccdc:
                result["ccdc_number"] = known_ccdc
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as e:
        sys.exit(f"Inference failed after 3 attempts: {e}")

if __name__ == "__main__":
    main()
