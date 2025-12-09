# Agent B: GBUs + AM
import os, json, argparse, time
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI

# agent_am.py  — derive ONLY am_label
SCHEMA = {
  "type": "object",
  "additionalProperties": False,
  "properties": {
    "am_label": {"type": "string"}
  },
  "required": ["am_label"]
}


SYSTEM = (
"Output JSON matching SCHEMA. Derive ONLY 'am_label'; else return an empty string ''. Do not output other fields.\n\n"
"Definition:\n"
"- am_label encodes two roles: '(n1-planarity1)xN1(n2-planarity2)xN2'. Lowercase planarity. No spaces. Base-10 integers.\n\n"
"Role identity (order and types):\n"
"1) If GBUs are present in context, you MUST use them:\n"
"   - Role 1 = gbus[0] (higher modularity). Role 2 = gbus[1].\n"
"   - n1 = gbus[0].has_modularity; planarity1 = gbus[0].has_planarity; same for role 2.\n"
"2) If GBUs are absent:\n"
"   - Assume the canonical pair {4-planar metal node, 2-bent organic linker}.\n"
"   - Set role order as (4-planar) first, (2-bent) second.\n\n"
"Counting N1 and N2 (multiplicities per single cage molecule, NOT per unit cell):\n"
"A) PAPER_TEXT override: If the paper explicitly states the numbers of metal nodes and linkers in the cage, use them.\n"
"B) Molecular graph from CIF/RES (ignore crystal packing and Z):\n"
"   - Build only the discrete cage (remove symmetry translations; keep one molecule).\n"
"   - Count unique sites per role by connectivity:\n"
"     • 4-planar paddlewheel nodes: count distinct [M2] nodes in the cage.\n"
"     • 2-connector linkers: count distinct ditopic linkers bridging nodes.\n"
"   - Use unique-site multiplicities if sites split into orbits (e.g., 6+6 metals). Sum to N1 or N2 respectively.\n"
"C) Polyhedral template hint (if a reliable polyhedron name/symbol is present in context):\n"
"   - If the template unambiguously fixes counts AND matches the role pair above, adopt those counts (e.g., common MOP cages yield 12 metal nodes and 24 linkers).\n"
"D) Tie-breakers when counts remain ambiguous and text is silent:\n"
"   - Prefer N1 consistent with a single metal-node type of multiplicity 12; set N2 to the linker count implied by edges for that cage (often 24).\n"
"   - If the structure is not a discrete cage or counts cannot be established without crystal packing, return '' for am_label.\n\n"
"Hard constraints:\n"
"- Never infer counts from the crystal space group; crystal symmetry ≠ molecular symmetry.\n"
"- Do not swap role order once chosen (GBUs present → gbus order; GBUs absent → 4-planar first).\n"
"- Planarity tokens must be exactly one of: planar, bent, linear, pyramidal (lowercase).\n"
"- n1 and n2 are '2'…'9'. N1 and N2 are positive integers.\n"
"- If any required element (n, planarity, or a multiplicity) is unknown, return am_label=''.\n\n"
"Validation before emit:\n"
"- Regex must match: ^\\((\\d+)-(planar|bent|linear|pyramidal)\\)x(\\d+)\\((\\d+)-(planar|bent|linear|pyramidal)\\)x(\\d+)$\n"
"- If GBUs were provided, ensure n1/planarity1 and n2/planarity2 match them exactly; else output ''.\n"
"- Multiplicities must describe a single molecule (no Z multiplication). If detection is uncertain, output ''.\n"
"Deterministic. No extra keys."
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
    raise RuntimeError(f"Agent AM failed after {max_retries} attempts: {last_err}")

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
