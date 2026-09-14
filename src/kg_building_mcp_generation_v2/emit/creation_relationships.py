"""Emit *_creation_relationships.py."""

from __future__ import annotations

from src.extraction_prompt_generation.compile.context import (
    AgenticGenerationContext,
)
from src.kg_building_mcp_generation_v2.emit.creation_helpers import (
    _local_name,
    _py_name,
)

def _relationships_script(context: AgenticGenerationContext) -> str:
    from src.extraction_prompt_generation.compile.operation_units import (
        standalone_relationship_tool_contracts,
    )

    relationship_contracts = standalone_relationship_tool_contracts(
        context.contract.get("relationship_tool_contracts") or {},
        context.contract.get("materialization_operation_units") or {},
    )
    props = relationship_contracts or {
        name: {"predicate_local": name}
        for name, prop in (context.parsed.get("properties") or {}).items()
        if (prop or {}).get("kind") == "object"
    }
    parts = [
        f"""import re
from typing import Annotated
from pydantic import Field
from ._fixed_rdf_runtime import package_relationship_capabilities
from .{_py_name(context.ontology.name)}_creation_base import _format_error_json, _format_success_json

_RELATIONSHIP_CAPABILITIES = package_relationship_capabilities()

"""
    ]
    for prop in sorted(props.keys()):
        fn = _py_name(prop)
        relationship_contract = props.get(prop) or {}
        predicate_iri = str(relationship_contract.get("predicate_iri") or "").strip()
        range_locals = [
            str(value)
            for value in relationship_contract.get("range_locals") or []
            if str(value).strip()
        ]
        creator_tools = [
            str(value)
            for value in relationship_contract.get("creator_tools") or []
            if str(value).strip()
        ]
        external_range_iris = [
            str(value)
            for value in relationship_contract.get("external_range_iris") or []
            if str(value).strip()
        ]
        domain_locals = [
            _local_name(value)
            for value in relationship_contract.get("domain_iris") or []
            if _local_name(value)
        ]
        subject_desc = (
            "subject_iri must be an absolute IRI for domain "
            + (", ".join(domain_locals) or "T-Box-declared subject")
            + "; never a label/name/literal/plain text."
        )
        range_text = ", ".join(range_locals) or "T-Box-declared target"
        desc = (
            f"object_iri must be an absolute IRI for range {range_text}; "
            "never a label/name/literal/plain text."
        )
        if creator_tools:
            creator_text = ", ".join(creator_tools)
            desc += (
                f" For generated targets, use {creator_text}; object_iri must be the subject IRI "
                "returned by a successful creator call."
            )
        if external_range_iris and not relationship_contract.get(
            "external_creator_specs"
        ):
            desc += (
                " For external targets, pass an existing absolute IRI from the declared range; "
                "do not invent a creator tool."
            )
        doc = f'"""Add {prop}. {desc}"""'
        parts.append(
            f"""def add_{fn}(subject_iri: Annotated[str, Field(description={subject_desc!r})], object_iri: Annotated[str, Field(description={desc!r})], reuse_authorization_token: str | None = None) -> str:
    {doc}
    if not subject_iri:
        return _format_error_json("INVALID_SUBJECT_IRI", "subject_iri is required")
    if not object_iri:
        return _format_error_json("INVALID_OBJECT_IRI", "object_iri is required")
    if not re.match(r"^https?://", str(subject_iri or "")):
        return _format_error_json("INVALID_SUBJECT_IRI", "subject_iri must be an absolute IRI (http/https)")
    if not re.match(r"^https?://", str(object_iri or "")):
        return _format_error_json(
            "INVALID_OBJECT_IRI",
            "object_iri must be an absolute IRI (http/https), never a label/name/literal/plain text",
        )
    try:
        result = _RELATIONSHIP_CAPABILITIES[{predicate_iri!r}](
            subject_iri,
            object_iri,
            reuse_authorization_token,
        )
    except (KeyError, ValueError) as exc:
        return _format_error_json("RELATIONSHIP_CONTRACT_REJECTED", str(exc))
    return _format_success_json(object_iri, f"linked {prop}", created=True, enforcement=result)

"""
        )
    parts.append(
        "\n__all__ = "
        + repr([f"add_{_py_name(prop)}" for prop in sorted(props.keys())])
        + "\n"
    )
    return "".join(parts)

