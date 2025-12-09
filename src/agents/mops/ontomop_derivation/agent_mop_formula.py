"""Agent to derive full MOP formula by combining metal and organic CBU formulas."""
import os
import json
import asyncio
import argparse
from pathlib import Path
from typing import Dict, List, Optional

from models.locations import DATA_DIR
from src.agents.mops.cbu_derivation.utils.io_utils import resolve_identifier_to_hash
from src.agents.mops.cbu_derivation.utils.metal_cbu import (
    safe_name as metal_safe_name,
    load_entity_extraction_content,
    extract_ccdc_from_entity_ttl,
)
from src.agents.mops.cbu_derivation.utils.cbu_general import load_res_file as util_load_res_file
from src.utils.global_logger import get_logger
from dotenv import load_dotenv
from openai import OpenAI

# Assembly Model (AM) catalog: maps pairs of GBU types to stoichiometry and symmetry
# GBU types follow the canonical strings: '2-linear', '2-bent', '3-planar', '3-pyramidal',
# '4-planar', '4-pyramidal', '5-pyramidal'
AM_CATALOG = [
    # Icosahedral-like cages
    {"a": "5-pyramidal", "b": "2-linear", "nA": 12, "nB": 30, "symmetry": "Ih"},
    {"a": "2-linear", "b": "5-pyramidal", "nA": 30, "nB": 12, "symmetry": "Ih"},

    # Cuboctahedral-like cages
    {"a": "4-planar", "b": "2-bent", "nA": 12, "nB": 24, "symmetry": "Oh"},
    {"a": "2-bent", "b": "4-planar", "nA": 24, "nB": 12, "symmetry": "Oh"},

    # Octahedral family (two dual descriptions)
    {"a": "4-pyramidal", "b": "3-planar", "nA": 6, "nB": 8, "symmetry": "Oh"},
    {"a": "3-planar", "b": "4-pyramidal", "nA": 8, "nB": 6, "symmetry": "Oh"},
    {"a": "3-pyramidal", "b": "4-planar", "nA": 6, "nB": 8, "symmetry": "Oh"},
    {"a": "4-planar", "b": "3-pyramidal", "nA": 8, "nB": 6, "symmetry": "Oh"},
]


def _classify_gbu(modularity: int, planarity: str) -> str:
    """Return canonical GBU type string from modularity and planarity descriptor.
    planarity is expected lower-case among {linear, bent, planar, pyramidal}.
    """
    p = (planarity or "").strip().lower()
    m = int(modularity)
    if m == 2:
        return "2-linear" if p == "linear" else "2-bent"
    if m == 3:
        return "3-planar" if p == "planar" else "3-pyramidal"
    if m == 4:
        return "4-planar" if p == "planar" else "4-pyramidal"
    if m == 5:
        return "5-pyramidal"
    # Fallback to pyramidal for n>=3 if unspecified
    return f"{m}-pyramidal"


def _select_compatible_ams(gbu_a: str, gbu_b: str) -> List[Dict[str, object]]:
    """Return AM entries compatible with the provided GBU types.
    Returned entries are oriented so that entry['nA'] applies to gbu_a and entry['nB'] to gbu_b.
    """
    out: List[Dict[str, object]] = []
    for entry in AM_CATALOG:
        if entry["a"] == gbu_a and entry["b"] == gbu_b:
            out.append({"gbu_a": gbu_a, "gbu_b": gbu_b, "nA": entry["nA"], "nB": entry["nB"], "symmetry": entry.get("symmetry", "")})
    return out


def _toggle_gbu_planarity(gbu: str) -> str:
    """Return the alternate GBU of same modularity with toggled linear/bent or planar/pyramidal."""
    try:
        mod, kind = gbu.split("-")
    except ValueError:
        return gbu
    if mod == "2":
        return "2-bent" if kind == "linear" else "2-linear"
    if kind == "planar":
        return f"{mod}-pyramidal"
    if kind == "pyramidal":
        return f"{mod}-planar"
    return gbu


def _format_charge_superscript(q: int) -> str:
    if q == 0:
        return ""
    sign = "+" if q > 0 else "-"
    mag = abs(q)
    return f"^{{{mag}{sign}}}"


