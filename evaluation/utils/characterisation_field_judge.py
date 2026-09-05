"""Cached LLM rescue for characterisation fields that fail deterministic normalize."""

from __future__ import annotations

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from src.agents.scripts_and_prompts_generation.level1_code_repair import invoke_json


SCHEMA_VERSION = "characterisation-field-judge.v1"
PROMPT_POLICY_VERSION = "same-measurement-style-v1"
RELATIONS = {
    "abbreviated_peak_list",
    "notation_variant",
    "deuterated_solvent",
    "punctuation_variant",
    "unrelated",
    "uncertain",
}
FIELD_KINDS = {
    "HNMR.shifts",
    "HNMR.solvent",
    "HNMR.temperature",
    "ElementalAnalysis.weightPercentageCalculated",
    "ElementalAnalysis.weightPercentageExperimental",
    "ElementalAnalysis.chemicalFormula",
    "InfraredSpectroscopy.material",
    "InfraredSpectroscopy.bands",
}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class FieldJudgeConfig:
    enabled: bool = False
    model: str = "gpt-4o"
    cache_dir: Path = Path("evaluation/cache/characterisation_field_judge")
    required: bool = False
    timeout_seconds: int = 300
    batch_size: int = 20
    max_workers: int = 8


@dataclass(frozen=True)
class FieldJudgement:
    field_kind: str
    ground_truth_value: str
    prediction_value: str
    ground_truth_fingerprint: str
    prediction_fingerprint: str
    equivalent: bool
    confidence: float
    relation: str
    reason: str
    gt_abbreviated: bool
    source: str
    status: str


def _cache_key(
    *,
    model: str,
    field_kind: str,
    ground_truth_fingerprint: str,
    prediction_fingerprint: str,
) -> str:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "policy_version": PROMPT_POLICY_VERSION,
        "model": model,
        "field_kind": field_kind,
        "ground_truth_fingerprint": ground_truth_fingerprint,
        "prediction_fingerprint": prediction_fingerprint,
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _prompt(pairs: list[dict[str, str]]) -> str:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "task": "characterisation_field_equivalence",
        "policy": [
            "Judge whether both strings describe the same experimental measurement.",
            "Accept writing-style differences only: abbreviated vs assigned NMR peak lists with the same ppm set; δ/delta/= /ppm wrappers; DMSO vs DMSO-d6 as the same NMR solvent name; punctuation, case, and figure-caption wrappers around the same IR wavenumbers.",
            "HNMR.shifts: equivalent only when the chemical-shift numbers match. Extra multiplicity, integration, or assignment text is allowed. Extra or missing ppm values are not equivalent.",
            "HNMR.solvent: DMSO, DMSO-d6, (CD3)2SO, and dimethyl sulfoxide are equivalent. CDCl3 vs DMSO is not.",
            "Do not invent missing elemental-analysis or IR values. If the two strings are about different quantities, return unrelated.",
            "If the GT string is a shortened transcription of the same measurement the prediction wrote more completely (or the reverse), still mark equivalent=true and set gt_abbreviated=true when GT dropped assignment text or deuteration that the other side kept.",
            "If identity cannot be established, return equivalent=false and relation=uncertain.",
        ],
        "pairs": pairs,
    }
    return (
        "Return only one JSON object with exactly keys schema_version and judgements. "
        "Each judgement must have exactly pair_id, equivalent, confidence, relation, "
        "reason, gt_abbreviated, ground_truth_interpretation, prediction_interpretation. "
        f"relation must be one of {sorted(RELATIONS)}. confidence must be 0..1. "
        "gt_abbreviated must be boolean. "
        "Set equivalent=true only when confidence is at least 0.9; otherwise set "
        "equivalent=false and relation=uncertain. "
        "Do not use external context that is not inherent in the two field strings.\n\n"
        + json.dumps(payload, ensure_ascii=False, sort_keys=True)
    )


