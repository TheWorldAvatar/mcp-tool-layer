# Agent B: GBUs + AM
import os, json, argparse, time
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI

 

# agent_pointgroup.py  — derive ONLY symmetry_point_group
SCHEMA = {
  "type": "object",
  "additionalProperties": False,
  "properties": {
    "symmetry_point_group": {"type": "string"}
  },
  "required": ["symmetry_point_group"]
}

SYSTEM = (
"Output JSON matching SCHEMA. Derive ONLY 'symmetry_point_group' (canonical Schoenflies) or '' if uncertain. "
"Do not output any other fields.\n\n"

"Evidence cascade (apply in order and stop at the first decisive match):\n"
"1) PAPER_TEXT explicit PG: If text explicitly states a molecular point group (e.g., 'Oh', 'D3h', 'Td', written as 'O_h', 'O h', etc.), "
"   normalize and return it.\n"
"2) PAPER_TEXT polyhedral name → canonical PG:\n"
"   - tetrahedron → Td\n"
"   - octahedron → Oh\n"
"   - cube → Oh\n"
"   - cuboctahedron (Archimedean) → Oh\n"
"   - triangular orthobicupola / anticuboctahedron / Johnson solid J27 → D3h\n"
"   - icosahedron → Ih\n"
"   - dodecahedron → Ih\n"
"   - icosidodecahedron → Ih\n"
"   If text says 'distorted <shape>', still use the idealized PG above unless the text explicitly states a lower PG.\n"
"3) Polyhedral symbol in context (if provided by the caller's input context, not crystal data): enforce binding table:\n"
"   tet→Td; oct→Oh; cub→Oh; cuo→Oh; twc→D3h; ico→Ih; dod→Ih; ido→Ih. If symbol present and mapped, return the bound PG.\n"
"4) Vertex-orbit test from the molecular graph (use CIF/RES connectivity; ignore crystal packing):\n"
"   - If all 12 metal nodes in the cage are equivalent (one metal-vertex orbit with a single vertex figure) → Oh.\n"
"   - If there are two distinct metal-vertex orbits of 6+6 with two vertex figures → D3h.\n"
"   Implement by grouping metal atoms by their intramolecular coordination environment after translating/merging symmetry-equivalent "
"   atoms into a single cage; do not use crystal space group symmetry to claim molecular PG.\n"
"5) am_label hint (if provided in context): am_label cannot determine PG by itself, but if it corresponds to a known template and no other "
"   evidence contradicts, prefer the PG consistent with that template via the binding table in step 3.\n"
"6) If still ambiguous, return an empty string ''.\n\n"

"Normalization and constraints:\n"
"- Normalize to canonical Schoenflies with correct case and no underscores/spaces: Oh, Td, Ih, D3h, D5h, D2d, etc.\n"
"- Do NOT infer molecular point group from the crystallographic space group; crystal symmetry ≠ molecular symmetry.\n"
"- If PAPER_TEXT explicitly states a PG that conflicts with the binding table, trust PAPER_TEXT for PG.\n"
"- Deterministic. No extra keys."
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
    raise RuntimeError(f"Agent Point Group failed after {max_retries} attempts: {last_err}")

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
