# Agent B: GBUs + AM
import os, json, argparse, time
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI

# agent_symbol.py  — derive ONLY polyhedral_shape_symbol
SCHEMA = {
  "type": "object",
  "additionalProperties": False,
  "properties": {
    "polyhedral_shape_symbol": {"type": "string"}
  },
  "required": ["polyhedral_shape_symbol"]
}

# agent_symbol.py — derive ONLY polyhedral_shape_symbol

SCHEMA = {
  "type": "object",
  "additionalProperties": False,
  "properties": {
    "polyhedral_shape_symbol": {"type": "string"}
  },
  "required": ["polyhedral_shape_symbol"]
}


SYSTEM = (
"Output JSON matching SCHEMA. Derive ONLY 'polyhedral_shape_symbol' as a lowercase RCSR code, or '' if uncertain. "
"Do not output any other fields.\n\n"

"Hard rule against over-defaulting:\n"
"- Never choose 'cuo' because the cage has 12 metals or is called 'cuboctahedral' without qualifier. "
"Require an explicit code/name or a one-orbit test. Otherwise return ''.\n\n"

"Pre-normalization of noisy inputs (no external tools needed):\n"
"- CBU sanitization: from each 'cbu_formula' keep only the core scaffold and carboxylate count. "
"  Strip alkyl/aryl substituents and positions (e.g., tBu, Me, 5-). "
"  Examples: '[Cr2]'→'[M2]'; '[(C6H4)(CO2)2]'→'[benzene-(CO2)2]'; "
"  '[((C6H3)C(CH3)3)(CO2)2]'→'[benzene-(CO2)2]'.\n"
"- Map sanitized CBUs to GBU types:\n"
"  '[M2]' with carboxylates → paddlewheel node, 4-planar, equatorial connectors only.\n"
"  '[benzene-(CO2)2]' or isophthalate family → ditopic dicarboxylate linker, 2-bent, rigid.\n"
"- Treat substituents as non-structural for AM selection.\n\n"

"Decision order (apply in order and stop at first decisive match):\n"
"1) PAPER_TEXT explicit RCSR code present (tet, oct, cub, cuo, twc, ico, dod, ido) → normalize and return.\n"
"2) PAPER_TEXT polyhedral name → code (normalize synonyms; 'distorted <name>' still maps unless a different code is stated):\n"
"   tetrahedron→tet; octahedron→oct; cube→cub; Archimedean/regular cuboctahedron→cuo; "
"   triangular orthobicupola / anticuboctahedron / Johnson solid J27 / J27-type→twc; "
"   icosahedron→ico; dodecahedron→dod; icosidodecahedron→ido.\n"
"   NOTE: the bare word 'cuboctahedral' is ambiguous; do not commit to 'cuo' from that word alone.\n"
"3) Vertex-count filter from a single discrete cage molecule (ignore packing and Z):\n"
"   4→tet; 6→oct; 8→cub; 12→{cuo,twc,ico}; 20→dod; 30→ido. Prune incompatible options.\n"
"4) High-precision prior from CBU types (soft prior, never final by itself):\n"
"   If node=4-planar paddlewheel and linker=ditopic 2-bent dicarboxylate and vertex-count=12, "
"   prefer 'twc' unless step 1–2 indicate otherwise OR step 5 finds a single metal orbit.\n"
"5) Orbit test for 12-vertex cages using intramolecular connectivity in CIF/RES (ignore axial donors on paddlewheels):\n"
"   - Build the cage graph. Identify metal vertices by element in [M2(O2CR)4] units; connectors are equatorial M–O directions. "
"     Do not add M–M dimer as an edge.\n"
"   - Per-metal fingerprint: degree≈4; sorted equatorial M–O distances; local edge-angle histogram; axial donor presence/absence.\n"
"   - Cluster identical fingerprints:\n"
"       • One orbit of 12 → 'cuo'.\n"
"       • Two orbits of 6+6 with distinct vertex figures → 'twc'.\n"
"   Implementation cues when bonds absent: infer M–O by distance thresholds ~1.8–2.3 Å. "
"   CIF labels with two metal site types of equal count (e.g., Cr1/Cr2) support 6+6.\n"
"6) Molecular point-group hint from the isolated molecule only (not crystal space group):\n"
"   D3h favors 'twc'; Oh favors 'cuo'/'oct'/'cub'. Use only as a tie-breaker after step 3.\n"
"7) If no decisive evidence after steps 1–6, return ''.\n\n"

"Constraints:\n"
"- Emit a single lowercase RCSR code or ''. No extra keys.\n"
"- Never infer from crystal symmetry or packing features.\n"
"- Do not treat generic phrases like 'square faces' or 'linear edges' as sufficient evidence for 'cuo' without step 1–2 or the orbit test.\n"
"Deterministic."
)



