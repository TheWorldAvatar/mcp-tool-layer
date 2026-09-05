from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


GLOBAL_REUSE_SCOPES = frozenset(
    {"global", "global_value", "global_reference", "legacy_unspecified"}
)
DOCUMENT_REUSE_SCOPES = frozenset({"document"})
ENTITY_REUSE_SCOPES = frozenset({"top_entity"})
NON_REUSE_SCOPES = frozenset(
    {
        "never",
        "never_by_generic_reuse",
        "never_numeric_payload",
        "occurrence_local",
        "prohibited",
    }
)
KNOWN_REUSE_SCOPES = (
    GLOBAL_REUSE_SCOPES
    | DOCUMENT_REUSE_SCOPES
    | ENTITY_REUSE_SCOPES
    | NON_REUSE_SCOPES
)
# lookup_scope values that must reject an empty proposed-entity probe.
# Derived only from storage scope, never from class or ontology names.
EXISTING_CHECK_EVIDENCE_REQUIRED_SCOPES = frozenset({"central", "document"})


def prohibited_class_locals(policy: dict[str, Any] | None) -> set[str]:
    """Return classes explicitly forbidden from materialization by policy."""
    return {
        str(item.get("class_local") or "").strip()
        for item in (policy or {}).get("classes") or []
        if isinstance(item, dict)
        and str(item.get("reuse_scope") or "").strip() == "prohibited"
        and str(item.get("class_local") or "").strip()
    }


def reuse_storage_scope(reuse_scope: str, *, reusable: bool) -> str:
    """Map reviewed policy vocabulary to one deterministic runtime store."""
    scope = str(reuse_scope or "").strip()
    if scope not in KNOWN_REUSE_SCOPES:
        raise ValueError(f"unknown reuse_scope: {scope!r}")
    if not reusable:
        return "none"
    if scope in GLOBAL_REUSE_SCOPES:
        return "central"
    if scope in DOCUMENT_REUSE_SCOPES:
        return "document"
    if scope in ENTITY_REUSE_SCOPES:
        return "scoped"
    raise ValueError(f"reusable class cannot use non-reuse scope: {scope!r}")


def _py_name(value: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_]", "_", str(value or "").strip())
    if name and name[0].isdigit():
        name = f"_{name}"
    return name


def load_reuse_policy(path: str | Path) -> dict[str, Any]:
    """Load and normalize the reviewed operational class-reuse policy."""
    policy_path = Path(path).resolve()
    raw_text = policy_path.read_text(encoding="utf-8")
    payload = json.loads(raw_text)
    if not isinstance(payload, dict):
        raise ValueError("reuse policy must be a JSON object")
    if payload.get("schema_version") != "binary-class-reuse-review.v0":
        raise ValueError("reuse policy must use binary-class-reuse-review.v0")
    if payload.get("generated_candidate") is True and payload.get(
        "status"
    ) != "approved_for_runtime":
        raise ValueError(
            "generated reuse policy is not approved_for_runtime; "
            "complete match_basis review first"
        )

    classes: list[dict[str, Any]] = []
    seen_iris: set[str] = set()
    for index, item in enumerate(payload.get("classes") or []):
        if not isinstance(item, dict):
            raise ValueError(f"reuse policy classes[{index}] must be an object")
        class_iri = str(item.get("class_iri") or "").strip()
        class_local = str(item.get("class_local") or "").strip()
        reusable = item.get("reusable")
        if not class_iri or not class_local or not isinstance(reusable, bool):
            raise ValueError(
                f"reuse policy classes[{index}] requires class_iri, class_local, and boolean reusable"
            )
        if class_iri in seen_iris:
            raise ValueError(f"reuse policy class appears more than once: {class_iri}")
        seen_iris.add(class_iri)
        reuse_scope = str(item.get("reuse_scope") or "").strip()
        match_basis = str(item.get("match_basis") or "").strip()
        if reusable and (not reuse_scope or not match_basis):
            raise ValueError(
                f"reusable class requires reuse_scope and match_basis: {class_iri}"
            )
        reuse_storage_scope(reuse_scope, reusable=reusable)
        classes.append(
            {
                "class_iri": class_iri,
                "class_local": class_local,
                "reusable": reusable,
                "reuse_scope": reuse_scope,
                "match_basis": match_basis,
                "rationale": str(item.get("rationale") or "").strip(),
                "confidence": str(item.get("confidence") or "").strip(),
            }
        )
    if not classes:
        raise ValueError("reuse policy contains no class decisions")
    return {
        "schema_version": "operational-reuse-policy.v1",
        "source_schema_version": payload["schema_version"],
        "source_sha256": hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
        "decision_semantics": dict(payload.get("decision_semantics") or {}),
        "classes": classes,
    }


