# Agent C: final merger + mop_formula
import json, argparse, re
from pathlib import Path
from typing import Dict, Any

AM_RX = re.compile(
    r"^\((\d+)-(planar|bent|linear|pyramidal)\)x(\d+)\((\d+)-(planar|bent|linear|pyramidal)\)x(\d+)$"
)
BIND = {"tet": "Td", "oct": "Oh", "cub": "Oh", "cuo": "Oh", "twc": "D3h", "ico": "Ih", "dod": "Ih", "ido": "Ih"}

def _canon_pg(pg: str) -> str:
    if not pg:
        return ""
    s = pg.replace("_", "").strip()
    t = s.lower()
    if t == "oh": return "Oh"
    if t == "td": return "Td"
    if t == "ih": return "Ih"
    if t == "d3h": return "D3h"
    if t == "d5h": return "D5h"
    if t == "d2d": return "D2d"
    # Fallback: first char upper, rest as-is
    return s[0].upper() + s[1:]

def _mk_mop(cbus: list, am_label: str) -> str:
    if not cbus or len(cbus) < 2 or not am_label:
        return ""
    m = AM_RX.match(am_label)
    if not m:
        return ""
    n1, _, c1, n2, _, c2 = m.groups()
    try:
        n1i, n2i = int(n1), int(n2)
        c1i, c2i = int(c1), int(c2)
    except Exception:
        return ""
    F0 = cbus[0].get("cbu_formula", "")
    F1 = cbus[1].get("cbu_formula", "")
    if not F0 or not F1:
        return ""
    # Higher modularity maps to metal CBU (cbus[0]) by convention
    if n1i >= n2i:
        N0, N1 = c1i, c2i
    else:
        N0, N1 = c2i, c1i
    return f"{F0}{N0}{F1}{N1}"

def _mirror_gbus(result: Dict[str, Any]) -> None:
    ag = result.get("assembly_model", {}).get("gbus", [])
    if isinstance(ag, list) and len(ag) == 2:
        result["gbus"] = ag

def _bind_symbol_pg(am: Dict[str, Any]) -> None:
    code = (am.get("polyhedral_shape_symbol") or "").strip().lower()
    pg = _canon_pg(am.get("symmetry_point_group") or "")
    # Enforce pairing
    if code:
        am["polyhedral_shape_symbol"] = code
        if code in BIND:
            am["symmetry_point_group"] = BIND[code]
        else:
            am["symmetry_point_group"] = pg  # unknown code, keep normalized pg
    else:
        am["symmetry_point_group"] = pg
        # If pg present and unique inverse exists, back-fill code
        inv = {}
        for k, v in BIND.items():
            inv.setdefault(v, []).append(k)
        choices = inv.get(pg, [])
        am["polyhedral_shape_symbol"] = choices[0] if len(choices) == 1 else am.get("polyhedral_shape_symbol", "")

def main():
    ap = argparse.ArgumentParser(description="Agent C: finalize JSON")
    ap.add_argument("--A", type=Path, required=True, help="Agent A output JSON path")
    ap.add_argument("--B", type=Path, required=True, help="Agent B output JSON path")
    ap.add_argument("--out", type=Path, default=Path("final.json"))
    args = ap.parse_args()

    A = json.loads(args.A.read_text(encoding="utf-8"))
    B = json.loads(args.B.read_text(encoding="utf-8"))

    # Build assembly_model and enforce symbol↔point-group binding
    am = dict(B.get("assembly_model", {}))
    _bind_symbol_pg(am)

    # Mirror GBUs into assembly_model and top-level
    gbus = B.get("gbus", [])
    am["gbus"] = gbus

    # Build mop_formula
    cbus = A.get("cbus", [])
    mop = _mk_mop(cbus, am.get("am_label", ""))

    final = {
        "mop_formula": mop,
        "assembly_model": am,
        "gbus": gbus,
        "cbus": cbus,
        "ccdc_number": A.get("ccdc_number", ""),
        "doi": A.get("doi", "")
    }

    # Minimal sanitation: empty out obviously broken mop_formula
    if "[]" in final["mop_formula"]:
        final["mop_formula"] = ""

    args.out.write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(final, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
