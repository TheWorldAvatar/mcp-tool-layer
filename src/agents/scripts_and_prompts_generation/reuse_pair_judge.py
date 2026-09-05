"""Generic LLM judgement for cross-context RDF entity reuse."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from src.agents.scripts_and_prompts_generation.level1_code_repair import invoke_json


SCHEMA_VERSION = "entity-reuse-pair-judge.v1"
POLICY_VERSION = "generic-real-world-identity.v1"


@dataclass(frozen=True)
class ReuseJudgeConfig:
    model: str = "gpt-5"
    cache_dir: Path = Path("evaluation/cache/entity_reuse_judge")
    audit_dir: Path | None = None
    confidence_threshold: float = 0.95
    timeout_seconds: int = 300
    max_semantic_attempts: int = 3
    required: bool = True

    @classmethod
    def from_environment(cls) -> "ReuseJudgeConfig":
        cache = Path(
            os.environ.get("TWA_REUSE_JUDGE_CACHE_DIR")
            or "evaluation/cache/entity_reuse_judge"
        )
        audit_raw = str(os.environ.get("TWA_REUSE_JUDGE_AUDIT_DIR") or "").strip()
        audit = (
            Path(audit_raw)
            if audit_raw
            else Path(os.environ.get("TWA_AGENTIC_DATA_DIR") or "data")
            / "central_memory"
            / "reuse_judge_audit"
        )
        return cls(
            model=str(os.environ.get("TWA_REUSE_JUDGE_MODEL") or "gpt-5").strip(),
            cache_dir=cache,
            audit_dir=audit,
            confidence_threshold=float(
                os.environ.get("TWA_REUSE_JUDGE_CONFIDENCE") or "0.95"
            ),
            required=str(
                os.environ.get("TWA_REUSE_JUDGE_REQUIRED") or "1"
            ).strip().casefold()
            not in {"0", "false", "no"},
        )


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _request_key(request: dict[str, Any], config: ReuseJudgeConfig) -> str:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "policy_version": POLICY_VERSION,
        "model": config.model,
        "confidence_threshold": config.confidence_threshold,
        "request": request,
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _prompt(requests: list[dict[str, Any]], validation_error: str = "") -> str:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "task": "real_world_entity_identity_and_cross_context_reuse",
        "generic_policy": [
            "Judge each pair independently from its ontology semantics, facts, source evidence, reuse policy, and provenance.",
            "Authorize reuse only when the proposed entity and candidate are the same real-world entity, not merely similar entities or entities with the same role or label.",
            "A label match alone is never identity evidence.",
            "Decide whether the entity identity is context-independent. An occurrence, assertion, document-bound record, procedure-bound record, measurement, role, or contextual value is not globally reusable merely because its text repeats.",
            "Require the declared match basis to be satisfied by explicit compatible facts. Missing identity evidence means deny.",
            "Differences in stable identifiers, defining values, composition, scope, provenance meaning, or contextual ownership mean deny.",
            "When evidence is incomplete, ambiguous, or contradictory, deny reuse.",
            "Do not use class-name keyword rules. Interpret the supplied T-Box comments and property contracts generically.",
        ],
        "output_policy": [
            "Return exactly one judgement for every pair_id.",
            "reuse_authorized=true requires same_real_world_entity=true, context_independent_identity=true, match_basis_satisfied=true, and confidence at least 0.95.",
            "If any required condition is not established, set reuse_authorized=false.",
        ],
        "requests": requests,
    }
    repair = (
        f"\nThe previous response was invalid: {validation_error}. "
        "Correct the response without weakening the policy."
        if validation_error
        else ""
    )
    return (
        "Return only one JSON object with exactly keys schema_version and judgements. "
        "Each judgement must have exactly pair_id, reuse_authorized, "
        "same_real_world_entity, context_independent_identity, "
        "match_basis_satisfied, confidence, reason, and evidence_used. "
        "confidence must be a number from 0 to 1; reason must be non-empty; "
        "evidence_used must be a list of strings."
        + repair
        + "\n\n"
        + _canonical_json(payload)
    )


def _validate_payload(
    data: dict[str, Any],
    requests: list[dict[str, Any]],
    confidence_threshold: float,
) -> dict[str, dict[str, Any]]:
    if set(data) != {"schema_version", "judgements"}:
        raise ValueError("reuse judgement has unexpected top-level keys")
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("reuse judgement schema version mismatch")
    rows = data.get("judgements")
    if not isinstance(rows, list):
        raise ValueError("reuse judgements must be a list")
    expected_ids = {str(item["pair_id"]) for item in requests}
    required_keys = {
        "pair_id",
        "reuse_authorized",
        "same_real_world_entity",
        "context_independent_identity",
        "match_basis_satisfied",
        "confidence",
        "reason",
        "evidence_used",
    }
    validated: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != required_keys:
            raise ValueError("reuse judgement row has invalid keys")
        pair_id = str(row.get("pair_id") or "")
        if pair_id not in expected_ids or pair_id in validated:
            raise ValueError("reuse judgement pair ids do not match request")
        for key in (
            "reuse_authorized",
            "same_real_world_entity",
            "context_independent_identity",
            "match_basis_satisfied",
        ):
            if not isinstance(row.get(key), bool):
                raise ValueError(f"{key} must be boolean")
        confidence = row.get("confidence")
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not 0 <= confidence <= 1
        ):
            raise ValueError("confidence must be between zero and one")
        if not str(row.get("reason") or "").strip():
            raise ValueError("reuse judgement reason is required")
        evidence = row.get("evidence_used")
        if not isinstance(evidence, list) or not all(
            isinstance(item, str) and item.strip() for item in evidence
        ):
            raise ValueError("evidence_used must be a list of non-empty strings")
        if row["reuse_authorized"] and (
            not row["same_real_world_entity"]
            or not row["context_independent_identity"]
            or not row["match_basis_satisfied"]
            or confidence < confidence_threshold
        ):
            raise ValueError("positive reuse judgement violates authorization gate")
        validated[pair_id] = row
    if set(validated) != expected_ids:
        raise ValueError("reuse judgement omitted requested pairs")
    return validated


def _read_cache(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(_io_path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    judgement = payload.get("judgement")
    return judgement if isinstance(judgement, dict) else None


def _io_path(path: Path) -> Path:
    """Use an extended Windows path for long cache/audit filenames."""
    resolved = path.resolve()
    text = str(resolved)
    if os.name == "nt" and not text.startswith("\\\\?\\"):
        return Path("\\\\?\\" + text)
    return resolved


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    _io_path(path.parent).mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid4().hex}.tmp")
    _io_path(temporary).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(_io_path(temporary), _io_path(path))


def _write_audit(
    *,
    config: ReuseJudgeConfig,
    request: dict[str, Any],
    judgement: dict[str, Any],
    source: str,
) -> None:
    if config.audit_dir is None:
        return
    timestamp = datetime.now(timezone.utc)
    path = (
        config.audit_dir
        / timestamp.strftime("%Y%m%d")
        / f"{timestamp.strftime('%H%M%S_%f')}_{uuid4().hex}.json"
    )
    _atomic_json(
        path,
        {
            "schema_version": SCHEMA_VERSION,
            "policy_version": POLICY_VERSION,
            "model": config.model,
            "created_at": timestamp.isoformat(),
            "source": source,
            "request": request,
            "judgement": judgement,
        },
    )


def _denied(pair_id: str, reason: str) -> dict[str, Any]:
    return {
        "pair_id": pair_id,
        "reuse_authorized": False,
        "same_real_world_entity": False,
        "context_independent_identity": False,
        "match_basis_satisfied": False,
        "confidence": 0.0,
        "reason": reason,
        "evidence_used": ["judge failed closed"],
    }


def judge_reuse_pairs(
    requests: Iterable[dict[str, Any]],
    config: ReuseJudgeConfig | None = None,
) -> list[dict[str, Any]]:
    """Judge proposed/candidate identity pairs with cache and strict validation."""
    cfg = config or ReuseJudgeConfig.from_environment()
    normalized = [dict(item) for item in requests]
    if not normalized:
        return []

    seen: set[str] = set()
    for index, request in enumerate(normalized, start=1):
        pair_id = str(request.get("pair_id") or f"p{index:04d}").strip()
        if not pair_id or pair_id in seen:
            raise ValueError("reuse requests require unique pair_id values")
        request["pair_id"] = pair_id
        seen.add(pair_id)

    results: dict[str, dict[str, Any]] = {}
    pending: list[dict[str, Any]] = []
    cache_paths: dict[str, Path] = {}
    for request in normalized:
        pair_id = str(request["pair_id"])
        cache_path = cfg.cache_dir / f"{_request_key(request, cfg)}.json"
        cached = _read_cache(cache_path)
        if cached is not None:
            try:
                row = _validate_payload(
                    {
                        "schema_version": SCHEMA_VERSION,
                        "judgements": [cached],
                    },
                    [request],
                    cfg.confidence_threshold,
                )[pair_id]
                results[pair_id] = row
                _write_audit(
                    config=cfg,
                    request=request,
                    judgement=row,
                    source="cache",
                )
                continue
            except ValueError:
                pass
        pending.append(request)
        cache_paths[pair_id] = cache_path

    if pending:
        error = ""
        validated: dict[str, dict[str, Any]] | None = None
        try:
            for _attempt in range(cfg.max_semantic_attempts):
                response = invoke_json(
                    cfg.model,
                    _prompt(pending, error),
                    timeout_seconds=cfg.timeout_seconds,
                    max_attempts=2,
                )
                try:
                    validated = _validate_payload(
                        response.data,
                        pending,
                        cfg.confidence_threshold,
                    )
                    break
                except ValueError as exc:
                    error = str(exc)
            if validated is None:
                raise ValueError(error or "reuse judge returned no valid response")
        except Exception as exc:
            if cfg.required:
                raise RuntimeError("entity reuse judge failed") from exc
            validated = {
                str(request["pair_id"]): _denied(
                    str(request["pair_id"]),
                    f"judge failed closed: {type(exc).__name__}",
                )
                for request in pending
            }

        request_by_id = {str(item["pair_id"]): item for item in pending}
        for pair_id, row in validated.items():
            request = request_by_id[pair_id]
            _atomic_json(
                cache_paths[pair_id],
                {
                    "schema_version": SCHEMA_VERSION,
                    "policy_version": POLICY_VERSION,
                    "model": cfg.model,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "request": request,
                    "judgement": row,
                },
            )
            results[pair_id] = row
            _write_audit(
                config=cfg,
                request=request,
                judgement=row,
                source="llm" if row["confidence"] else "error",
            )

    return [results[str(item["pair_id"])] for item in normalized]