def _get_client():
    load_dotenv(override=True)
    api_key=os.getenv("REMOTE_API_KEY") or os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")
    base=os.getenv("REMOTE_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "https://openrouter.ai/api/v1"
    model=os.getenv("REMOTE_MODEL") or os.getenv("OPENAI_MODEL") or "openai/gpt-5"
    if not api_key: raise SystemExit("Set REMOTE_API_KEY or OPENROUTER_API_KEY or OPENAI_API_KEY")
    headers={"HTTP-Referer":os.getenv("APP_URL","http://localhost"),"X-Title":os.getenv("APP_NAME","AM-GBU Deriver")} if "openrouter.ai" in base else {}
    return OpenAI(api_key=api_key, base_url=base, default_headers=headers), model

def _read(p:Path)->str:
    if not p: return ""
    try: b=p.read_bytes()
    except: return ""
    try: return b.decode("utf-8","replace")
    except: return b.decode("latin-1","replace")

def call_emit(system,payload,schema,max_tokens=1400,max_retries:int=3):
    client,model=_get_client()
    schema_text=json.dumps(schema.get("schema",schema),ensure_ascii=False)
    sys_msg=system+"\n\nReturn ONE JSON object that strictly conforms to this JSON Schema:\n"+schema_text
    messages=[
        {"role":"system","content":sys_msg},
        {"role":"user","content": json.dumps(payload,ensure_ascii=False)},
    ]
    last_err=None
    for attempt in range(1, max_retries+1):
        try:
            try:
                r=client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=0,
                    response_format={"type":"json_object"},
                    extra_body={"structured_outputs":True},
                )
            except TypeError:
                r=client.chat.completions.create(model=model,messages=messages,temperature=0)
            content=""
            try:
                content=r.choices[0].message.content or ""
            except Exception:
                content=""
            if not content.strip():
                raw=r.model_dump() if hasattr(r,"model_dump") else r
                return json.loads(json.dumps(raw))
            return json.loads(content)
        except Exception as e:
            last_err=e
            if attempt<max_retries:
                time.sleep(5*attempt)
            else:
                break
    raise RuntimeError(f"Agent Polyhedral Symbol failed after {max_retries} attempts: {last_err}")

def main():
    ap=argparse.ArgumentParser(description="Agent B: GBUs + AM")
    ap.add_argument("--paper",type=Path,required=True)
    ap.add_argument("--cif",type=Path)
    ap.add_argument("--res",type=Path)
    ap.add_argument("--cbus",type=Path,required=True,help="Output from Agent A or same shape")
    ap.add_argument("--out",type=Path,default=Path("B_gbus_am.json"))
    a=ap.parse_args()
    paper=_read(a.paper); cif=_read(a.cif) if a.cif else ""; res=_read(a.res) if a.res else ""
    cbus_obj=json.loads(a.cbus.read_text(encoding="utf-8"))
    cbus=cbus_obj.get("cbus") if isinstance(cbus_obj,dict) else cbus_obj
    out=call_emit(SYSTEM,{"paper":paper,"cif":cif,"res":res,"cbus":cbus},SCHEMA)
    a.out.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8"); print(json.dumps(out,ensure_ascii=False,indent=2))

if __name__=="__main__": main()
