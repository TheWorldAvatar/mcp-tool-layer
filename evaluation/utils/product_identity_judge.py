"""gpt-4o product-identity judge for synthesis pairing.

Used when a CCDC is missing or is not a unique paper-level anchor.
Token similarity then collapses similar inorganic cores (P-2 vs P-3).
"""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from src.agents.scripts_and_prompts_generation.level1_code_repair import invoke_json

SCHEMA_VERSION = "product-identity-judge.v1"
POLICY_VERSION = "same-synthesis-product-v1"
BATCH_SCHEMA_VERSION = "product-identity-judge.batch.v1"

_POLICY = [
    "Decide only whether the two product-name lists denote the same synthesis product.",
    "equivalent=true if one list is a short label or alias of the product named in the other list, including a short code that appears inside a longer systematic name.",
    "equivalent=false if the product labels differ, including a different trailing index or suffix, or if they name different materials.",
    "A shared inorganic core is not enough when the product labels themselves differ.",
    "Ignore extra words such as 'synthesis of' or a temperature in a title; those do not change product identity.",
]


@dataclass(frozen=True)
class ProductIdentityConfig:
    enabled: bool = False
    model: str = "gpt-4o"
    cache_dir: Path = Path("evaluation/cache/product_identity_judge")
    required: bool = False
    timeout_seconds: int = 120
    batch_size: int = 20
    max_workers: int = 8


@dataclass(frozen=True)
class ProductIdentityJudgement:
    equivalent: bool
    confidence: float
    relation: str
    reason: str
    source: str
    status: str


