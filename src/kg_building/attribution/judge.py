"""LLM (gpt-5.6-sol) ledger presence judge for KG scorer mistakes.

Cost rule: one LLM call per paper. False positives are fingerprint-only;
only false negatives go to the model, packed into that single call.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from src.extraction_prompt_generation.llm.invoke import invoke_json
from src.kg_building.attribution.atoms import MistakeAtom
from src.kg_building.attribution.normalize import fingerprint, value_tokens

ATTRIBUTION_MODEL = "openai/gpt-5.6-sol"
LEDGER_CHAR_LIMIT = 40_000

STAGE_KG = "kg"
STAGE_EXTRACTION = "extraction"
STAGE_KG_INVENTION = "kg_invention"
STAGE_EXTRACTION_HALLUCINATION = "extraction_hallucination"
STAGE_DERIVATION = "derivation"

JsonInvoker = Callable[..., Any]


def heuristic_ledger_present(atom: MistakeAtom, ledger: str) -> bool:
    """Deterministic substring/fingerprint probe used as a hint and offline fallback."""
    if not ledger.strip():
        return False
    needle = atom.gt_value if atom.error_type == "fn" else atom.pred_value
    tokens = value_tokens(needle)
    if not tokens and atom.context.get("chemicals"):
        tokens = value_tokens(atom.context.get("chemicals"))
    if not tokens and atom.field == "step_type":
        tokens = value_tokens(atom.gt_value or atom.pred_value)
    ledger_lines = [fingerprint(line) for line in ledger.splitlines() if line.strip()]
    joined = "\n".join(ledger_lines)
    for token in tokens:
        if len(token) < 3:
            continue
        if token in joined:
            return True
        for line in ledger_lines:
            if token in line or line in token:
                return True
    return False


def stage_for(
    atom: MistakeAtom,
    *,
    ledger_present: bool,
    cbu_derivation: bool = False,
) -> str:
    if atom.module in {"cbu", "cbu_formula"} and atom.error_type == "fn" and not ledger_present and cbu_derivation:
        return STAGE_DERIVATION
    if atom.error_type == "fn":
        return STAGE_KG if ledger_present else STAGE_EXTRACTION
    if ledger_present:
        return STAGE_EXTRACTION_HALLUCINATION
    return STAGE_KG_INVENTION


def _clip_ledger(ledger: str) -> str:
    text = ledger.strip()
    if len(text) <= LEDGER_CHAR_LIMIT:
        return text
    keep = LEDGER_CHAR_LIMIT // 2
    return (
        text[:keep]
        + "\n\n[... ledger truncated ...]\n\n"
        + text[-keep:]
    )


def _compact_atom(atom: MistakeAtom, ledger: str) -> dict[str, Any]:
    return {
        "id": atom.atom_id,
        "mod": atom.module,
        "field": atom.field,
        "gt": atom.judge_payload()["gt_value"],
        "hit": heuristic_ledger_present(atom, ledger),
    }


def _prompt(ledger: str, atoms: list[MistakeAtom]) -> str:
    payload = [_compact_atom(atom, ledger) for atom in atoms]
    return (
        "The KG builder can only use the extraction ledger, not the paper.\n"
        "For each FN atom, is gt already stated in the ledger "
        "(synonyms, formulas, and split amounts like `17.5 mg, 0.06 mmol` count)?\n"
        "Do not credit implied chemistry. `hit` is a weak substring hint.\n"
        "Return JSON: {\"judgements\":[{\"id\":\"...\",\"in_ledger\":true}]}\n"
        "Every input id exactly once. No other keys.\n\n"
        "LEDGER:\n"
        f"{_clip_ledger(ledger)}\n\n"
        "FN:\n"
        f"{json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n"
    )


def _parse_judgements(data: dict[str, Any], atoms: list[MistakeAtom]) -> dict[str, dict[str, Any]]:
    rows = data.get("judgements") if isinstance(data, dict) else None
    by_id: dict[str, dict[str, Any]] = {}
    if not isinstance(rows, list):
        return by_id
    known = {atom.atom_id for atom in atoms}
    for row in rows:
        if not isinstance(row, dict):
            continue
        atom_id = str(row.get("id") or row.get("atom_id") or "").strip()
        if atom_id not in known:
            continue
        present = row.get("in_ledger")
        if not isinstance(present, bool):
            present = row.get("ledger_present")
        if not isinstance(present, bool):
            continue
        by_id[atom_id] = {
            "ledger_present": present,
            "evidence": str(row.get("evidence") or "").strip(),
            "reason": str(row.get("reason") or "").strip(),
            "source": "llm",
        }
    return by_id


def _heuristic_row(atom: MistakeAtom, ledger: str, reason: str, source: str) -> dict[str, Any]:
    present = heuristic_ledger_present(atom, ledger)
    return {
        "ledger_present": present,
        "evidence": "",
        "reason": reason,
        "source": source,
    }


def judge_atoms(
    atoms: list[MistakeAtom],
    ledger: str,
    *,
    model: str = ATTRIBUTION_MODEL,
    heuristic_only: bool = False,
    cbu_derivation: bool = False,
    invoke: JsonInvoker | None = None,
) -> list[dict[str, Any]]:
    """Return per-atom stage tags. ``invoke`` is injected in tests.

    One LLM round-trip for the whole atom list. FPs stay heuristic because
    they do not move extraction-counterfactual F1.
    """
    if not atoms:
        return []
    judgements: dict[str, dict[str, Any]] = {}
    fns = [atom for atom in atoms if atom.error_type == "fn"]
    fps = [atom for atom in atoms if atom.error_type != "fn"]
    for atom in fps:
        judgements[atom.atom_id] = _heuristic_row(
            atom, ledger, "fp fingerprint", "heuristic_fp"
        )
    if heuristic_only:
        for atom in fns:
            judgements[atom.atom_id] = _heuristic_row(
                atom, ledger, "heuristic-only", "heuristic"
            )
    elif fns:
        caller = invoke or invoke_json
        try:
            result = caller(model, _prompt(ledger, fns))
            data = result.data if hasattr(result, "data") else result
            if not isinstance(data, dict):
                raise ValueError("judge response is not an object")
            judgements.update(_parse_judgements(data, fns))
        except Exception as exc:
            for atom in fns:
                if atom.atom_id in judgements:
                    continue
                judgements[atom.atom_id] = _heuristic_row(
                    atom,
                    ledger,
                    f"LLM judge failed ({type(exc).__name__}); used heuristic",
                    "heuristic_fallback",
                )
        print(f"  [judge] llm_calls=1 fn={len(fns)} fp_heuristic={len(fps)}", flush=True)
    else:
        print(f"  [judge] llm_calls=0 fn=0 fp_heuristic={len(fps)}", flush=True)
    rows: list[dict[str, Any]] = []
    for atom in atoms:
        judged = judgements.get(atom.atom_id)
        if judged is None:
            judged = _heuristic_row(
                atom,
                ledger,
                "heuristic-only" if heuristic_only else "missing LLM judgement",
                "heuristic",
            )
        stage = stage_for(
            atom,
            ledger_present=bool(judged["ledger_present"]),
            cbu_derivation=cbu_derivation,
        )
        rows.append(
            {
                **atom.to_dict(),
                "ledger_present": judged["ledger_present"],
                "stage": stage,
                "evidence": judged.get("evidence") or "",
                "reason": judged.get("reason") or "",
                "judge_source": judged.get("source") or "heuristic",
            }
        )
    return rows
