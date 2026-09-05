"""Project eval30 extraction ledgers into merged_tll JSON for the existing scorers."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import importlib.util

from src.pipelines.main_ontology_extractions.extract import _safe_name

_HINTS_SPEC = importlib.util.spec_from_file_location(
    "score_extraction_hints_against_gt",
    REPO / "scripts" / "score_extraction_hints_against_gt.py",
)
_HINTS_MOD = importlib.util.module_from_spec(_HINTS_SPEC)
assert _HINTS_SPEC and _HINTS_SPEC.loader
_HINTS_SPEC.loader.exec_module(_HINTS_MOD)
parse_semantic_steps = _HINTS_MOD.parse_semantic_steps

OS_OM = REPO / "scenarios/mops/runs/20260822_eval30_os_om"
R2 = REPO / "scenarios/mops/runs/20260823_eval30_ext30-r2"
OUT = R2 / "evaluation/extraction_projected/merged_tll"
HINT_RUNS = [
    "20260822_eval30_last6-v4-e2e",
    "20260822_eval30_problem2-v4-atm-e2e",
    "20260821_eval30_next12-v4-e2e",
    "20260821_eval30_next6-kg1-e2e",
    "20260820_eval30_pubchem-dedup-e2e",
    "20260820_eval30_presence-e2e-p6",
    "20260819_eval30_ontosyn-kg-queue",
]
SKIP = {"", "n/a", "na", "none", "-1", "-1.0"}
HASH_TO_DOI = {
    value: key
    for key, value in json.loads((REPO / "data/doi_to_hash.json").read_text(encoding="utf-8")).items()
    if str(key).startswith("10.")
}
NA_CHAR = {
    "ElementalAnalysis": {
        "chemicalFormula": "N/A",
        "weightPercentageCalculated": "N/A",
        "weightPercentageExperimental": "N/A",
    },
    "HNMR": {"shifts": "N/A", "solvent": "N/A", "temperature": "N/A"},
    "InfraredSpectroscopy": {"bands": "N/A", "material": "N/A"},
}


def _ok(value: Any) -> bool:
    return str(value or "").strip().casefold() not in SKIP


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _fp(text: str) -> str:
    t = str(text or "").casefold().replace("μ", "mu").replace("µ", "mu").replace("·", "")
    for prefix in (
        "synthesis of ",
        "preparation of ",
        "structural transformation from ",
        "synthesis_of_",
        "preparation_of_",
    ):
        if t.startswith(prefix):
            t = t[len(prefix) :]
    return re.sub(r"[^a-z0-9]+", "", t)


def _aliases(text: str) -> list[str]:
    raw = str(text or "").strip()
    if not raw:
        return []
    found = [raw]
    found.extend(re.findall(r"\(([^()]{2,120})\)", raw))
    found.extend(re.findall(r"\[([^\[\]]{2,120})\]", raw))
    return found


def _is_code(fp: str) -> bool:
    return len(fp) >= 3 and any(c.isalpha() for c in fp) and any(c.isdigit() for c in fp)


def _keys(names: list[str]) -> list[str]:
    keys: list[str] = []
    seen: set[str] = set()
    for name in names:
        for alias in _aliases(name) + [_safe_name(name)]:
            fp = _fp(alias)
            if not fp or fp in seen:
                continue
            if len(fp) < 3 and not _is_code(fp):
                continue
            seen.add(fp)
            keys.append(fp)
    return keys


def _file_stems(path: Path) -> tuple[str, str]:
    stem = path.stem
    for prefix in ("iter3_hints_", "extraction_"):
        if stem.startswith(prefix):
            stem = stem[len(prefix) :]
    core = stem.rsplit("--", 1)[0] if "--" in stem else stem
    return stem, core


def _metals(text: str) -> set[str]:
    return set(re.findall(r"(?:cu|mo|ni|co|zn|zr|cr|fe|mg|pd|v)\d+", _fp(text)))


def _formula_frags(text: str) -> set[str]:
    return {
        f"c{int(carbon)}h{int(hydrogen)}o{int(oxygen)}"
        for carbon, hydrogen, oxygen in re.findall(r"C(\d+)H(\d+)O(\d+)", str(text), flags=re.I)
    }


def _file_score(path: Path, names: list[str]) -> int:
    full, core = _file_stems(path)
    exact = {_fp(n) for n in names} | {_fp(_safe_name(n)) for n in names} | {_safe_name(n).casefold() for n in names}
    exact.discard("")
    got = {_fp(full), _fp(core), full.casefold(), core.casefold()}
    if got & exact:
        return 10_000 + max(len(item) for item in (got & exact))
    blob = _fp(full) + _fp(core)
    score = 0
    for key in _keys(names):
        if len(key) < 6 and not _is_code(key):
            continue
        if key in blob or (len(key) >= 8 and blob in key):
            score += len(key)
    name_metals = _metals("".join(names))
    file_metals = _metals(blob)
    if name_metals and file_metals and name_metals.isdisjoint(file_metals):
        return 0
    name_frags = _formula_frags(" ".join(names))
    file_frags = _formula_frags(f"{path.name} {full} {core}")
    if name_frags and file_frags and name_frags.isdisjoint(file_frags):
        return 0
    return score


def assign_files(paths: list[Path], name_lists: list[list[str]]) -> list[Path | None]:
    unused = set(paths)
    assigned: list[Path | None] = [None] * len(name_lists)
    ranked: list[tuple[int, int, Path]] = []
    for index, names in enumerate(name_lists):
        for path in paths:
            score = _file_score(path, names)
            if score:
                ranked.append((score, index, path))
    ranked.sort(key=lambda row: (-row[0], row[1], row[2].name))
    taken: set[int] = set()
    for _score, index, path in ranked:
        if index in taken or path not in unused:
            continue
        assigned[index] = path
        taken.add(index)
        unused.discard(path)
    if len(unused) == 1:
        leftover = [index for index, got in enumerate(assigned) if got is None]
        if len(leftover) == 1:
            path = next(iter(unused))
            names = name_lists[leftover[0]]
            if _file_score(path, names) or not _metals("".join(names)):
                assigned[leftover[0]] = path
    return assigned


def load_top_labels(mcp: Path | None) -> list[str]:
    if not mcp:
        return []
    path = mcp / "iter1_top_entities.json"
    if not path.exists():
        return []
    data = _load(path)
    if not isinstance(data, list):
        return []
    return [
        str(row.get("label") or "").strip()
        for row in data
        if isinstance(row, dict) and str(row.get("label") or "").strip()
    ]


def match_top_label(labels: list[str], names: list[str]) -> str | None:
    keys = _keys(names)
    if not keys or not labels:
        return None
    name_metals = _metals("".join(names))
    scored: list[tuple[int, str]] = []
    for label in labels:
        if name_metals:
            lab_metals = _metals(label)
            if lab_metals and name_metals.isdisjoint(lab_metals):
                continue
        name_frags = _formula_frags("".join(names))
        lab_frags = _formula_frags(label)
        if name_frags and lab_frags and name_frags.isdisjoint(lab_frags):
            continue
        blob = _fp(label) + "".join(_fp(alias) for alias in _aliases(label))
        score = sum(len(key) for key in keys if key in blob)
        if score:
            scored.append((score, label))
    if not scored:
        return None
    scored.sort(key=lambda row: (-row[0], -len(row[1])))
    if len(scored) > 1 and scored[0][0] < scored[1][0] + 6:
        return None
    return scored[0][1]


def hint_dir(hash_id: str) -> tuple[Path | None, str | None]:
    for run in HINT_RUNS:
        path = REPO / "scenarios/mops/runs" / run / "runtime" / hash_id / "mcp_run"
        if list(path.glob("iter3_hints_*.txt")):
            return path, run
    return None, None


def _read_jsonish(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    payload = fence.group(1) if fence else text
    data = json.loads(payload)
    return data if isinstance(data, dict) else {}


def _empty_char(names: list[str], ccdc: str, formula: str = "N/A") -> dict[str, Any]:
    block = json.loads(json.dumps(NA_CHAR))
    block["productNames"] = names
    block["productCCDCNumber"] = ccdc if _ok(ccdc) else "N/A"
    block["ElementalAnalysis"]["chemicalFormula"] = formula if _ok(formula) else "N/A"
    return block


def _project_ontospecies(path: Path | None, names: list[str], ccdc: str) -> dict[str, Any]:
    if path is None:
        return _empty_char(names, ccdc)
    try:
        data = _read_jsonish(path)
    except (OSError, json.JSONDecodeError):
        return _empty_char(names, ccdc)
    entities = {
        str(row.get("ref")): row
        for row in data.get("entities") or []
        if isinstance(row, dict) and row.get("ref")
    }
    formula = ""
    product: list[str] = []
    found_ccdc = ""
    for entity in entities.values():
        cls = str(entity.get("class") or "")
        props = entity.get("datatype_properties") or {}
        if cls == "Species":
            label = str(entity.get("label") or props.get("hasProductName") or "").strip()
            if label:
                product.append(label)
        elif cls == "ChemicalFormula":
            formula = str(props.get("hasChemicalFormulaValue") or entity.get("label") or "").strip()
        elif cls == "MolecularFormula" and not formula:
            formula = str(props.get("hasMolecularFormulaValue") or entity.get("label") or "").strip()
        elif cls == "CCDCNumber":
            found_ccdc = str(props.get("hasCCDCNumberValue") or entity.get("label") or "").strip()
    return _empty_char(product or names, found_ccdc or ccdc, formula or "N/A")


def _is_metal_cbu(formula: str, names: list[str]) -> bool:
    blob = f"{formula} {' '.join(names)}"
    return bool(re.search(r"\b(?:Zr|Cu|Ni|Co|Mo|Mg|Cr|Fe|V|Pd|Zn|Mn|Cd)\d*", blob))


def _project_ontomops(path: Path | None, ccdc: str) -> dict[str, Any]:
    proc: dict[str, Any] = {"mopCCDCNumber": ccdc if _ok(ccdc) else "N/A"}
    if path is None:
        return proc
    try:
        data = _read_jsonish(path)
    except (OSError, json.JSONDecodeError):
        return proc
    entities = {
        str(row.get("ref")): row
        for row in data.get("entities") or []
        if isinstance(row, dict) and row.get("ref")
    }
    names_by_cbu: dict[str, list[str]] = {}
    mop_ccdc = ""
    for rel in data.get("relations") or []:
        if not isinstance(rel, dict):
            continue
        subj = entities.get(str(rel.get("subject_ref")))
        obj = entities.get(str(rel.get("object_ref")))
        if not subj or not obj:
            continue
        prop = str(rel.get("property") or "")
        if prop == "sameAs" and str(subj.get("class") or "") == "ChemicalBuildingUnit":
            label = str(obj.get("label") or "").strip()
            if label:
                names_by_cbu.setdefault(str(subj.get("ref")), []).append(label)
        if str(subj.get("class") or "") == "MetalOrganicPolyhedron":
            props = subj.get("datatype_properties") or {}
            mop_ccdc = str(props.get("hasCCDCNumber") or "").strip() or mop_ccdc
    cbus: list[tuple[str, list[str]]] = []
    for entity in entities.values():
        if str(entity.get("class") or "") != "ChemicalBuildingUnit":
            continue
        props = entity.get("datatype_properties") or {}
        formula = str(props.get("hasCBUFormula") or entity.get("label") or "").strip()
        if not formula:
            continue
        cbus.append((formula, names_by_cbu.get(str(entity.get("ref")), [])))
    cbus.sort(key=lambda row: (1 if _is_metal_cbu(row[0], row[1]) else 0, row[0]))
    for slot, (formula, names) in enumerate(cbus[:2], 1):
        proc[f"cbuFormula{slot}"] = formula
        if names:
            proc[f"cbuSpeciesNames{slot}"] = names
    if _ok(mop_ccdc):
        proc["mopCCDCNumber"] = mop_ccdc
    return proc


def _chemicals_from_steps(synth: dict[str, Any], names: list[str], ccdc: str) -> dict[str, Any]:
    inputs: list[dict[str, Any]] = []
    for step in synth.get("steps") or []:
        if not isinstance(step, dict):
            continue
        add = step.get("Add")
        if not isinstance(add, dict):
            continue
        for chem in add.get("addedChemical") or []:
            chem_names = [str(x).strip() for x in (chem.get("chemicalName") or []) if str(x).strip()]
            if not chem_names:
                continue
            amount = str(chem.get("chemicalAmount") or "N/A")
            inputs.append(
                {
                    "chemical": [
                        {
                            "chemicalName": chem_names,
                            "chemicalAmount": amount if _ok(amount) else "N/A",
                            "chemicalFormula": "N/A",
                            "purity": "N/A",
                            "supplierName": "N/A",
                        }
                    ],
                    "purity": "N/A",
                    "supplierName": "N/A",
                }
            )
    return {
        "procedureName": names[0] if names else "",
        "steps": [
            {
                "inputChemicals": inputs,
                "outputChemical": [
                    {
                        "names": names,
                        "CCDCNumber": ccdc if _ok(ccdc) else "N/A",
                        "chemicalFormula": "N/A",
                    }
                ],
            }
        ],
    }


def project_hash(hash_id: str) -> dict[str, Any]:
    doi = HASH_TO_DOI[hash_id]
    steps_gt = _load(REPO / "full_ground_truth/steps" / f"{doi}.json")
    mcp, hint_run = hint_dir(hash_id)
    hint_files = list(mcp.glob("iter3_hints_*.txt")) if mcp else []
    os_files = list((R2 / "runtime" / hash_id / "mcp_run_ontospecies").glob("extraction_*.txt"))
    om_files = list((R2 / "runtime" / hash_id / "mcp_run_ontomops").glob("extraction_*.txt"))
    top_labels = load_top_labels(mcp)
    entities: list[dict[str, Any]] = []
    searches: list[list[str]] = []
    for gt_synth in steps_gt.get("Synthesis") or []:
        names = [str(x) for x in (gt_synth.get("productNames") or []) if str(x).strip()]
        ccdc = str(gt_synth.get("productCCDCNumber") or "")
        search = list(names)
        top = match_top_label(top_labels, names)
        if top:
            search.append(top)
        entities.append({"names": names or [f"entity_{len(entities)+1}"], "ccdc": ccdc, "search": search})
        searches.append(search)
    hints = assign_files(hint_files, searches)
    os_xs = assign_files(os_files, searches)
    om_xs = assign_files(om_files, searches)
    syntheses: list[dict[str, Any]] = []
    chars: list[dict[str, Any]] = []
    chems: list[dict[str, Any]] = []
    cbus: list[dict[str, Any]] = []
    inventory: list[dict[str, Any]] = []
    for entity, hint, os_x, om_x in zip(entities, hints, os_xs, om_xs):
        if hint:
            parsed, errors = parse_semantic_steps(hint.read_text(encoding="utf-8"), entity["names"][0])
            steps = parsed.get("steps") or []
        else:
            steps, errors = [], ["missing_iter3"]
        synth = {
            "productNames": entity["names"],
            "productCCDCNumber": entity["ccdc"] if _ok(entity["ccdc"]) else "N/A",
            "steps": steps,
        }
        syntheses.append(synth)
        chars.append(_project_ontospecies(os_x, entity["names"], entity["ccdc"]))
        chems.append(_chemicals_from_steps(synth, entity["names"], entity["ccdc"]))
        cbus.append(_project_ontomops(om_x, entity["ccdc"]))
        inventory.append(
            {
                "entity": entity["names"][0],
                "ccdc": entity["ccdc"],
                "hint": None if hint is None else hint.name,
                "ontospecies": None if os_x is None else os_x.name,
                "ontomops": None if om_x is None else om_x.name,
                "n_steps": len(steps),
                "errors": errors,
                "hint_run": hint_run,
            }
        )
    dest = OUT / hash_id
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "steps.json").write_text(json.dumps({"Synthesis": syntheses}, ensure_ascii=False, indent=2), encoding="utf-8")
    (dest / "characterisation.json").write_text(
        json.dumps({"Devices": [{"Characterisation": chars}]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (dest / "chemicals.json").write_text(
        json.dumps({"synthesisProcedures": chems}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (dest / "cbu.json").write_text(
        json.dumps({"synthesisProcedures": cbus}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (dest / "inventory.json").write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"hash": hash_id, "doi": doi, "entities": len(entities), "missing_hints": sum(1 for row in inventory if not row["hint"])}


def main() -> int:
    hashes = sorted(path.name for path in (OS_OM / "runtime").iterdir() if path.is_dir() and len(path.name) == 8)
    OUT.mkdir(parents=True, exist_ok=True)
    summary = [project_hash(hash_id) for hash_id in hashes]
    (OUT.parent / "projection_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    missing = sum(row["missing_hints"] for row in summary)
    print(f"projected={len(summary)} missing_hint_entities={missing} out={OUT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
