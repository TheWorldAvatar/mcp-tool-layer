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


SCHEMA_VERSION = "chemical-synonym-judge.v1"
PROMPT_POLICY_VERSION = "same-species-strict-v2"
RELATIONS = {
    "abbreviation",
    "systematic_name",
    "formula_name",
    "hydrate_variant",
    "solvent_alias",
    "punctuation_variant",
    "unrelated",
    "uncertain",
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
class SynonymJudgeConfig:
    enabled: bool = False
    model: str = "gpt-4o"
    cache_dir: Path = Path("evaluation/cache/chemical_synonym_judge")
    required: bool = False
    timeout_seconds: int = 300
    # Chunk size for one LLM JSON call; keep modest so schema stays reliable.
    batch_size: int = 20
    # Parallel chunk workers (I/O bound LLM calls).
    max_workers: int = 8


@dataclass(frozen=True)
class SynonymJudgement:
    ground_truth_name: str
    prediction_name: str
    ground_truth_fingerprint: str
    prediction_fingerprint: str
    equivalent: bool
    confidence: float
    relation: str
    reason: str
    source: str
    status: str


def _cache_key(
    *,
    model: str,
    ground_truth_fingerprint: str,
    prediction_fingerprint: str,
) -> str:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "policy_version": PROMPT_POLICY_VERSION,
        "model": model,
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
        "task": "chemical_name_equivalence",
        "policy": [
            "Judge whether each pair denotes exactly the same chemical species.",
            "Accept abbreviations, systematic/common names, equivalent formula names, and punctuation variants only when identity is exact.",
            "Treat MeOH/CH3OH/methyl alcohol as methanol, H2O as water, and DMF/DMA abbreviations as the corresponding amide when unambiguous.",
            "For dimethylformamide and dimethylacetamide, treat N,N- vs N,N'- typography as equivalent (common OCR/GT variant); do not invent a different diamide.",
            "Normalize quote/prime variants in polycarboxylate locants ('' vs \") as the same name when the backbone matches.",
            "Different hydrate states, stoichiometries, charges, counterions, stereoisomers, coordination states, or compositions are not equivalent.",
            "Similar roles, shared fragments, or related precursor/product identities are not equivalence.",
            "If identity cannot be established, return equivalent=false and relation=uncertain.",
        ],
        "pairs": pairs,
    }
    return (
        "Return only one JSON object with exactly keys schema_version and judgements. "
        "Each judgement must have exactly pair_id, equivalent, confidence, relation, "
        "reason, ground_truth_interpretation, prediction_interpretation. "
        f"relation must be one of {sorted(RELATIONS)}. confidence must be 0..1. "
        "Set equivalent=true only when confidence is at least 0.9; otherwise set "
        "equivalent=false and relation=uncertain. "
        "Do not use external context that is not inherent in the chemical names.\n\n"
        + json.dumps(payload, ensure_ascii=False, sort_keys=True)
    )


def _validate_payload(
    data: dict[str, Any],
    pairs: list[dict[str, str]],
) -> dict[str, dict[str, Any]]:
    if set(data) != {"schema_version", "judgements"}:
        raise ValueError("synonym judgement has unexpected top-level keys")
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("synonym judgement schema version mismatch")
    rows = data.get("judgements")
    if not isinstance(rows, list):
        raise ValueError("synonym judgements must be a list")
    expected_ids = {pair["pair_id"] for pair in pairs}
    validated: dict[str, dict[str, Any]] = {}
    required_keys = {
        "pair_id",
        "equivalent",
        "confidence",
        "relation",
        "reason",
        "ground_truth_interpretation",
        "prediction_interpretation",
    }
    for row in rows:
        if not isinstance(row, dict) or set(row) != required_keys:
            raise ValueError("synonym judgement row has invalid keys")
        pair_id = str(row.get("pair_id") or "")
        if pair_id not in expected_ids or pair_id in validated:
            raise ValueError("synonym judgement pair ids do not match request")
        if not isinstance(row.get("equivalent"), bool):
            raise ValueError("equivalent must be boolean")
        confidence = row.get("confidence")
        if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            raise ValueError("confidence must be between zero and one")
        relation = str(row.get("relation") or "")
        if relation not in RELATIONS:
            raise ValueError("invalid synonym relation")
        if not str(row.get("reason") or "").strip():
            raise ValueError("synonym judgement reason is required")
        if row["equivalent"]:
            if relation in {"unrelated", "uncertain"} or confidence < 0.9:
                raise ValueError("positive synonym judgement is not sufficiently certain")
            if not str(row.get("ground_truth_interpretation") or "").strip():
                raise ValueError("positive judgement requires GT interpretation")
            if not str(row.get("prediction_interpretation") or "").strip():
                raise ValueError("positive judgement requires prediction interpretation")
        validated[pair_id] = row
    if set(validated) != expected_ids:
        raise ValueError("synonym judgement omitted requested pairs")
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
    pair: tuple[str, str, str, str],
    *,
    exc: Exception,
) -> SynonymJudgement:
    gt_name, pred_name, gt_fp, pred_fp = pair
    return SynonymJudgement(
        ground_truth_name=gt_name,
        prediction_name=pred_name,
        ground_truth_fingerprint=gt_fp,
        prediction_fingerprint=pred_fp,
        equivalent=False,
        confidence=0.0,
        relation="uncertain",
        reason=f"judge failed closed: {type(exc).__name__}",
        source="error",
        status="error",
    )


