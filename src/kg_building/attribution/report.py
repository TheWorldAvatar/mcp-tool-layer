"""Counterfactual extraction F1 from ledger-supported KG misses."""

from __future__ import annotations

from collections import Counter
from typing import Any

from src.kg_building.attribution.atoms import ModuleScore
from src.kg_building.attribution.judge import (
    STAGE_DERIVATION,
    STAGE_EXTRACTION,
    STAGE_EXTRACTION_HALLUCINATION,
    STAGE_KG,
    STAGE_KG_INVENTION,
)


def precision_recall_f1(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * prec * rec / (prec + rec)) if (prec + rec) else 0.0
    return prec, rec, f1


def _fn_counts(judged: list[dict[str, Any]]) -> tuple[int, int]:
    kg_fn = 0
    extraction_fn = 0
    for row in judged:
        if row.get("error_type") != "fn":
            continue
        count = max(1, int(row.get("count") or 1))
        if row.get("stage") == STAGE_KG:
            kg_fn += count
        elif row.get("stage") in {STAGE_EXTRACTION, STAGE_DERIVATION}:
            extraction_fn += count
    return kg_fn, extraction_fn


def summarize_module(
    score: ModuleScore,
    judged: list[dict[str, Any]],
) -> dict[str, Any]:
    kg_fn, extraction_fn = _fn_counts(judged)
    kg_fn = min(kg_fn, score.fn)
    extraction_tp = score.tp + kg_fn
    extraction_fn_kept = max(0, score.fn - kg_fn)
    kg_prec, kg_rec, kg_f1 = precision_recall_f1(score.tp, score.fp, score.fn)
    ext_prec, ext_rec, ext_f1 = precision_recall_f1(
        extraction_tp, score.fp, extraction_fn_kept
    )
    stages = Counter(str(row.get("stage") or "") for row in judged)
    return {
        "module": score.module,
        "official": {
            "tp": score.tp,
            "fp": score.fp,
            "fn": score.fn,
            "precision": round(kg_prec, 3),
            "recall": round(kg_rec, 3),
            "f1": round(kg_f1, 3),
            "from_official_scorer": score.official,
        },
        "attribution": {
            "kg_fn": kg_fn,
            "extraction_fn": extraction_fn,
            "kg_invention_fp": sum(
                1 for row in judged if row.get("stage") == STAGE_KG_INVENTION
            ),
            "extraction_hallucination_fp": sum(
                1 for row in judged if row.get("stage") == STAGE_EXTRACTION_HALLUCINATION
            ),
            "derivation_fn": sum(
                int(row.get("count") or 1)
                for row in judged
                if row.get("stage") == STAGE_DERIVATION
            ),
            "stages": dict(stages),
        },
        "extraction_counterfactual": {
            "tp": extraction_tp,
            "fp": score.fp,
            "fn": extraction_fn_kept,
            "precision": round(ext_prec, 3),
            "recall": round(ext_rec, 3),
            "f1": round(ext_f1, 3),
        },
        "monotonicity_ok": ext_f1 + 1e-12 >= kg_f1,
    }


def render_module_markdown(summary: dict[str, Any]) -> str:
    official = summary["official"]
    extraction = summary["extraction_counterfactual"]
    attr = summary["attribution"]
    flag = "yes" if summary["monotonicity_ok"] else "NO"
    return (
        f"## {summary['module']}\n\n"
        f"- KG (official): TP={official['tp']} FP={official['fp']} FN={official['fn']} "
        f"F1={official['f1']:.3f}\n"
        f"- Extraction counterfactual: TP={extraction['tp']} FP={extraction['fp']} "
        f"FN={extraction['fn']} F1={extraction['f1']:.3f}\n"
        f"- FN split: extraction={attr['extraction_fn']} kg={attr['kg_fn']} "
        f"derivation={attr['derivation_fn']}\n"
        f"- Extraction F1 >= KG F1: {flag}\n"
    )
