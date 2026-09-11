"""Emit *_creation_entities.py (atomic or legacy split)."""

from __future__ import annotations

from typing import Any

from src.extraction_prompt_generation.compile.context import (
    AgenticGenerationContext,
)
from src.extraction_prompt_generation.compile.operation_units import (
    owned_entity_tool_contracts,
)
from src.kg_building_mcp_generation.emit.creation_helpers import (
    _py_name,
)

def legacy_entities_script(context: Any) -> str:
    """Render the pre-composite entity module without operation-unit inputs."""
    from src.extraction_prompt_generation.compile.operation_units import (
        owned_entity_tool_contracts as _owned_entity_tool_contracts,
    )
    from src.kg_building_mcp_generation.emit.creation_helpers import (
        _class_ancestors,
        _local_name,
        _ordered_member_classes,
        _ordering_datatype_properties,
        _py_name,
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

def _entities_script(context: AgenticGenerationContext) -> str:
    if (
        (
            context.contract.get("materialization_operation_units") or {}
        ).get("inference_mode")
        != "accepted_atomic"
    ):
        return legacy_entities_script(context)
    has_om2_quantity = bool(context.contract.get("om2_quantity_properties"))
    creator_contracts = owned_entity_tool_contracts(context)
    parts = [
        """from __future__ import annotations

import re

from . import _fixed_rdf_runtime as rdf_runtime
"""
    ]
    if has_om2_quantity:
        parts.append(
            "from ._fixed_rdf_runtime import create_om2_quantity\n\n"
        )
    parts.append(
        """_ENTITY_CREATORS = rdf_runtime.package_entity_capabilities()
_ORDERED_CREATORS = rdf_runtime.package_ordered_entity_capabilities()
_DATATYPE_WRITERS = rdf_runtime.package_datatype_capabilities()
_RELATIONSHIP_WRITERS = rdf_runtime.package_relationship_capabilities()


def _validate_label(value: object, field: str) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return rdf_runtime.error_json(code="INVALID_LABEL", message=f"{field} must be a non-empty string")
    return None


def _validate_iri(value: object, field: str) -> str | None:
    if not isinstance(value, str) or re.match(r"^https?://", value.strip()) is None:
        return rdf_runtime.error_json(code="INVALID_IRI", message=f"{field} must be an absolute HTTP(S) IRI")
    return None


def _validate_scalar(value: object, expected: str, field: str, required: bool = False) -> str | None:
    if value is None:
        return rdf_runtime.error_json(code="MISSING_REQUIRED_INPUT", message=f"{field} is required") if required else None
    valid = (
        isinstance(value, str) if expected == "str" else
        isinstance(value, bool) if expected == "bool" else
        isinstance(value, int) and not isinstance(value, bool) if expected == "int" else
        isinstance(value, (int, float)) and not isinstance(value, bool) if expected == "float" else
        False
    )
    if not valid:
        return rdf_runtime.error_json(code="INVALID_DATATYPE", message=f"{field} must be {expected}")
    return None


"""
    )
    entity_manifest: list[str] = []
    for creator in creator_contracts:
        tool_name = str(creator.get("public_tool") or "").strip()
        class_iri = str(creator.get("class_iri") or "").strip()
        class_local = str(creator.get("class_local") or "").strip()
        if not tool_name or not class_iri:
            continue
        entity_manifest.append(tool_name)
        ordering_local = str(creator.get("ordering_property_local") or "")
        required_params: list[tuple[str, str]] = []
        optional_params: list[tuple[str, str]] = []
        validation_lines = [
            "    error = _validate_label(label, \"label\")",
            "    if error: return error",
        ]
        owner_writer_lines: list[str] = []
        for datatype in creator.get("datatype_inputs") or []:
            property_local = str(datatype.get("property_local") or "")
            parameter_name = _py_name(property_local)
            python_type = str(datatype.get("python_type") or "str")
            required = bool(datatype.get("required"))
            (required_params if required else optional_params).append(
                (parameter_name, python_type)
            )
            validation_lines.extend(
                [
                    f"    error = _validate_scalar({parameter_name}, {python_type!r}, {parameter_name!r}, required={required!r})",
                    "    if error: return error",
                ]
            )
            if property_local != ordering_local:
                owner_writer_lines.append(
                    f"            if {parameter_name} is not None: _DATATYPE_WRITERS[{str(datatype.get('property_iri') or '')!r}](iri, {parameter_name})"
                )
        edge_writer_lines: list[str] = []
        dependent_metadata: list[str] = []
        for edge_index, edge in enumerate(creator.get("required_edges") or []):
            predicate_iri = str(edge.get("predicate_iri") or "")
            if edge.get("target_resolution") == "existing_iri_parameter":
                parameter_name = str(edge.get("parameter_name") or "")
                required_params.append((parameter_name, "str"))
                validation_lines.extend(
                    [
                        f"    error = _validate_iri({parameter_name}, {parameter_name!r})",
                        "    if error: return error",
                    ]
                )
                if edge.get("direction") == "container_as_subject_owner_as_object":
                    edge_writer_lines.append(
                        f"            _RELATIONSHIP_WRITERS[{predicate_iri!r}]({parameter_name}, iri)"
                    )
                else:
                    edge_writer_lines.append(
                        f"            _RELATIONSHIP_WRITERS[{predicate_iri!r}](iri, {parameter_name})"
                    )
            elif edge.get("target_resolution") == "same_operation_create":
                label_parameter = str(edge.get("label_parameter") or "")
                required_params.append((label_parameter, "str"))
                validation_lines.extend(
                    [
                        f"    error = _validate_label({label_parameter}, {label_parameter!r})",
                        "    if error: return error",
                    ]
                )
                dependent_name = f"dependent_iri_{edge_index}"
                edge_writer_lines.append(
                    f"            {dependent_name} = _ENTITY_CREATORS[{str(edge.get('dependent_fixed_capability_key') or '')!r}]({label_parameter})"
                )
                for dependent_input in edge.get("datatype_inputs") or []:
                    parameter_name = str(dependent_input.get("parameter_name") or "")
                    python_type = str(dependent_input.get("python_type") or "str")
                    required = bool(dependent_input.get("required"))
                    (required_params if required else optional_params).append(
                        (parameter_name, python_type)
                    )
                    validation_lines.extend(
                        [
                            f"    error = _validate_scalar({parameter_name}, {python_type!r}, {parameter_name!r}, required={required!r})",
                            "    if error: return error",
                        ]
                    )
                    edge_writer_lines.append(
                        f"            if {parameter_name} is not None: _DATATYPE_WRITERS[{str(dependent_input.get('property_iri') or '')!r}]({dependent_name}, {parameter_name})"
                    )
                edge_writer_lines.append(
                    f"            _RELATIONSHIP_WRITERS[{predicate_iri!r}](iri, {dependent_name})"
                )
                dependent_metadata.append(dependent_name)
        unique_required = list(dict.fromkeys(required_params))
        unique_optional = [
            item for item in dict.fromkeys(optional_params) if item not in unique_required
        ]
        signature_parts = ["label: str"]
        signature_parts.extend(f"{name}: {kind}" for name, kind in unique_required)
        signature_parts.extend(
            f"{name}: {kind} | None = None" for name, kind in unique_optional
        )
        ordering_parameter = _py_name(ordering_local)
        creator_line = (
            f"            iri = _ORDERED_CREATORS[{class_iri!r}](label, {ordering_parameter})"
            if creator.get("ordered_member")
            else f"            iri = _ENTITY_CREATORS[{class_iri!r}](label)"
        )
        mutation_lines = [
            "    try:",
            "        with rdf_runtime.atomic_graph_transaction():",
            creator_line,
            *owner_writer_lines,
            *edge_writer_lines,
            "    except Exception as exc:",
            "        return rdf_runtime.error_json(code=\"ATOMIC_CREATE_REJECTED\", message=str(exc))",
        ]
        metadata = (
            f", dependent_iris=[{', '.join(dependent_metadata)}]"
            if dependent_metadata
            else ""
        )
        parts.append(
            f"""def {tool_name}({", ".join(signature_parts)}) -> str:
{chr(10).join(validation_lines)}
{chr(10).join(mutation_lines)}
    return rdf_runtime.success_json(iri=iri, message={f"{class_local} created"!r}{metadata})


"""
        )
    if has_om2_quantity:
        entity_manifest.append("create_om2_quantity")
    parts.append(f"\n__all__ = {entity_manifest!r}\n")
    return "".join(parts)