def _validate_payload(
    data: dict[str, Any],
    pairs: list[dict[str, str]],
) -> dict[str, dict[str, Any]]:
    if set(data) != {"schema_version", "judgements"}:
        raise ValueError("field judgement has unexpected top-level keys")
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("field judgement schema version mismatch")
    rows = data.get("judgements")
    if not isinstance(rows, list):
        raise ValueError("field judgements must be a list")
    expected_ids = {pair["pair_id"] for pair in pairs}
    validated: dict[str, dict[str, Any]] = {}
    required_keys = {
        "pair_id",
        "equivalent",
        "confidence",
        "relation",
        "reason",
        "gt_abbreviated",
        "ground_truth_interpretation",
        "prediction_interpretation",
    }
    for row in rows:
        if not isinstance(row, dict) or set(row) != required_keys:
            raise ValueError("field judgement row has invalid keys")
        pair_id = str(row.get("pair_id") or "")
        if pair_id not in expected_ids or pair_id in validated:
            raise ValueError("field judgement pair ids do not match request")
        if not isinstance(row.get("equivalent"), bool):
            raise ValueError("equivalent must be boolean")
        if not isinstance(row.get("gt_abbreviated"), bool):
            raise ValueError("gt_abbreviated must be boolean")
        confidence = row.get("confidence")
        if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            raise ValueError("confidence must be between zero and one")
        relation = str(row.get("relation") or "")
        if relation not in RELATIONS:
            raise ValueError("invalid field relation")
        if not str(row.get("reason") or "").strip():
            raise ValueError("field judgement reason is required")
        if row["equivalent"]:
            if relation in {"unrelated", "uncertain"} or confidence < 0.9:
                raise ValueError("positive field judgement is not sufficiently certain")
            if not str(row.get("ground_truth_interpretation") or "").strip():
                raise ValueError("positive judgement requires GT interpretation")
            if not str(row.get("prediction_interpretation") or "").strip():
                raise ValueError("positive judgement requires prediction interpretation")
        validated[pair_id] = row
    if set(validated) != expected_ids:
        raise ValueError("field judgement omitted requested pairs")
    return validated


