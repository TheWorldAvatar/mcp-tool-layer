# Agent B: GBUs + AM
import os, json, argparse, time
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI

# agent_gbus.py  — derive ONLY gbus
SCHEMA = {
  "type": "object",
  "additionalProperties": False,
  "properties": {
    "gbus": {
      "type": "array",
      "minItems": 2, "maxItems": 2,
      "items": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
          "gbu_type_detail": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
              "has_planarity": {"type": "string", "enum": ["planar","bent","linear","pyramidal"]},
              "has_modularity": {"type": "string", "pattern": "^[2-9]$"}
            },
            "required": ["has_planarity","has_modularity"]
          }
        },
        "required": ["gbu_type_detail"]
      }
    }
  },
  "required": ["gbus"]
}


SYSTEM = (
"Output JSON matching SCHEMA. Derive ONLY 'gbus'. Do not output other fields.\n\n"
"Evidence cascade (apply in order; stop at first decisive evidence for each GBU):\n"
"1) PAPER_TEXT explicit geometry: if it states a numeric connector angle or planarity class for a unit, use it after normalization.\n"
"2) STRUCTURE_FILES measurement:\n"
"   2a) For n=2 linkers: classify 'linear' ONLY if a measurable connector–connector angle θ ≥ 165° between the two outgoing link vectors.\n"
"       Otherwise 'bent'. Accept θ from M–L–M, donor–centroid–donor, or donor–pivot–donor definitions; pick the clearest available.\n"
"   2b) For n≥3 nodes: fit the best plane to the n connector vectors (or donor atom directions). If RMSD ≤ 0.30 Å AND mean angular\n"
"       deviation ≤ 15°, set 'planar'; else 'pyramidal'.\n"
"3) Chemical priors (only when 1–2 are unavailable): a dinuclear paddlewheel [M2(O2CR)4] equatorial node is '4-planar' by default unless\n"
"   STRUCTURE_FILES or PAPER_TEXT provide explicit counter-evidence.\n\n"
"Connector identification hints (non-binding):\n"
"- Treat metal carboxylate paddlewheel cores as the n≥3 candidate (n=4). Use equatorial M–O directions as connectors; ignore axial ligands.\n"
"- Treat aryl dicarboxylate linkers as n=2 candidates. Use vectors along the two donor directions towards distinct metal nodes.\n"
"- Ignore hydrogen atoms, solvent, counterions, and masked density (e.g., SQUEEZE).\n\n"
"Normalization and ordering:\n"
"- has_modularity is a digit string '2'…'9'. has_planarity ∈ {'planar','bent','linear','pyramidal'} (lowercase).\n"
"- Return exactly two GBUs: order by descending modularity. If modularities tie, put the metal-bearing unit first.\n"
"- If more than two units are detectable, choose the two that form the cage (metal node + organic linker) and discard extras.\n\n"
"Conservatism and ambiguity:\n"
"- If a numeric θ cannot be established for a 2-connector and the text is silent, classify it as 'bent'.\n"
"- If planarity metrics for n≥3 cannot be established and no counter-evidence exists, keep the paddlewheel prior '4-planar'.\n"
"- If after all steps a GBU remains indeterminate, emit the canonical pair {4-planar, 2-bent} consistent with typical MOP cages.\n\n"
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
    raise RuntimeError(f"Agent GBU failed after {max_retries} attempts: {last_err}")

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
