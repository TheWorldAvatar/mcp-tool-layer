"""Frozen pre-composite deterministic script-generation path.

This module is intentionally separate from inferred operation-boundary code so
the former per-class creator plus standalone relationship design remains
directly callable during experiments and rollback comparisons.
"""

from __future__ import annotations

from typing import Any


def legacy_entities_script(context: Any) -> str:
    """Render the pre-composite entity module without operation-unit inputs."""
    from src.agents.scripts_and_prompts_generation.agentic_generation_runner import (
        _class_ancestors,
        _local_name,
        _ordered_member_classes,
        _ordering_datatype_properties,
        _py_name,
    )
    from src.agents.scripts_and_prompts_generation.pure_llm_generation import (
        _owned_entity_tool_contracts,
    )

    has_om2_quantity = bool(context.contract.get("om2_quantity_properties"))
    creator_contracts = _owned_entity_tool_contracts(context)
    internal_creator_classes = [
        str(item.get("class_local") or "")
        for item in creator_contracts
        if not item.get("external_range_class")
    ]
    external_creator_contracts = [
        item for item in creator_contracts if item.get("external_range_class")
    ]
    parts = [
        f"""from __future__ import annotations

from ._fixed_rdf_runtime import package_entity_capabilities{", create_om2_quantity" if has_om2_quantity else ""}
from .{_py_name(context.ontology.name)}_creation_base import (
    GRAPH,
    NS,
    RDF,
    URIRef,
    _add_literal,
    _format_success_json,
    get_top_entity_iri,
)

_ENTITY_CAPABILITIES = package_entity_capabilities()

"""
    ]
    classes = context.parsed.get("classes") or {}
    class_iris = {
        _local_name(str(item.get("class_iri") or "")): str(
            item.get("class_iri") or ""
        )
        for item in (
            (context.contract.get("ontology_publish_contract") or {}).get(
                "classes"
            )
            or []
        )
        if str(item.get("class_iri") or "").strip()
    }
    for cls in sorted(internal_creator_classes):
        fn = _py_name(cls)
        data_props = set(
            ((classes.get(cls) or {}).get("datatype_properties") or {}).keys()
        )
        if cls in _ordered_member_classes(context):
            data_props.update(_ordering_datatype_properties(context))
        data_props = sorted(data_props)
        params = ", ".join(f"{_py_name(prop)}=None" for prop in data_props)
        suffix = (", " + params) if params else ""
        literal_lines = "\n".join(
            f"    _add_literal(str(iri), {prop!r}, {_py_name(prop)})"
            for prop in data_props
        )
        builtin_parents = {"Thing", "owl:Thing", "Resource", "rdfs:Resource"}
        parent_type_lines = "\n".join(
            f"    GRAPH.add((iri, RDF.type, NS[{parent!r}]))"
            for parent in _class_ancestors(classes, cls)
            if parent not in builtin_parents and parent in classes
        )
        body_lines = "\n".join(
            line for line in [parent_type_lines, literal_lines] if line
        )
        if not body_lines:
            body_lines = "    pass"
        class_iri = class_iris.get(cls, "")
        parts.append(
            f"""def create_{fn}(label: str{suffix}) -> str:
    _ = get_top_entity_iri
    iri = URIRef(_ENTITY_CAPABILITIES[{class_iri!r}](label))
    created = True
{body_lines}
    return _format_success_json(iri, f"created or reused {cls}", created=created)

"""
        )
    for spec in external_creator_contracts:
        tool_name = str((spec or {}).get("public_tool") or "").strip()
        class_iri = str((spec or {}).get("class_iri") or "").strip()
        class_local = str((spec or {}).get("class_local") or "").strip()
        if not tool_name or not class_iri:
            continue
        parts.append(
            f"""def {tool_name}(label: str) -> str:
    iri = URIRef(_ENTITY_CAPABILITIES[{class_iri!r}](label))
    return _format_success_json(iri, "created or reused external T-Box range {class_local}", created=True)

"""
        )
    manifest = [
        f"create_{_py_name(cls)}" for cls in sorted(internal_creator_classes)
    ]
    manifest.extend(
        str((spec or {}).get("public_tool") or "").strip()
        for spec in external_creator_contracts
        if str((spec or {}).get("public_tool") or "").strip()
    )
    if has_om2_quantity:
        manifest.append("create_om2_quantity")
    parts.append(f"\n__all__ = {manifest!r}\n")
    return "".join(parts)


def force_legacy_operation_contract(context: Any) -> None:
    """Remove inferred decisions and restore the split operation surface."""
    from src.agents.scripts_and_prompts_generation.materialization_operation_units import (
        compile_materialization_operation_units,
    )

    context.contract.pop("materialization_operation_candidates", None)
    context.contract.pop("materialization_operation_decisions", None)
    context.contract["materialization_operation_units"] = (
        compile_materialization_operation_units(
            parsed=context.parsed,
            contract=context.contract,
            iteration_plan=context.iteration_blueprint,
        )
    )


def generate_legacy_script_slice(context: Any) -> list[str]:
    """Explicit legacy entry point retained independently of inference mode."""
    force_legacy_operation_contract(context)
    from src.agents.scripts_and_prompts_generation.agentic_generation_runner import (
        generate_deterministic_script_slice,
    )

    return generate_deterministic_script_slice(context)