def _clean_names(names: Iterable[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for name in names or []:
        text = str(name or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    return cleaned


def _normalized_overlap(gt_names: list[str], pred_names: list[str]) -> bool:
    from evaluation.scoring_steps import _normalize_product_name

    gt = {_normalize_product_name(name) for name in gt_names}
    pred = {_normalize_product_name(name) for name in pred_names}
    gt.discard("")
    pred.discard("")
    return bool(gt & pred)


def _cache_key(config: ProductIdentityConfig, gt_names: list[str], pred_names: list[str]) -> str:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "policy_version": POLICY_VERSION,
        "model": config.model,
        "ground_truth_names": gt_names,
        "prediction_names": pred_names,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _prompt(gt_names: list[str], pred_names: list[str]) -> str:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "task": "synthesis_product_identity",
        "ground_truth_names": gt_names,
        "prediction_names": pred_names,
        "policy": _POLICY,
    }
    return (
        "Return only one JSON object with exactly these keys: schema_version, "
        "equivalent, confidence, relation, reason. relation must be exactly one "
        "of equivalent, different, uncertain. Set equivalent=true only with "
        "confidence >= 0.9; otherwise fail closed with equivalent=false.\n\n"
        + json.dumps(payload, ensure_ascii=False, sort_keys=True)
    )


def _prompt_batch(pairs: list[dict[str, Any]]) -> str:
    payload = {
        "schema_version": BATCH_SCHEMA_VERSION,
        "task": "synthesis_product_identity_batch",
        "policy": _POLICY,
        "pairs": pairs,
    }
    return (
        "Return only one JSON object with exactly keys schema_version and judgements. "
        "Each judgement must have exactly pair_id, schema_version, equivalent, "
        "confidence, relation, reason. relation must be exactly one of equivalent, "
        "different, uncertain. Set equivalent=true only with confidence >= 0.9.\n\n"
        + json.dumps(payload, ensure_ascii=False, sort_keys=True)
    )


def _validate(data: dict[str, Any]) -> dict[str, Any]:
    required = {"schema_version", "equivalent", "confidence", "relation", "reason"}
    if set(data) != required or data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("product identity schema mismatch")
    if not isinstance(data.get("equivalent"), bool):
        raise ValueError("product identity equivalent must be boolean")
    confidence = data.get("confidence")
    if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        raise ValueError("product identity confidence out of range")
    if data.get("relation") not in {"equivalent", "different", "uncertain"}:
        raise ValueError("invalid product identity relation")
    if not str(data.get("reason") or "").strip():
        raise ValueError("product identity reason is required")
    if data["equivalent"] and (confidence < 0.9 or data["relation"] != "equivalent"):
        raise ValueError("positive product identity is not sufficiently certain")
    return data


def _validate_batch(data: dict[str, Any], pairs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    if set(data) != {"schema_version", "judgements"}:
        raise ValueError("product identity batch has unexpected keys")
    if data.get("schema_version") != BATCH_SCHEMA_VERSION:
        raise ValueError("product identity batch schema mismatch")
    rows = data.get("judgements")
    if not isinstance(rows, list):
        raise ValueError("product identity judgements must be a list")
    expected = {pair["pair_id"] for pair in pairs}
    validated: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("product identity row must be an object")
        pair_id = str(row.get("pair_id") or "")
        if pair_id not in expected or pair_id in validated:
            raise ValueError("product identity pair ids do not match request")
        body = {k: v for k, v in row.items() if k != "pair_id"}
        if "schema_version" not in body:
            body = {**body, "schema_version": SCHEMA_VERSION}
        validated[pair_id] = _validate(body)
    if set(validated) != expected:
        raise ValueError("product identity omitted requested pairs")
    return validated


class ProductIdentityJudge:
    def __init__(self, config: ProductIdentityConfig = ProductIdentityConfig()):
        self.config = config
        self._memory: dict[tuple[tuple[str, ...], tuple[str, ...]], ProductIdentityJudgement] = {}

    def equivalent(self, ground_truth_names: Iterable[str], prediction_names: Iterable[str]) -> bool:
        if not self.config.enabled:
            return False
        gt_names = _clean_names(ground_truth_names)
        pred_names = _clean_names(prediction_names)
        if not gt_names or not pred_names:
            return False
        if _normalized_overlap(gt_names, pred_names):
            return True
        memory_key = (tuple(gt_names), tuple(pred_names))
        cached = self._memory.get(memory_key)
        if cached is not None:
            return cached.equivalent and cached.status == "ok"
        cache_path = self.config.cache_dir / (_cache_key(self.config, gt_names, pred_names) + ".json")
        judgement = self._read_cache(cache_path)
        if judgement is None:
            judgement = self._invoke(cache_path, gt_names, pred_names)
        self._memory[memory_key] = judgement
        return judgement.equivalent and judgement.status == "ok"

    def prefetch(self, pairs: Iterable[tuple[Iterable[str], Iterable[str]]]) -> None:
        if not self.config.enabled:
            return
        pending: list[dict[str, Any]] = []
        pending_meta: dict[str, tuple[list[str], list[str], Path]] = {}
        seen: set[tuple[tuple[str, ...], tuple[str, ...]]] = set()
        for index, (gt_raw, pred_raw) in enumerate(pairs, start=1):
            gt_names = _clean_names(gt_raw)
            pred_names = _clean_names(pred_raw)
            if not gt_names or not pred_names:
                continue
            if _normalized_overlap(gt_names, pred_names):
                self._memory[(tuple(gt_names), tuple(pred_names))] = ProductIdentityJudgement(
                    equivalent=True,
                    confidence=1.0,
                    relation="equivalent",
                    reason="normalized product-name overlap",
                    source="deterministic",
                    status="ok",
                )
                continue
            key = (tuple(gt_names), tuple(pred_names))
            if key in seen or key in self._memory:
                continue
            seen.add(key)
            cache_path = self.config.cache_dir / (_cache_key(self.config, gt_names, pred_names) + ".json")
            cached = self._read_cache(cache_path)
            if cached is not None:
                self._memory[key] = cached
                continue
            pair_id = f"p{index:04d}"
            pending.append(
                {
                    "pair_id": pair_id,
                    "ground_truth_names": gt_names,
                    "prediction_names": pred_names,
                }
            )
            pending_meta[pair_id] = (gt_names, pred_names, cache_path)
        if not pending:
            return
        chunks = [
            pending[i : i + self.config.batch_size]
            for i in range(0, len(pending), self.config.batch_size)
        ]
        print(
            f"[product-identity] prefetch pending={len(pending)} "
            f"chunks={len(chunks)} model={self.config.model}",
            flush=True,
        )

        def _run_chunk(chunk: list[dict[str, Any]]) -> None:
            try:
                response = invoke_json(
                    self.config.model,
                    _prompt_batch(chunk),
                    timeout_seconds=self.config.timeout_seconds,
                    max_attempts=2,
                )
                validated = _validate_batch(response.data, chunk)
                for pair_id, row in validated.items():
                    gt_names, pred_names, cache_path = pending_meta[pair_id]
                    self._memory[(tuple(gt_names), tuple(pred_names))] = self._persist(
                        cache_path, gt_names, pred_names, row
                    )
            except Exception:
                for item in chunk:
                    gt_names, pred_names, cache_path = pending_meta[item["pair_id"]]
                    self._memory[(tuple(gt_names), tuple(pred_names))] = self._invoke(
                        cache_path, gt_names, pred_names
                    )

        workers = min(self.config.max_workers, len(chunks))
        if workers == 1:
            for chunk in chunks:
                _run_chunk(chunk)
        else:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = [executor.submit(_run_chunk, chunk) for chunk in chunks]
                for future in as_completed(futures):
                    future.result()

    def _read_cache(self, path: Path) -> ProductIdentityJudgement | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            row = _validate(payload["judgement"])
        except (OSError, KeyError, TypeError, json.JSONDecodeError, ValueError):
            return None
        return ProductIdentityJudgement(
            equivalent=bool(row["equivalent"]),
            confidence=float(row["confidence"]),
            relation=str(row["relation"]),
            reason=str(row["reason"]),
            source="cache",
            status="ok",
        )

    def _persist(
        self,
        cache_path: Path,
        gt_names: list[str],
        pred_names: list[str],
        row: dict[str, Any],
    ) -> ProductIdentityJudgement:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "policy_version": POLICY_VERSION,
            "model": self.config.model,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "ground_truth_names": gt_names,
            "prediction_names": pred_names,
            "judgement": row,
        }
        temp_path = cache_path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temp_path.replace(cache_path)
        return ProductIdentityJudgement(
            equivalent=bool(row["equivalent"]),
            confidence=float(row["confidence"]),
            relation=str(row["relation"]),
            reason=str(row["reason"]),
            source="llm",
            status="ok",
        )

    def _invoke(
        self,
        cache_path: Path,
        gt_names: list[str],
        pred_names: list[str],
    ) -> ProductIdentityJudgement:
        try:
            response = invoke_json(
                self.config.model,
                _prompt(gt_names, pred_names),
                timeout_seconds=self.config.timeout_seconds,
                max_attempts=2,
            )
            row = _validate(response.data)
            return self._persist(cache_path, gt_names, pred_names, row)
        except Exception as exc:
            if self.config.required:
                raise RuntimeError("product identity judge failed") from exc
            return ProductIdentityJudgement(
                equivalent=False,
                confidence=0.0,
                relation="uncertain",
                reason=f"judge failed closed: {type(exc).__name__}",
                source="error",
                status="error",
            )