def derive_mop_via_am(cbu_a: Dict[str, object], cbu_b: Dict[str, object]) -> Dict[str, object]:
    """Algorithmic derivation per AM catalog and valence closure.

    cbu_x schema (required fields):
      - name: str (informative)
      - formula: str (bracketed block, e.g., "[V6O6(OCH3)9(SO4)]")
      - charge: int (formal charge per CBU)
      - modularity: int (binding-site multiplicity)
      - planarity: str in {linear, bent, planar, pyramidal}
    """
    name_a = str(cbu_a.get("name") or "A")
    name_b = str(cbu_b.get("name") or "B")
    fa = str(cbu_a.get("formula") or "").strip()
    fb = str(cbu_b.get("formula") or "").strip()
    qa = int(cbu_a.get("charge"))
    qb = int(cbu_b.get("charge"))
    ma = int(cbu_a.get("modularity"))
    mb = int(cbu_b.get("modularity"))
    pa = str(cbu_a.get("planarity") or "").strip().lower()
    pb = str(cbu_b.get("planarity") or "").strip().lower()

    gbu_a = _classify_gbu(ma, pa)
    gbu_b = _classify_gbu(mb, pb)

    candidates = []
    for am in _select_compatible_ams(gbu_a, gbu_b):
        nA = int(am["nA"]) ; nB = int(am["nB"]) ; sym = str(am.get("symmetry") or "")
        closure_ok = (nA * ma) == (nB * mb)
        q_total = (nA * qa) + (nB * qb)
        charge_sup = _format_charge_superscript(q_total)
        mop = f"{fa}{nA}{fb}{nB}{charge_sup}"
        candidates.append({
            "am": f"({gbu_a}){nA}({gbu_b}){nB}",
            "symmetry": sym,
            "nA": nA,
            "nB": nB,
            "valence_closure_ok": closure_ok,
            "q_total": q_total,
            "mop_formula": mop,
        })

    # Provide nearby suggestions by toggling planarity/linearity on either GBU
    suggestions: List[Dict[str, object]] = []
    alt_pairs = [( _toggle_gbu_planarity(gbu_a), gbu_b ), ( gbu_a, _toggle_gbu_planarity(gbu_b) )]
    for gA_alt, gB_alt in alt_pairs:
        for am in _select_compatible_ams(gA_alt, gB_alt):
            nA = int(am["nA"]) ; nB = int(am["nB"]) ; sym = str(am.get("symmetry") or "")
            closure_ok = (nA * ma) == (nB * mb)
            suggestions.append({
                "alt_gbu_a": gA_alt,
                "alt_gbu_b": gB_alt,
                "am": f"({gA_alt}){nA}({gB_alt}){nB}",
                "nA": nA,
                "nB": nB,
                "valence_closure_ok": closure_ok,
                "symmetry": sym,
            })

    # Prefer first candidate that closes valence; else return first available
    best = None
    for c in candidates:
        if c["valence_closure_ok"]:
            best = c ; break
    if best is None and candidates:
        best = candidates[0]

    return {
        "cbu_a_gbu": gbu_a,
        "cbu_b_gbu": gbu_b,
        "candidates": candidates,
        "best": best,
        "reason": ("no_compatible_am" if not candidates else ("no_valence_closure" if candidates and not any(x.get("valence_closure_ok") for x in candidates) else "ok")),
        "suggestions": suggestions,
        "assumptions": [
            "Charges are taken as provided; no protonation inference performed.",
            "AM catalog limited to common families (Ih, Oh).",
        ],
    }


"""
LLM-based derivation of full MOP formula using paper content, RES/CIF, TTL, and known CBUs.
"""

# JSON schema for LLM response
SCHEMA = {
  "type": "object",
  "additionalProperties": False,
  "properties": {
    "mop_formula": {"type": "string", "minLength": 3},
    "x": {"type": "integer"},
    "y": {"type": "integer"},
    "metal_cbu_norm": {"type": "string"},
    "organic_cbu_norm": {"type": "string"},
    "degree_metal": {"type": "integer"},
    "degree_organic": {"type": "integer"},
    "notes": {"type": "string"}
  },
  "required": ["mop_formula","x","y","metal_cbu_norm","organic_cbu_norm"]
}