def attach_reuse_policy(
    contract: dict[str, Any], policy_path: str | Path
) -> dict[str, Any]:
    """Attach a validated derived policy to a generation contract."""
    policy = load_reuse_policy(policy_path)
    contract["reuse_policy"] = policy
    publish = dict(contract.get("ontology_publish_contract") or {})
    publish["reuse_policy"] = policy
    contract["ontology_publish_contract"] = publish
    return policy


def existing_entity_check_contracts(
    *,
    parsed: dict[str, Any],
    contract: dict[str, Any],
    legacy_all_classes_when_absent: bool = True,
) -> list[dict[str, Any]]:
    """Project central-reuse and scoped-reference checks onto the tool surface."""
    classes = parsed.get("classes") or {}
    known_by_iri: dict[str, tuple[str, dict[str, Any]]] = {
        str((spec or {}).get("iri") or "").strip(): (str(local), spec or {})
        for local, spec in classes.items()
        if str((spec or {}).get("iri") or "").strip()
    }
    for item in contract.get("external_class_creators") or []:
        class_iri = str((item or {}).get("class_iri") or "").strip()
        class_local = str((item or {}).get("class_local") or "").strip()
        if class_iri and class_local:
            known_by_iri.setdefault(class_iri, (class_local, {}))

    policy = contract.get("reuse_policy") or (
        (contract.get("ontology_publish_contract") or {}).get("reuse_policy")
        or {}
    )
    decisions = policy.get("classes") or []
    if not decisions and legacy_all_classes_when_absent:
        decisions = [
            {
                "class_iri": str((spec or {}).get("iri") or "").strip(),
                "class_local": local,
                "reusable": True,
                "reuse_scope": "legacy_unspecified",
                "match_basis": "legacy label inventory",
            }
            for local, spec in sorted(classes.items())
            if str((spec or {}).get("iri") or "").strip()
        ]
        decisions.extend(
            {
                "class_iri": str((item or {}).get("class_iri") or "").strip(),
                "class_local": str((item or {}).get("class_local") or "").strip(),
                "reusable": True,
                "reuse_scope": "legacy_unspecified",
                "match_basis": "legacy label inventory",
                "public_tool": str(
                    (item or {}).get("check_tool_name")
                    or f"check_existing_{_py_name(str((item or {}).get('class_local') or ''))}"
                ).strip(),
            }
            for item in contract.get("external_class_creators") or []
            if str((item or {}).get("class_iri") or "").strip()
            and str((item or {}).get("class_local") or "").strip()
        )

    checks: list[dict[str, Any]] = []
    seen_tools: set[str] = set()
    for decision in decisions:
        if not isinstance(decision, dict):
            continue
        class_iri = str(decision.get("class_iri") or "").strip()
        if class_iri not in known_by_iri:
            continue
        known_local, class_spec = known_by_iri[class_iri]
        class_local = str(decision.get("class_local") or known_local).strip()
        tool_name = str(
            decision.get("public_tool")
            or f"check_existing_{_py_name(class_local)}"
        ).strip()
        if not class_local or tool_name in seen_tools:
            continue
        seen_tools.add(tool_name)
        checks.append(
            {
                "class_local": class_local,
                "class_iri": class_iri,
                "public_tool": tool_name,
                "reusable": decision.get("reusable") is True,
                "lookup_scope": reuse_storage_scope(
                    str(decision.get("reuse_scope") or "").strip(),
                    reusable=decision.get("reusable") is True,
                )
                if decision.get("reusable") is True
                else "scoped",
                "reuse_authorized": (
                    decision.get("reusable") is True
                    and str(decision.get("reuse_scope") or "").strip()
                    not in ENTITY_REUSE_SCOPES
                ),
                "reference_resolution_only": (
                    decision.get("reusable") is not True
                    or str(decision.get("reuse_scope") or "").strip()
                    in ENTITY_REUSE_SCOPES
                ),
                "reuse_scope": str(decision.get("reuse_scope") or "").strip(),
                "match_basis": str(decision.get("match_basis") or "").strip(),
                "rationale": str(decision.get("rationale") or "").strip(),
                "class_comment": str(
                    (class_spec or {}).get("comment") or ""
                ).strip(),
                "datatype_properties": dict(
                    (class_spec or {}).get("datatype_properties") or {}
                ),
                "object_properties": dict(
                    (class_spec or {}).get("object_properties") or {}
                ),
            }
        )
    return checks
