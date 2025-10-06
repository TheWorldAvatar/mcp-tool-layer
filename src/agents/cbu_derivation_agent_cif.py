#!/usr/bin/env python3
"""
This agent derives the CBUs, organic and inorganic, from the paper content
using only RES (SHELXL refinement file) and the concentrated paper content.
"""

from models.ModelConfig import ModelConfig
from src.utils.global_logger import get_logger
from models.locations import PLAYGROUND_DIR
import os, sys, json, time, asyncio, glob
from datetime import datetime
from tqdm import tqdm
from models.LLMCreator import LLMCreator

# ------------------------------- IO helpers -------------------------------

def iterate_over_cbu_ground_truth_json():
    files = glob.glob(os.path.join(PLAYGROUND_DIR, "data", "triple_compare", "cbu", "*", "*ground_truth.json"))
    result = {}
    for fp in files:
        with open(fp, "r", encoding="utf-8") as f:
            content = json.load(f)
        doi = os.path.basename(fp).replace("_cbu_ground_truth.json", "")
        ccdc_numbers = [e.get("mopCCDCNumber") for e in content.get("synthesisProcedures", [])]
        result[doi] = ccdc_numbers
    return result

def load_ground_truth_json(doi: str, ccdc_number: str):
    path = os.path.join(PLAYGROUND_DIR, "data", "triple_compare", "cbu", doi, f"{doi}_cbu_ground_truth.json")
    with open(path, "r", encoding="utf-8") as f:
        content = json.load(f)
    for entry in content.get("synthesisProcedures", []):
        if entry.get("mopCCDCNumber") == ccdc_number:
            return entry
    return None

def load_paper_content(doi: str):
    path = os.path.join(PLAYGROUND_DIR, "data", "triple_compare", "cbu", doi, f"{doi}_complete.md")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def _strip_cif_semicolon_delimiters(text: str) -> str:
    lines = text.splitlines()
    if lines and lines[0].strip() == ';':
        lines = lines[1:]
    if lines and lines[-1].strip() == ';':
        lines = lines[:-1]
    return "\n".join(line.rstrip() for line in lines).strip() + "\n"

def load_res_file(res_path: str) -> str:
    if not os.path.exists(res_path):
        raise FileNotFoundError(f"Res file not found at {res_path}")
    with open(res_path, "r", encoding="utf-8") as f:
        txt = f.read()
    return _strip_cif_semicolon_delimiters(txt)

# ------------------------------- Agent -------------------------------

class CBUDerivationAgent:
    def __init__(self, doi: str, res_path: str, concentrate: bool = False, cbu_model: str = "gpt-5-mini"):
        self.model_config = ModelConfig(temperature=0.1, top_p=0.1)
        self.logger = get_logger("agent", "CBUDerivationAgent")
        self.llm_creator = LLMCreator(model=cbu_model, remote_model=True,
                                      model_config=self.model_config,
                                      structured_output=False,
                                      structured_output_schema=None)
        self.llm = self.llm_creator.setup_llm()

        self.raw_paper_content = load_paper_content(doi)
        self.res_text = load_res_file(res_path)
        self.concentrate = concentrate

        if self.concentrate:
            conc_path = os.path.join(PLAYGROUND_DIR, "data", "triple_compare", "cbu", doi,
                                     f"{doi}_concentrated_paper_content.md")
            if os.path.exists(conc_path):
                print("using concentrated paper content")
                with open(conc_path, "r", encoding="utf-8") as f:
                    self.concentrated_paper_content = f.read()
            else:
                print("concentrating paper content")
                self.concentrated_paper_content = self.llm.invoke(
                    f"Concentrate the paper content to only info needed for deriving CBUs. "
                    f"Do not invent content.\n\n{self.raw_paper_content}"
                ).content
                with open(conc_path, "w", encoding="utf-8") as f:
                    f.write(self.concentrated_paper_content)
        else:
            print("using raw paper content")
            self.concentrated_paper_content = self.raw_paper_content

    async def run(self):
        paper_content = self.concentrated_paper_content.strip()
        INSTRUCTION_PROMPT = f"""
Your task: derive the Chemical Building Units (CBUs) of the given MOP and output two bracketed formulas.

OUTPUT
- Exactly two lines: line 1 = metal CBU, line 2 = organic CBU.
- Each line is one bracketed formula. No prose.

PRIMARY SOURCES
- SHELXL RES (below): use for metal counts, oxo/hydroxo/methoxy, SO4/PO4/VO4/PhPO3 anions, and symmetry.
- Paper content: use for linker identity/topology and final (CO2)x.

RULES
- Suppress “1” coefficients, drop zero-count terms.
- Metal line: merge all metals; order = metals → O/OH → anions → OCH3 → optional neutral coligands.
- Organic line: concatenate fragments (CkHn...) with multipliers; end with (CO2)x.
- Aryl H rule: two attachments → (C6H4).
- Only use (C2) if explicit C≡C.
- Strict self-check: no metals on line 2, no stray fragments.

SHELXL RES:
{self.res_text}

Paper content:
{paper_content}
"""
        return self.llm.invoke(INSTRUCTION_PROMPT).content

# ------------------------------- CLI -------------------------------
"""
RUNNING WITH CONCENTRATION
====================================================================================================
Doi: 10.1021_ic402428m
--------------------------------------------------
MOP CCDC Number: 950333
Concentrated Iteration 1
[Zr3O1(OH)3(C5H5)3]
[(C8H4)6(CO2)6]
Time taken: 4.706637382507324 seconds
--------------------------------------------------
Ground truth metal CBU: [Zr3O(OH)3(C5H5)3]
Ground truth organic CBU: [(C6H3)(C6H4)3(CO2)3]
"""
if __name__ == "__main__":
    doi = "10.1021_ic402428m"
    ccdc_number = "950333"
    res_file = "data/ccdc/res/950333.res"

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    cbu_model = "gpt-5"
    output_file = f"cbu_derivation_output_{timestamp}_{cbu_model}.txt"

    class Tee:
        def __init__(self, *files): self.files = files
        def write(self, obj): [f.write(obj) or f.flush() for f in self.files]
        def flush(self): [f.flush() for f in self.files]

    f = open(output_file, 'a', encoding="utf-8")
    original_stdout = sys.stdout
    sys.stdout = Tee(sys.stdout, f)

    try:
        agent = CBUDerivationAgent(doi=doi, res_path=res_file, concentrate=True, cbu_model=cbu_model)
        print("="*100)
        print(f"DOI {doi}, CCDC {ccdc_number}")
        START = time.time()
        response = asyncio.run(agent.run())
        print(response)
        print(f"Time taken: {time.time() - START:.2f} s")
        gt = load_ground_truth_json(doi, ccdc_number)
        if gt:
            print(f"Ground truth metal CBU:   {gt['cbuFormula1']}")
            print(f"Ground truth organic CBU: {gt['cbuFormula2']}")
        print("="*100)
    finally:
        sys.stdout = original_stdout
        f.close()
        print(f"\nOutput saved to: {output_file}")
