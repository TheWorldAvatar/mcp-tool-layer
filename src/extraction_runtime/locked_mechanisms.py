"""Extraction-revision policy.

Complex ontologies (`execution_profile=complex_main`, chemistry extensions)
keep extraction revision locked on: the same judges and attempt counts always
run. Callers cannot turn that off via config, CLI, or skip-if-file-exists.

Simple main ontologies (`execution_profile=simple_main`, OntoMed) skip those
LLM judges. Empty or non-parseable payloads still retry. The domain config
profile is the switch, not a campaign flag.

Resume may skip only when a revision receipt proves the configured path
already ran.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from src.extraction_runtime.judges.closed_ledger import _validate_closed_ledger_shape
from src.extraction_runtime.llm import strip_code_fences

ONE_SHOT_ENV = "TWA_COMPARE_ONE_SHOT_EXTRACTION"

EXTRACTION_REVISION_LOCKED = True
REVISION_RECEIPT_SCHEMA = "extraction-revision.v1"
REVISION_RECEIPT_SUFFIX = ".extraction_revision.json"

MAIN_EXTRACTION_REVISION_ATTEMPTS = 5
PRE_CLOSED_LEDGER_REVISION_ATTEMPTS = 8
TOP_ENTITY_REVISION_ATTEMPTS = 5
CONTRACT_CRITIC_FORMAT_ATTEMPTS = 3
CLOSED_LEDGER_AUDIT_VOTES = 3
CLOSED_LEDGER_FORMAT_RETRIES = 3

_FORBIDDEN_EXTRACTION_DISABLE_KEYS = (
    "disable_extraction_revision",
    "skip_extraction_revision",
    "no_extraction_revision",
    "extraction_revision_disabled",
)

# Domain-config execution_profile that skips extraction judges (OntoMed).
SIMPLE_MAIN_EXECUTION_PROFILE = "simple_main"


def one_shot_extraction_enabled() -> bool:
    """Comparison ablation only. Default extraction revision stays locked on."""
    return str(os.environ.get(ONE_SHOT_ENV) or "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def _config_execution_profile(config: dict[str, Any] | None) -> str:
    payload = dict(config or {})
    explicit = str(payload.get("execution_profile") or "").strip().lower()
    if explicit:
        return explicit
    domain = payload.get("domain")
    nested = getattr(domain, "execution_profile", None)
    if nested:
        return str(nested).strip().lower()
    ontology = str(payload.get("ontology") or "").strip().lower()
    if not ontology and isinstance(domain, str):
        ontology = domain.strip().lower()
    if ontology == "medical":
        return SIMPLE_MAIN_EXECUTION_PROFILE
    return ""


def extraction_revision_enabled(config: dict[str, Any] | None = None) -> bool:
    """True for complex ontologies. False for simple_main (OntoMed)."""
    return _config_execution_profile(config) != SIMPLE_MAIN_EXECUTION_PROFILE


def skip_extraction_judges(config: dict[str, Any] | None = None) -> bool:
    """Skip LLM judges after a valid payload exists.

    True for the comparison one-shot env, and for simple_main domains.
    Empty or invalid payloads still consume the retry budget.
    """
    return one_shot_extraction_enabled() or not extraction_revision_enabled(config)


def _strip_extraction_disable_knobs(updated: dict[str, Any]) -> dict[str, Any]:
    for key in _FORBIDDEN_EXTRACTION_DISABLE_KEYS:
        updated.pop(key, None)
    for key in list(updated):
        name = str(key)
        if name.startswith("skip_iter") and name.endswith("_extraction"):
            updated.pop(key, None)
    return updated


def payload_retry_attempts() -> int:
    """Attempts used to obtain a materializable extraction payload.

    One-shot skips LLM judges after a valid payload exists. It must not
    accept empty or non-parseable extraction output; those still consume
    this budget and retry with the recorded failure feedback.
    """
    return MAIN_EXTRACTION_REVISION_ATTEMPTS


def is_closed_ledger_source(text: str) -> bool:
    return not _validate_closed_ledger_shape(strip_code_fences(text), "")


def is_semantic_hints(text: str) -> bool:
    return str(text or "").lstrip().startswith("SEMANTIC_HINTS_V1")


def revision_feedback_fingerprint(*parts: str) -> frozenset[str]:
    """Stable identity for consecutive judge rejections.

    Drops trailing source quotes so the same violation retries as the same
    fingerprint even when the cited span is rephrased.
    """
    items: list[str] = []
    for raw in parts:
        text = str(raw or "").split("[source:", 1)[0].strip()
        if text:
            items.append(text)
    return frozenset(items)


def same_revision_feedback(
    current: frozenset[str], previous: frozenset[str] | None
) -> bool:
    """True when a schema-valid candidate hit the same judge text twice."""
    return bool(current) and previous is not None and current == previous


def revision_receipt_path(artifact: Path) -> Path:
    return artifact.with_name(artifact.name + REVISION_RECEIPT_SUFFIX)


def write_extraction_revision_receipt(
    artifact: Path,
    *,
    attempts: int,
    max_attempts: int,
    accepted: bool,
    judges: list[str],
) -> Path:
    """Persist proof that extraction judges ran for this artifact."""
    artifact.parent.mkdir(parents=True, exist_ok=True)
    path = revision_receipt_path(artifact)
    payload = {
        "schema_version": REVISION_RECEIPT_SCHEMA,
        "revision_ran": True,
        "locked": True,
        "attempts": int(attempts),
        "max_attempts": int(max_attempts),
        "accepted": bool(accepted),
        "judges": list(judges),
        "artifact": artifact.name,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def extraction_revision_complete(artifact: Path) -> bool:
    """True only when the artifact exists and judges already left a receipt."""
    if not artifact.is_file() or artifact.stat().st_size <= 0:
        return False
    receipt = revision_receipt_path(artifact)
    if not receipt.is_file():
        return False
    try:
        payload = json.loads(receipt.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    schema = str(payload.get("schema_version") or "")
    return bool(payload.get("revision_ran")) and schema.startswith("extraction-revision")


def skip_if_already_revised(kind: str, artifact: Path) -> bool:
    """Resume skip. Artifacts without a receipt are re-revised, not reused."""
    if extraction_revision_complete(artifact):
        print(f"[SKIP] {kind} already revised: {artifact}")
        return True
    if artifact.is_file() and artifact.stat().st_size > 0:
        print(
            f"[INFO] {kind} exists without an extraction-revision receipt; "
            "re-running locked extraction revision"
        )
    return False


def assert_extraction_revision_locked(config: dict[str, Any] | None = None) -> None:
    """Reject campaign knobs that disable extraction revision on complex domains."""
    if not extraction_revision_enabled(config):
        return
    if EXTRACTION_REVISION_LOCKED is not True:
        raise RuntimeError("extraction revision is hard-locked on")
    payload = dict(config or {})
    if payload.get("extraction_revision") is False:
        raise ValueError("extraction revision is locked on; cannot set extraction_revision=false")
    for key in _FORBIDDEN_EXTRACTION_DISABLE_KEYS:
        if payload.get(key):
            raise ValueError(f"extraction revision is locked on; {key} is forbidden")
    for key, value in payload.items():
        name = str(key)
        if name.startswith("skip_iter") and name.endswith("_extraction") and value:
            raise ValueError(
                f"extraction revision is locked on; {name} cannot skip an extraction iteration"
            )


def apply_extraction_revision_lock(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Apply domain-config extraction revision: complex on, simple_main off."""
    updated = dict(config or {})
    if not extraction_revision_enabled(updated):
        updated["extraction_revision"] = False
        return _strip_extraction_disable_knobs(updated)
    assert_extraction_revision_locked(updated)
    updated["extraction_revision"] = True
    return _strip_extraction_disable_knobs(updated)
