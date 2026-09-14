"""Instantiate a covering occurrence script from compiled public tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from src.extraction_prompt_generation.compile.operation_units import _python_name

ROOT_SENTINEL = "$root"
NESTED_SENTINEL = "$nested:"
SCRIPT_SCHEMA = "tbox-instantiated-occurrence-script.v1"


@dataclass(frozen=True)
class MockCall:
    name: str
    kind: str
    arguments: dict[str, Any]
    identity_key: tuple[Any, ...]


@dataclass
class MockScript:
    schema_version: str
    root_label: str
    top_class_iri: str
    root_iri: str
    calls: list[MockCall] = field(default_factory=list)


def _local(iri: str) -> str:
    text = str(iri or "")
    if "#" in text:
        return text.rsplit("#", 1)[-1]
    return text.rstrip("/").rsplit("/", 1)[-1]


def _call_ref(tool_name: str) -> str:
    return f"$call:{tool_name}"


def encode_nested_ref(host_name: str, predicate_iri: str, label: str) -> str:
    return NESTED_SENTINEL + "|".join((host_name, predicate_iri, label))


def decode_nested_ref(value: str) -> tuple[str, str, str]:
    payload = str(value)[len(NESTED_SENTINEL) :]
    host, predicate_iri, label = payload.split("|", 2)
    return host, predicate_iri, label


def nested_host_name(value: str) -> str | None:
    if isinstance(value, str) and value.startswith(NESTED_SENTINEL):
        return decode_nested_ref(value)[0]
    return None


def _nested_dummy_label(item: Mapping[str, Any]) -> str:
    target = str(item.get("target_class_local") or "Dependent")
    return f"{target}-1"


def _dummy_scalar(python_type: str, token: str) -> Any:
    if python_type == "int":
        return 1
    if python_type == "bool":
        return True
    if python_type == "float":
        return 1.0
    return token


def _quantity_example(surface: Mapping[str, Any], range_iri: str) -> str:
    spec = (surface.get("classes") or {}).get(range_iri) or {}
    labels = [str(item) for item in spec.get("example_labels") or [] if str(item)]
    if labels:
        return labels[0]
    return str(surface.get("example_number") or "1") + " <unit>"


def _parent_ref(
    tool: Mapping[str, Any],
    *,
    compiled: Mapping[str, Any],
    contract: Mapping[str, Any],
) -> str | None:
    if not tool.get("parent_parameter"):
        return None
    if tool.get("parent_binds_to_session_root", True):
        return ROOT_SENTINEL
    predicate_iri = str(tool.get("parent_predicate_iri") or "")
    spec = {}
    for item in (contract.get("relationship_tool_contracts") or {}).values():
        if isinstance(item, Mapping) and str(item.get("predicate_iri") or "") == predicate_iri:
            spec = item
            break
    domains = {str(value) for value in spec.get("domain_iris") or []}
    top_iri = str((contract.get("top_entity") or {}).get("class_iri") or "")
    if top_iri and top_iri in domains:
        return ROOT_SENTINEL
    for other in compiled.get("public_tools") or []:
        owner = str(other.get("owner_class_iri") or "")
        name = str(other.get("name") or "")
        if owner in domains and name and name != tool.get("name"):
            return _call_ref(name)
    for other in compiled.get("public_tools") or []:
        name = str(other.get("name") or "")
        if not name or name == tool.get("name"):
            continue
        for group in ("fresh_dependents", "reusable_links"):
            for item in other.get(group) or []:
                target = str(item.get("target_class_iri") or "")
                predicate_iri = str(item.get("predicate_iri") or "")
                if target in domains and predicate_iri:
                    return encode_nested_ref(
                        name, predicate_iri, _nested_dummy_label(item)
                    )
    raise ValueError(f"cannot resolve parent occurrence for {tool.get('name')}")


def _identity_key(
    tool: Mapping[str, Any],
    arguments: Mapping[str, Any],
    parent_key: tuple[Any, ...],
) -> tuple[Any, ...]:
    identity = tool.get("identity_contract") or {}
    kind = str(identity.get("kind") or "semantic_occurrence")
    owner = str(tool.get("owner_class_iri") or "")
    if kind == "ordered":
        ordering = _python_name(str(tool.get("ordering_property_local") or ""))
        return ("ordered", owner, parent_key, arguments.get(ordering))
    if kind == "unique_parent":
        return ("unique_parent", owner, parent_key)
    if kind == "adopted_focus":
        return ("focus", owner)
    return ("occurrence", owner, arguments.get("label"))


def _fill_create_arguments(
    tool: Mapping[str, Any],
    *,
    parent_ref: str | None,
    surface: Mapping[str, Any],
    order: int,
    public_owner_iris: set[str] | None = None,
) -> dict[str, Any]:
    owner_local = str(tool.get("owner_class_local") or "Occurrence")
    public_owners = public_owner_iris or set()
    arguments: dict[str, Any] = {"label": f"{owner_local}-1"}
    parent = str(tool.get("parent_parameter") or "")
    if parent and parent_ref is not None:
        arguments[parent] = parent_ref
    ordering = _python_name(str(tool.get("ordering_property_local") or ""))
    if ordering:
        arguments[ordering] = int(order)
    for item in tool.get("datatype_inputs") or []:
        name = _python_name(str(item.get("property_local") or ""))
        if not name or name == ordering:
            continue
        python_type = str(item.get("python_type") or "str")
        arguments[name] = _dummy_scalar(python_type, f"{name}-1")
    for item in list(tool.get("quantities") or []) + list(
        tool.get("parent_quantities") or []
    ):
        name = str(item.get("parameter") or "")
        range_iri = str(item.get("range_iri") or "")
        if name:
            arguments[name] = _quantity_example(surface, range_iri)
    skipped_parent_locals: set[str] = set()
    for group in ("fresh_dependents", "reusable_links"):
        for item in tool.get(group) or []:
            if str(item.get("target_class_iri") or "") in public_owners:
                skipped_parent_locals.add(str(item.get("predicate_local") or ""))
                continue
            label_name = str(item.get("label_parameter") or "")
            target = str(item.get("target_class_local") or "Dependent")
            if label_name:
                arguments[label_name] = f"{target}-1"
            for datatype in item.get("datatype_inputs") or []:
                name = str(datatype.get("parameter_name") or "")
                if not name:
                    continue
                python_type = str(datatype.get("python_type") or "str")
                arguments[name] = _dummy_scalar(python_type, f"{name}-1")
    for item in tool.get("nested_reusable_links") or []:
        if str(item.get("target_class_iri") or "") in public_owners:
            continue
        if str(item.get("parent_predicate_local") or "") in skipped_parent_locals:
            continue
        label_name = str(item.get("label_parameter") or "")
        target = str(item.get("child_predicate_local") or "Nested")
        if label_name:
            arguments[label_name] = f"{_local(target)}-1"
    return arguments


def instantiate_covering_script(
    compiled: Mapping[str, Any],
    quantity_surface: Mapping[str, Any],
    *,
    contract: Mapping[str, Any],
    root_iri: str,
) -> MockScript:
    """One create_* per public tool, plus leftover linkers, filled from T-Box types."""
    top = compiled.get("top_entity_class_iri") or str(
        (contract.get("top_entity") or {}).get("class_iri") or ""
    )
    top_local = compiled.get("top_entity_class_local") or _local(str(top))
    root_label = f"{top_local}-1" if top_local else "Root-1"
    tools = [item for item in compiled.get("public_tools") or [] if item.get("name")]
    parent_of = {
        str(tool.get("name")): _parent_ref(tool, compiled=compiled, contract=contract)
        for tool in tools
    }
    pending = [str(tool.get("name")) for tool in tools]
    ordered_names: list[str] = []
    while pending:
        progress = False
        remaining: list[str] = []
        for name in pending:
            parent = parent_of.get(name)
            if parent is None or parent == ROOT_SENTINEL:
                ordered_names.append(name)
                progress = True
                continue
            if isinstance(parent, str) and parent.startswith("$call:"):
                depends = parent.split(":", 1)[1]
                if depends in ordered_names:
                    ordered_names.append(name)
                    progress = True
                    continue
            nested_host = nested_host_name(parent) if isinstance(parent, str) else None
            if nested_host:
                if nested_host in ordered_names:
                    ordered_names.append(name)
                    progress = True
                    continue
            remaining.append(name)
        if not progress:
            ordered_names.extend(remaining)
            break
        pending = remaining
    by_name = {str(tool.get("name")): tool for tool in tools}
    orders: dict[str, int] = {}
    calls: list[MockCall] = []
    keys: dict[str, tuple[Any, ...]] = {}
    for name in ordered_names:
        tool = by_name[name]
        parent_ref = parent_of.get(name)
        if parent_ref == ROOT_SENTINEL:
            parent_key: tuple[Any, ...] = ("root",)
        elif isinstance(parent_ref, str) and parent_ref.startswith("$call:"):
            parent_key = keys[parent_ref.split(":", 1)[1]]
        elif isinstance(parent_ref, str) and parent_ref.startswith(NESTED_SENTINEL):
            host, predicate_iri, label = decode_nested_ref(parent_ref)
            parent_key = ("nested", host, predicate_iri, label)
        else:
            parent_key = ("root",)
        bucket = parent_ref or ROOT_SENTINEL
        if tool.get("ordered_member"):
            next_order = orders.get(bucket, 0) + 1
            orders[bucket] = next_order
        else:
            next_order = 1
        arguments = _fill_create_arguments(
            tool,
            parent_ref=parent_ref,
            surface=quantity_surface,
            order=next_order,
            public_owner_iris={
                str(item.get("owner_class_iri") or "")
                for item in tools
                if str(item.get("owner_class_iri") or "")
            },
        )
        identity = _identity_key(tool, arguments, parent_key)
        keys[name] = identity
        calls.append(
            MockCall(
                name=name,
                kind="create",
                arguments=arguments,
                identity_key=identity,
            )
        )
    for linker in compiled.get("public_linkers") or []:
        name = str(linker.get("name") or "")
        if not name:
            continue
        target = str(linker.get("object_class_local") or "Linked")
        arguments = {
            "subject_iri": ROOT_SENTINEL,
            "object_label": f"{target}-1",
        }
        calls.append(
            MockCall(
                name=name,
                kind="link",
                arguments=arguments,
                identity_key=("link", name, ("root",), arguments["object_label"]),
            )
        )
    return MockScript(
        schema_version=SCRIPT_SCHEMA,
        root_label=root_label,
        top_class_iri=str(top),
        root_iri=str(root_iri),
        calls=calls,
    )


def drop_required_identity_argument(script: MockScript, compiled: Mapping[str, Any]) -> MockScript:
    """Generic invalid mutation: omit a required identity argument on the first ordered create."""
    tools = {str(item.get("name")): item for item in compiled.get("public_tools") or []}
    mutated: list[MockCall] = []
    dropped = False
    for call in script.calls:
        if dropped or call.kind != "create":
            mutated.append(call)
            continue
        tool = tools.get(call.name) or {}
        identity = tool.get("identity_contract") or {}
        args = [
            str(value)
            for value in identity.get("identity_args") or []
            if str(value) and str(value) != "parent_iri" and str(value) != "label"
        ]
        if not args:
            mutated.append(call)
            continue
        arguments = dict(call.arguments)
        arguments.pop(args[0], None)
        mutated.append(
            MockCall(
                name=call.name,
                kind=call.kind,
                arguments=arguments,
                identity_key=call.identity_key,
            )
        )
        dropped = True
    if not dropped:
        raise ValueError("no required identity argument available to drop")
    return MockScript(
        schema_version=script.schema_version,
        root_label=script.root_label,
        top_class_iri=script.top_class_iri,
        root_iri=script.root_iri,
        calls=mutated,
    )