def _read_cache(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    judgement = data.get("judgement")
    return judgement if isinstance(judgement, dict) else None


def _write_cache(
    path: Path,
    *,
    model: str,
    pair: dict[str, str],
    judgement: dict[str, Any],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "policy_version": PROMPT_POLICY_VERSION,
        "model": model,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "pair": pair,
        "judgement": judgement,
    }
    temp_path = path.with_suffix(".tmp")
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temp_path.replace(path)


def _failed_closed(
    pair: tuple[str, str, str, str, str],
    *,
    exc: Exception,
) -> FieldJudgement:
    field_kind, gt_value, pred_value, gt_fp, pred_fp = pair
    return FieldJudgement(
        field_kind=field_kind,
        ground_truth_value=gt_value,
        prediction_value=pred_value,
        ground_truth_fingerprint=gt_fp,
        prediction_fingerprint=pred_fp,
        equivalent=False,
        confidence=0.0,
        relation="uncertain",
        reason=f"judge failed closed: {type(exc).__name__}",
        gt_abbreviated=False,
        source="error",
        status="error",
    )


def _to_result(
    pair: tuple[str, str, str, str, str],
    row: dict[str, Any],
    *,
    source: str,
    status: str,
) -> FieldJudgement:
    field_kind, gt_value, pred_value, gt_fp, pred_fp = pair
    return FieldJudgement(
        field_kind=field_kind,
        ground_truth_value=gt_value,
        prediction_value=pred_value,
        ground_truth_fingerprint=gt_fp,
        prediction_fingerprint=pred_fp,
        equivalent=bool(row["equivalent"]),
        confidence=float(row["confidence"]),
        relation=str(row["relation"]),
        reason=str(row["reason"]),
        gt_abbreviated=bool(row["gt_abbreviated"]),
        source=source,
        status=status,
    )


def _invoke_chunk(
    config: FieldJudgeConfig,
    chunk: list[dict[str, str]],
    pending_meta: dict[str, tuple[str, str, str, str, str, Path]],
) -> dict[tuple[str, str, str], FieldJudgement]:
    results: dict[tuple[str, str, str], FieldJudgement] = {}

    def _apply(validated: dict[str, dict[str, Any]], items: list[dict[str, str]]) -> None:
        for pair_id, row in validated.items():
            field_kind, gt_value, pred_value, gt_fp, pred_fp, cache_path = pending_meta[pair_id]
            pair = (field_kind, gt_value, pred_value, gt_fp, pred_fp)
            _write_cache(
                cache_path,
                model=config.model,
                pair=next(item for item in items if item["pair_id"] == pair_id),
                judgement=row,
            )
            results[(field_kind, gt_fp, pred_fp)] = _to_result(
                pair, row, source="llm", status="ok"
            )

    try:
        response = invoke_json(
            config.model,
            _prompt(chunk),
            timeout_seconds=config.timeout_seconds,
            max_attempts=2,
        )
        validated = _validate_payload(response.data, chunk)
        _apply(validated, chunk)
        return results
    except Exception as outer_exc:
        if len(chunk) == 1:
            if config.required:
                raise RuntimeError("characterisation field judge failed") from outer_exc
            pair_id = chunk[0]["pair_id"]
            meta = pending_meta[pair_id]
            results[(meta[0], meta[3], meta[4])] = _failed_closed(meta[:5], exc=outer_exc)
            return results

        for item in chunk:
            try:
                response = invoke_json(
                    config.model,
                    _prompt([item]),
                    timeout_seconds=config.timeout_seconds,
                    max_attempts=2,
                )
                validated = _validate_payload(response.data, [item])
                _apply(validated, [item])
            except Exception as inner_exc:
                if config.required:
                    raise RuntimeError("characterisation field judge failed") from inner_exc
                meta = pending_meta[item["pair_id"]]
                results[(meta[0], meta[3], meta[4])] = _failed_closed(
                    meta[:5], exc=inner_exc
                )
        return results


def judge_field_pairs(
    pairs: Iterable[tuple[str, str, str, str, str]],
    config: FieldJudgeConfig,
) -> list[FieldJudgement]:
    """Judge unresolved (field_kind, GT raw, pred raw, GT fp, pred fp) pairs."""
    unique: dict[tuple[str, str, str], tuple[str, str, str, str, str]] = {}
    for pair in pairs:
        unique.setdefault((pair[0], pair[3], pair[4]), pair)

    results: dict[tuple[str, str, str], FieldJudgement] = {}
    pending: list[dict[str, str]] = []
    pending_meta: dict[str, tuple[str, str, str, str, str, Path]] = {}
    for index, pair in enumerate(unique.values(), start=1):
        field_kind, gt_value, pred_value, gt_fp, pred_fp = pair
        if field_kind not in FIELD_KINDS:
            results[(field_kind, gt_fp, pred_fp)] = FieldJudgement(
                field_kind=field_kind,
                ground_truth_value=gt_value,
                prediction_value=pred_value,
                ground_truth_fingerprint=gt_fp,
                prediction_fingerprint=pred_fp,
                equivalent=False,
                confidence=0.0,
                relation="uncertain",
                reason="unsupported field kind",
                gt_abbreviated=False,
                source="error",
                status="error",
            )
            continue
        key = _cache_key(
            model=config.model,
            field_kind=field_kind,
            ground_truth_fingerprint=gt_fp,
            prediction_fingerprint=pred_fp,
        )
        cache_path = config.cache_dir / f"{key}.json"
        cached = _read_cache(cache_path)
        if cached is not None:
            try:
                validated = _validate_payload(
                    {
                        "schema_version": SCHEMA_VERSION,
                        "judgements": [cached],
                    },
                    [{"pair_id": str(cached.get("pair_id") or "")}],
                )
                row = next(iter(validated.values()))
                results[(field_kind, gt_fp, pred_fp)] = _to_result(
                    pair, row, source="cache", status="ok"
                )
                continue
            except ValueError:
                pass
        pair_id = f"p{index:04d}"
        request = {
            "pair_id": pair_id,
            "field_kind": field_kind,
            "ground_truth_value": gt_value,
            "prediction_value": pred_value,
            "ground_truth_fingerprint": gt_fp,
            "prediction_fingerprint": pred_fp,
        }
        pending.append(request)
        pending_meta[pair_id] = (*pair, cache_path)

    if not pending:
        return [results[key] for key in sorted(results)]

    batch_size = max(1, int(config.batch_size or _env_int("CHAR_FIELD_BATCH_SIZE", 20)))
    max_workers = max(1, int(config.max_workers or _env_int("CHAR_FIELD_MAX_WORKERS", 8)))
    chunks = [pending[i : i + batch_size] for i in range(0, len(pending), batch_size)]
    print(
        f"[characterisation-field-judge] pending={len(pending)} "
        f"chunks={len(chunks)} batch_size={batch_size} workers={max_workers}",
        flush=True,
    )

    workers = min(max_workers, len(chunks))
    if workers == 1:
        for chunk in chunks:
            results.update(_invoke_chunk(config, chunk, pending_meta))
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [
                executor.submit(_invoke_chunk, config, chunk, pending_meta)
                for chunk in chunks
            ]
            for future in as_completed(futures):
                results.update(future.result())

    return [results[key] for key in sorted(results)]