SYSTEM = (
"You are a MOP stoichiometry expert. Task: given two CBU formulas, return ONLY the normalized full MOP formula of the form "
"[CBU_metal]x[CBU_organic]y, where x and y are the final reported counts.\n\n"
"Strict rules:\n"
"1) Work with CBUs as provided. Do not read paper/RES/TTL unless included. Do not invent extra species.\n"
"2) Infer node connectivities (degrees) from well-known motifs:\n"
"   - [(C3N3)(C6H4)3(CO2)3] (TATB core) → 3-connected.\n"
"   - [C10H6(CO2)2] family → 2-connected.\n"
"   - [V6O6(OCH3)9(SO4)] → 6-connected.\n"
"   - [V7O10(OCH3)9] → TREAT AS EQUIVALENT TO [V6O6(OCH3)9(SO4)] FOR REPORTING, DEGREE = 6.\n"
"   - If a linker has three carboxylates around one core, assume 3-connected; two carboxylates → 2-connected.\n"
"3) Compute minimal closure x_min,y_min by solving x*m = y*n, where m=degree_metal, n=degree_organic, with smallest positive integers.\n"
"4) Choose a practical base count by scaling (x_min,y_min) up to the smallest pair with x ≥ 4. Example anchors: (5,2)→(12,30), (4,3)→(6,8), (6,3)→(4,8), (4,2)→(12,24). Use the smallest that satisfies x≥4.\n"
"5) Normalize to equal counts by bundling the lower-degree CBU: let L=lcm(m,n). Report x'=(x*m)/L and y'=(y*n)/L. This yields x'=y'.\n"
"6) Normalize names:\n"
"   - Replace [V7O10(OCH3)9] with [V6O6(OCH3)9(SO4)] in the output.\n"
"   - Keep the organic CBU string as provided unless a clear canonical synonym of the SAME species is present in the input. Do not add charges/superscripts.\n"
"7) Output JSON only (SCHEMA). mop_formula MUST be exactly '[metal]x[organic]y' with brackets and integers, no spaces, no underscores.\n"
"8) Be deterministic. temperature=0 behavior assumed.\n"
)


def _get_client():
    load_dotenv(override=True)
    # Try different API sources in order of preference
    api_key = (os.getenv("OPENAI_API_KEY") or
               os.getenv("REMOTE_API_KEY") or
               os.getenv("OPENROUTER_API_KEY"))
    base_url = (os.getenv("OPENAI_BASE_URL") or
                os.getenv("REMOTE_BASE_URL") or
                "https://api.openai.com/v1")  # Use OpenAI directly, not OpenRouter
    model = os.getenv("OPENAI_MODEL") or "gpt-4o"  # Use gpt-4o which is widely supported

    if not api_key:
        raise SystemExit("Set OPENAI_API_KEY or REMOTE_API_KEY or OPENROUTER_API_KEY")

    headers = {"HTTP-Referer": os.getenv("APP_URL", "http://localhost"),
               "X-Title": os.getenv("APP_NAME", "MOP Deriver")} if "openrouter.ai" in base_url else {}

    return OpenAI(api_key=api_key, base_url=base_url, default_headers=headers), model


def _json_dumps(obj):
    try:
        return json.dumps(obj, ensure_ascii=False)
    except Exception:
        return json.dumps(str(obj))


def call_emit(system, payload, schema, max_retries:int=3):
    client, model = _get_client()
    schema_text = json.dumps(schema, ensure_ascii=False)
    sys_msg = system + "\n\nReturn ONLY a JSON object that strictly conforms to this JSON Schema:\n" + schema_text
    messages = [
        {"role": "system", "content": sys_msg},
        {"role": "user", "content": _json_dumps(payload)},
    ]
    last_err = None
    for attempt in range(1, max_retries+1):
        try:
            # Use native OpenAI API with structured output
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0,
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content or ""
            if not content.strip():
                continue
            return json.loads(content)
        except Exception as e:
            last_err = e
            if attempt < max_retries:
                import time
                time.sleep(5 * attempt)
            else:
                break
    raise RuntimeError(f"MOP agent failed after {max_retries} attempts: {last_err}")


async def derive_mop_formula(
    metal_cbu: str,
    organic_cbu: str,
    *,
    cbu_a: Dict[str, object] = None,
    cbu_b: Dict[str, object] = None,
) -> Dict[str, object]:
    """Derive MOP formula via Assembly Model (AM) reasoning, no free-form LLM glue.

    Either provide only formulas (legacy) or full CBU descriptors in cbu_a/cbu_b to enable
    AM selection, valence closure, and charge accounting.
    """
    logger = get_logger("agent", "MOPFormula")

    if cbu_a is None or cbu_b is None:
        # No descriptors: cannot do AM reasoning – return structured error
        return {"mop_formula": "N/A", "best": None, "candidates": [], "reason": "missing_cbu_descriptors"}

    result = derive_mop_via_am(cbu_a, cbu_b)
    return result