def _invoke_chunk(
    config: SynonymJudgeConfig,
    chunk: list[dict[str, str]],
    pending_meta: dict[str, tuple[str, str, str, str, Path]],
) -> dict[tuple[str, str], SynonymJudgement]:
    """Judge one chunk of pairs; on chunk failure, split once then fail-closed."""
    results: dict[tuple[str, str], SynonymJudgement] = {}

    def _apply(validated: dict[str, dict[str, Any]], items: list[dict[str, str]]) -> None:
        for pair_id, row in validated.items():
            gt_name, pred_name, gt_fp, pred_fp, cache_path = pending_meta[pair_id]
            pair = (gt_name, pred_name, gt_fp, pred_fp)
            _write_cache(
                cache_path,
                model=config.model,
                pair=next(item for item in items if item["pair_id"] == pair_id),
                judgement=row,
            )
            results[(gt_fp, pred_fp)] = _to_result(pair, row, source="llm", status="ok")

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
        # Retry as singletons so one bad row does not sink the whole chunk.
        if len(chunk) == 1:
            if config.required:
                raise RuntimeError("chemical synonym judge failed") from outer_exc
            pair_id = chunk[0]["pair_id"]
            meta = pending_meta[pair_id]
            results[(meta[2], meta[3])] = _failed_closed(meta[:4], exc=outer_exc)
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
                    raise RuntimeError("chemical synonym judge failed") from inner_exc
                meta = pending_meta[item["pair_id"]]
                results[(meta[2], meta[3])] = _failed_closed(meta[:4], exc=inner_exc)
        return results


def judge_pairs(
    pairs: Iterable[tuple[str, str, str, str]],
    config: SynonymJudgeConfig,
) -> list[SynonymJudgement]:
    """Judge unresolved (GT raw, prediction raw, GT fp, prediction fp) pairs.

    Pending pairs are chunked and judged in parallel LLM calls so large
    Cartesian products do not serialize one-pair-at-a-time.
    """
    unique: dict[tuple[str, str], tuple[str, str, str, str]] = {}
    for pair in pairs:
        unique.setdefault((pair[2], pair[3]), pair)

    results: dict[tuple[str, str], SynonymJudgement] = {}
    pending: list[dict[str, str]] = []
    pending_meta: dict[str, tuple[str, str, str, str, Path]] = {}
    for index, pair in enumerate(unique.values(), start=1):
        gt_name, pred_name, gt_fp, pred_fp = pair
        key = _cache_key(
            model=config.model,
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
                results[(gt_fp, pred_fp)] = _to_result(
                    pair, row, source="cache", status="ok"
                )
                continue
            except ValueError:
                pass
        pair_id = f"p{index:04d}"
        request = {
            "pair_id": pair_id,
            "ground_truth_name": gt_name,
            "prediction_name": pred_name,
            "ground_truth_fingerprint": gt_fp,
            "prediction_fingerprint": pred_fp,
        }
        pending.append(request)
        pending_meta[pair_id] = (*pair, cache_path)

    if not pending:
        return [results[key] for key in sorted(results)]

    batch_size = max(1, int(config.batch_size or _env_int("CHEMICAL_SYNONYM_BATCH_SIZE", 20)))
    max_workers = max(1, int(config.max_workers or _env_int("CHEMICAL_SYNONYM_MAX_WORKERS", 8)))
    chunks = [pending[i : i + batch_size] for i in range(0, len(pending), batch_size)]
    print(
        f"[chemical-synonym-judge] pending={len(pending)} "
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


def _to_result(
    pair: tuple[str, str, str, str],
    row: dict[str, Any],
    *,
    source: str,
    status: str,
) -> SynonymJudgement:
    gt_name, pred_name, gt_fp, pred_fp = pair
    return SynonymJudgement(
        ground_truth_name=gt_name,
        prediction_name=pred_name,
        ground_truth_fingerprint=gt_fp,
        prediction_fingerprint=pred_fp,
        equivalent=bool(row["equivalent"]),
        confidence=float(row["confidence"]),
        relation=str(row["relation"]),
        reason=str(row["reason"]),
        source=source,
        status=status,
    )