def derive_mop_formula_llm(metal_cbu: str, organic_cbu: str) -> Dict[str, object]:
    payload = {"metal_cbu": metal_cbu, "organic_cbu": organic_cbu}
    out = call_emit(SYSTEM, payload, SCHEMA, max_retries=3)
    try:
        out["mop_formula"] = (out.get("mop_formula") or "").replace(" ", "")
    except Exception:
        pass
    return out


def _read_metal_cbu_formula(hash_value: str, entity_name: str) -> str:
    """Read metal CBU formula from structured outputs."""
    root = os.path.join(DATA_DIR, hash_value, "cbu_derivation", "metal", "structured")
    
    # Convert entity name to safe file name (spaces to underscores)
    safe_entity = entity_name.replace(' ', '_')
    
    # Try JSON first
    json_path = os.path.join(root, f"{safe_entity}.json")
    if os.path.exists(json_path):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                j = json.load(f)
            mc = j.get("metal_cbu")
            if isinstance(mc, str):
                return mc.strip()
            elif isinstance(mc, dict):
                return (mc.get("formula") or "").strip()
        except Exception:
            pass
    
    # Fallback to txt
    txt_path = os.path.join(root, f"{safe_entity}.txt")
    if os.path.exists(txt_path):
        try:
            with open(txt_path, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            pass
    
    return ""


def _read_organic_cbu_formula(hash_value: str, entity_name: str) -> str:
    """Read organic CBU formula from structured outputs."""
    root = os.path.join(DATA_DIR, hash_value, "cbu_derivation", "organic", "structured")
    
    # Convert entity name to safe file name (spaces to underscores)
    safe_entity = entity_name.replace(' ', '_')
    
    # Try JSON first
    json_path = os.path.join(root, f"{safe_entity}.json")
    if os.path.exists(json_path):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                j = json.load(f)
            oc = j.get("organic_cbu")
            if isinstance(oc, str):
                return oc.strip()
            elif isinstance(oc, dict):
                return (oc.get("formula") or "").strip()
        except Exception:
            pass
    
    # Fallback to txt
    txt_path = os.path.join(root, f"{safe_entity}.txt")
    if os.path.exists(txt_path):
        try:
            with open(txt_path, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            pass
    
    return ""


def _find_top_entities(hash_value: str) -> List[str]:
    """Derive top-level entity names from ontomops_output/ontomops_extension_*.ttl filenames."""
    out: List[str] = []
    ttl_dir = os.path.join(DATA_DIR, hash_value, "ontomops_output")
    if not os.path.isdir(ttl_dir):
        return out

    # Load mapping file to convert filenames to actual entity labels
    mapping_file = os.path.join(ttl_dir, "ontomops_output_mapping.json")
    filename_to_label = {}  # Maps filename -> actual entity label
    if os.path.exists(mapping_file):
        try:
            with open(mapping_file, 'r', encoding='utf-8') as mf:
                mapping = json.load(mf)
                # Reverse mapping: filename -> entity_label
                for entity_label, filename in mapping.items():
                    if not entity_label.startswith("https://"):  # Skip IRI entries, keep only label entries
                        filename_to_label[filename] = entity_label
        except Exception:
            pass

    for name in sorted(os.listdir(ttl_dir)):
        if not name.startswith("ontomops_extension_") or not name.endswith(".ttl"):
            continue
        # Try to get actual entity label from mapping, fallback to filename-based label
        actual_entity_label = filename_to_label.get(name, name[len("ontomops_extension_"):-len(".ttl")])
        if actual_entity_label:
            out.append(actual_entity_label)
    return out


def _process_entity_sync(hash_value: str, entity: str, output_dir: str, test_mode: bool, idx: int, total: int) -> None:
    logger = get_logger("agent", "MOPFormula")
    print(f"🔬 [{idx}/{total}] Deriving MOP formula for: {entity}")

    # Read metal and organic CBU formulas
    metal_formula = _read_metal_cbu_formula(hash_value, entity)
    organic_formula = _read_organic_cbu_formula(hash_value, entity)

    if not metal_formula or not organic_formula:
        logger.warning(f"Skipping {entity}: missing CBU formulas (metal={bool(metal_formula)}, organic={bool(organic_formula)})")
        return

    # Check if already processed
    out_json = os.path.join(output_dir, f"{entity}.json")
    if os.path.exists(out_json) and not test_mode:
        logger.info(f"⏭️  Skipping {entity}: already processed")
        return

    # LLM-first minimal call using only CBUs
    llm_out = derive_mop_formula_llm(metal_formula, organic_formula)
    # Strip any charge superscript from the returned formula to enforce display standard
    raw_formula = (llm_out or {}).get("mop_formula") or "N/A"
    try:
        import re
        mop_formula = re.sub(r"\^\{[^}]+\}$", "", raw_formula).strip()
    except Exception:
        mop_formula = raw_formula

    if not mop_formula or mop_formula.upper() == "N/A":
        # Print detailed debugging info
        debug_block = {
            "entity": entity,
            "metal_cbu": metal_formula,
            "organic_cbu": organic_formula,
            "llm_raw": llm_out,
        }
        try:
            print("[MOP-DERIVE][DEBUG] " + json.dumps(debug_block, ensure_ascii=False))
        except Exception:
            print("[MOP-DERIVE][DEBUG] (unserializable debug block)")
        error_msg = f"❌ CRITICAL: Failed to derive MOP formula for {entity}. " \
                   f"Metal CBU: '{metal_formula}', Organic CBU: '{organic_formula}'. " \
                   f"This indicates LLM failure in MOP formula derivation."
        logger.error(error_msg)
        raise RuntimeError(error_msg)

    # Write outputs
    output_data = {
        "entity": entity,
        "metal_cbu": metal_formula,
        "organic_cbu": organic_formula,
        "mop_formula": mop_formula,
        "am": (llm_out or {}).get("am") or "",
        "symmetry": (llm_out or {}).get("symmetry") or "",
        "q_total": (llm_out or {}).get("q_total"),
        "nA": (llm_out or {}).get("nA"),
        "nB": (llm_out or {}).get("nB"),
    }

    # Write JSON
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)

    # Write TXT (just the formula)
    out_txt = os.path.join(output_dir, f"{entity}.txt")
    with open(out_txt, "w", encoding="utf-8") as f:
        f.write(mop_formula)

    logger.info(f"✅ Derived: {mop_formula}")


async def run_for_hash(hash_value: str, test_mode: bool = False):
    """Run MOP formula derivation for all entities in a hash with parallelization."""
    logger = get_logger("agent", "MOPFormula")

    # Setup output directory
    output_dir = os.path.join(DATA_DIR, hash_value, "cbu_derivation", "full")
    os.makedirs(output_dir, exist_ok=True)

    # Get entities
    entities = _find_top_entities(hash_value)
    if not entities:
        logger.warning(f"No entities found for hash {hash_value}")
        return True

    total = len(entities)
    logger.info(f"Processing {total} entities for hash {hash_value}")

    # Flexible concurrency: min(env limit, number of entities)
    try:
        max_conc = int(os.getenv("MOP_FORMULA_MAX_CONCURRENCY", "8"))
        if max_conc < 1:
            max_conc = 1
    except Exception:
        max_conc = 8
    concurrency = min(max_conc, total)

    sem = asyncio.Semaphore(concurrency)

    async def _worker(i: int, ent: str):
        async with sem:
            # run blocking work in a thread to avoid blocking the event loop
            await asyncio.to_thread(_process_entity_sync, hash_value, ent, output_dir, test_mode, i, total)

    tasks = [asyncio.create_task(_worker(idx, entity)) for idx, entity in enumerate(entities, 1)]
    await asyncio.gather(*tasks)

    print(f"✅ MOP formula derivation completed. Outputs: {output_dir}")
    return True


async def main():
    parser = argparse.ArgumentParser(description='MOP Formula Derivation Agent')
    parser.add_argument('--file', type=str, required=True, help='Run for specific DOI or hash')
    parser.add_argument('--test', action='store_true', help='Test mode (reprocess existing)')
    args = parser.parse_args()
    
    hash_value = resolve_identifier_to_hash(args.file)
    print(f"Running MOP formula derivation for hash: {hash_value}")
    await run_for_hash(hash_value, test_mode=args.test)


if __name__ == "__main__":
    asyncio.run(main())

