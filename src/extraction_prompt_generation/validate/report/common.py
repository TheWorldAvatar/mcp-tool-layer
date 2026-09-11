"""Shared helpers for mechanical validation reports.

Import, AST, graph, and obligation helpers used by every checker.
See report/README.md.
"""

from __future__ import annotations

import ast
import asyncio
import hashlib
import importlib.util
import inspect
import json
import re
import sys
import types
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from rdflib import Graph, URIRef
from rdflib.namespace import RDF

from src.extraction_prompt_generation.compile.context import (
    AgenticGenerationContext,
)
from src.extraction_prompt_generation.compile.reuse_policy import (
    EXISTING_CHECK_EVIDENCE_REQUIRED_SCOPES,
)


EXPECTED_SCRIPT_SUFFIXES = (
    "_creation_base.py",
    "_creation_checks.py",
    "_creation_entities.py",
    "_creation_relationships.py",
    "main.py",
)


def _required_operation_fixture_triples(
    *,
    operation_units: Mapping[str, Any],
    owner_class_iri: str,
    owner: URIRef,
    parent: URIRef,
    token: str,
) -> list[tuple[URIRef, URIRef, URIRef]]:
    """Build a valid required-edge fixture from all matching operation units."""
    creators: list[Mapping[str, Any]] = []
    for raw_unit in operation_units.get("units") or []:
        unit = raw_unit if isinstance(raw_unit, Mapping) else {}
        candidate = unit.get("creator_contract") or {}
        if (
            isinstance(candidate, Mapping)
            and str(candidate.get("class_iri") or "") == owner_class_iri
        ):
            creators.append(candidate)

    triples: list[tuple[URIRef, URIRef, URIRef]] = []
    for unit_index, creator in enumerate(creators):
        for edge_index, raw_edge in enumerate(creator.get("required_edges") or []):
            edge = raw_edge if isinstance(raw_edge, Mapping) else {}
            predicate_iri = str(edge.get("predicate_iri") or "").strip()
            if not predicate_iri:
                continue
            predicate = URIRef(predicate_iri)
            resolution = str(edge.get("target_resolution") or "")
            direction = str(edge.get("direction") or "")
            if resolution == "existing_iri_parameter":
                target = parent
            elif resolution == "same_operation_create":
                target = URIRef(
                    f"urn:validator:required-target:{token}:{unit_index}:{edge_index}"
                )
                dependent_class_iri = str(
                    edge.get("dependent_class_iri") or ""
                ).strip()
                if dependent_class_iri:
                    triples.append((target, RDF.type, URIRef(dependent_class_iri)))
            else:
                continue

            if direction == "container_as_subject_owner_as_object":
                triples.append((target, predicate, owner))
            else:
                triples.append((owner, predicate, target))
    return list(dict.fromkeys(triples))


def _seed_existing_operation_targets(
    graph: Graph,
    creator_contract: Mapping[str, Any],
    call_kwargs: Mapping[str, Any],
) -> None:
    """Seed pre-existing typed inputs required by an atomic creator probe."""
    for raw_edge in creator_contract.get("required_edges") or []:
        edge = raw_edge if isinstance(raw_edge, Mapping) else {}
        if edge.get("target_resolution") != "existing_iri_parameter":
            continue
        parameter_name = str(edge.get("parameter_name") or "").strip()
        target_iri = str(call_kwargs.get(parameter_name) or "").strip()
        if not target_iri:
            continue
        target_classes = {
            str(value).strip()
            for key in (
                "container_class_iris",
                "target_class_iris",
                "range_class_iris",
            )
            for value in (edge.get(key) or [])
            if str(value).strip()
        }
        dependent_class_iri = str(
            edge.get("dependent_class_iri") or ""
        ).strip()
        if dependent_class_iri:
            target_classes.add(dependent_class_iri)
        for class_iri in target_classes:
            graph.add((URIRef(target_iri), RDF.type, URIRef(class_iri)))


def empty_existing_check_probe_failures(
    *,
    artifact_name: str,
    tool_name: str,
    lookup_scope: str,
    payload: Mapping[str, Any],
    expected_reuse_authorized: bool = False,
    expected_reference_resolution_only: bool = False,
) -> list[str]:
    """Score one empty-argument `check_existing_*` call against lookup_scope only."""
    failures: list[str] = []
    if lookup_scope in EXISTING_CHECK_EVIDENCE_REQUIRED_SCOPES:
        if (
            str(payload.get("status") or "").casefold() != "rejected"
            or payload.get("code") != "PROPOSED_ENTITY_EVIDENCE_REQUIRED"
        ):
            failures.append(
                f"{artifact_name}: {tool_name} must fail closed with "
                "PROPOSED_ENTITY_EVIDENCE_REQUIRED when proposed "
                "entity evidence is absent"
            )
        return failures
    expected_metadata = {
        "lookup_scope": lookup_scope,
        "reuse_authorized": expected_reuse_authorized,
        "reference_resolution_only": expected_reference_resolution_only,
    }
    for field, expected_value in expected_metadata.items():
        if payload.get(field) != expected_value:
            failures.append(
                f"{artifact_name}: {tool_name} must return {field}="
                f"{expected_value!r}; observed={payload.get(field)!r}"
            )
    if not any(
        isinstance(payload.get(field), list)
        for field in ("instances", "candidates", "entities")
    ):
        failures.append(
            f"{artifact_name}: {tool_name} must return a bounded candidate list"
        )
    return failures


def _graph_fingerprint(graph: Graph) -> str:
    """Hash graph content independently of Turtle serialization order."""
    canonical = "\n".join(sorted(f"{s.n3()} {p.n3()} {o.n3()} ." for s, p, o in graph))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        owner = _call_name(node.value)
        return f"{owner}.{node.attr}" if owner else node.attr
    return ""


def _string_constant(node: ast.AST) -> str:
    return str(node.value) if isinstance(node, ast.Constant) and isinstance(node.value, str) else ""


def _statically_selected_string(
    node: ast.AST,
    bindings: Mapping[str, str] | None = None,
) -> str:
    """Resolve only strings selected by an unambiguous compile-time expression."""
    direct = _string_constant(node)
    if direct:
        return direct
    if isinstance(node, ast.Name):
        return str((bindings or {}).get(node.id) or "")
    if (
        isinstance(node, ast.IfExp)
        and isinstance(node.test, ast.Constant)
        and isinstance(node.test.value, bool)
    ):
        return _statically_selected_string(
            node.body if node.test.value else node.orelse,
            bindings,
        )
    return ""


def _module_string_bindings(module: ast.Module) -> dict[str, str]:
    """Resolve module constants and aliases without executing generated code."""
    bindings: dict[str, str] = {}
    assignments: list[tuple[str, ast.AST]] = []
    for node in module.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        assignments.extend(
            (target.id, value) for target in targets if isinstance(target, ast.Name)
        )
    # Multiple passes support forward-independent alias chains while remaining finite.
    for _ in range(len(assignments) + 1):
        changed = False
        for name, value in assignments:
            resolved = _statically_selected_string(value, bindings)
            if resolved and bindings.get(name) != resolved:
                bindings[name] = resolved
                changed = True
        if not changed:
            break
    return bindings


def _relationship_binding_evidence(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    module: ast.Module | None = None,
) -> dict[str, Any]:
    """Find provable predicate bindings while treating dynamic data flow as unknown."""
    binding_calls: list[ast.Call] = []
    candidate_calls: list[ast.Call] = []
    bound_iris: set[str] = set()
    callable_bindings: dict[str, set[str]] = {}
    value_bindings = _module_string_bindings(module) if module is not None else {}
    for node in ast.walk(function):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        target = (
            node.targets[0]
            if isinstance(node, ast.Assign) and len(node.targets) == 1
            else node.target
            if isinstance(node, ast.AnnAssign)
            else None
        )
        if not isinstance(target, ast.Name):
            continue
        resolved = _statically_selected_string(value, value_bindings)
        if resolved:
            value_bindings[target.id] = resolved
        if isinstance(value, ast.Subscript):
            key = _statically_selected_string(value.slice, value_bindings)
            if key.startswith(("http://", "https://")):
                callable_bindings.setdefault(target.id, set()).add(key)
    for call in (node for node in ast.walk(function) if isinstance(node, ast.Call)):
        argument_nodes = [
            *call.args,
            *(keyword.value for keyword in call.keywords),
        ]
        referenced_names = {
            node.id
            for argument in argument_nodes
            for node in ast.walk(argument)
            if isinstance(node, ast.Name)
        }
        if not {"subject_iri", "object_iri"} <= referenced_names:
            continue
        candidate_calls.append(call)
        call_bound_iris: set[str] = set()
        if isinstance(call.func, ast.Name):
            call_bound_iris.update(callable_bindings.get(call.func.id, set()))
        call_bound_iris.update(
            resolved
            for argument in argument_nodes
            if (
                resolved := _statically_selected_string(argument, value_bindings)
            ).startswith(("http://", "https://"))
        )
        literal_roots = [call.func, *argument_nodes]
        call_bound_iris.update(
            str(node.value)
            for root in literal_roots
            for node in ast.walk(root)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value.startswith(("http://", "https://"))
        )
        # A call that merely receives both IRIs (for example, input validation)
        # is not a relationship capability. Count only calls whose predicate
        # binding can be proven from the callable or its arguments.
        if call_bound_iris:
            binding_calls.append(call)
            bound_iris.update(call_bound_iris)
    return {
        "call_count": (
            len(binding_calls) if bound_iris else len(candidate_calls)
        ),
        "bound_iris": sorted(bound_iris),
        "binding_status": "proven" if bound_iris else "unknown",
    }


def _init_memory_ast_evidence(node: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, Any]:
    """Collect canonical lifecycle control-flow evidence independent of variable names."""
    path_variables: set[str] = set()
    scoped_calls: list[int] = []
    guarded_initializers: list[int] = []
    destructive_calls: list[str] = []

    for candidate in ast.walk(node):
        if isinstance(candidate, ast.Call):
            called = _call_name(candidate.func)
            if called.endswith(("reset_graph", "reset_retained_graph")):
                destructive_calls.append(called)
            if called == "rdf_runtime.scoped_memory_paths":
                scoped_calls.append(getattr(candidate, "lineno", 0))
        if not isinstance(candidate, (ast.Assign, ast.AnnAssign)):
            continue
        value = candidate.value
        if not isinstance(value, ast.Call) or _call_name(value.func) != "rdf_runtime.scoped_memory_paths":
            continue
        target = candidate.targets[0] if isinstance(candidate, ast.Assign) else candidate.target
        if isinstance(target, (ast.Tuple, ast.List)) and target.elts:
            first = target.elts[0]
            if isinstance(first, ast.Name):
                path_variables.add(first.id)

    for candidate in ast.walk(node):
        if not isinstance(candidate, ast.If):
            continue
        guarded_variable = ""
        if (
            isinstance(candidate.test, ast.Call)
            and _call_name(candidate.test.func).endswith(".is_file")
            and isinstance(candidate.test.func, ast.Attribute)
            and isinstance(candidate.test.func.value, ast.Name)
            and not candidate.test.args
        ):
            guarded_variable = candidate.test.func.value.id
        if guarded_variable not in path_variables:
            continue
        for nested in candidate.body:
            for call in (item for item in ast.walk(nested) if isinstance(item, ast.Call)):
                if _call_name(call.func) != "rdf_runtime.initialize_retained_graph":
                    continue
                source_keyword = next(
                    (keyword.value for keyword in call.keywords if keyword.arg == "source_path"),
                    None,
                )
                if (
                    isinstance(source_keyword, ast.Call)
                    and _call_name(source_keyword.func) == "str"
                    and len(source_keyword.args) == 1
                    and isinstance(source_keyword.args[0], ast.Name)
                    and source_keyword.args[0].id == guarded_variable
                ):
                    guarded_initializers.append(getattr(call, "lineno", 0))
    return {
        "path_variables": sorted(path_variables),
        "scoped_calls": scoped_calls,
        "guarded_initializers": guarded_initializers,
        "destructive_calls": destructive_calls,
    }


def _probe_artifact_tokens(graph: Graph) -> list[str]:
    """Return validator-only labels/IRIs that must never escape probe graphs."""
    markers = ("validator", "semantic identity probe", "semantic invalid om-2 probe")
    return sorted(
        {
            str(node)
            for triple in graph
            for node in triple
            if any(marker in str(node).casefold() for marker in markers)
        }
    )


def _structured_result(value: Any) -> dict[str, Any]:
    """Normalize generated tool envelopes without interpreting domain semantics."""
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return dict(decoded) if isinstance(decoded, Mapping) else {}
    return {}


def _is_structured_rejection(value: Any) -> bool:
    """Return whether a generated tool explicitly rejected an operation."""
    return str(_structured_result(value).get("status") or "").casefold() in {
        "rejected",
        "error",
    }


def _fastmcp_tools(registry: Any) -> dict[str, Any]:
    """Read the concrete FastMCP inventory through its public async API."""
    getter = getattr(registry, "get_tools", None)
    if not callable(getter):
        raise TypeError("mcp does not expose the FastMCP get_tools API")
    result = getter()
    if inspect.isawaitable(result):
        result = asyncio.run(result)
    if not isinstance(result, Mapping):
        raise TypeError("FastMCP get_tools() did not return a tool mapping")
    return {str(name): tool for name, tool in result.items()}


def _local_name(iri: Any) -> str:
    text = str(iri or "").strip()
    return text.rstrip("/#").rsplit("/", 1)[-1].rsplit("#", 1)[-1] if text else ""


def _normalized_symbol(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(text or "").lower())


def _predicate_target_stem(predicate_local: str) -> str:
    text = str(predicate_local or "").strip()
    for prefix in ("has", "is"):
        if text.startswith(prefix) and len(text) > len(prefix):
            return text[len(prefix) :]
    return text


def _read_texts(root: Path, pattern: str) -> dict[str, str]:
    if not root.exists():
        return {}
    return {
        p.name: p.read_text(encoding="utf-8", errors="replace")
        for p in sorted(root.glob(pattern))
    }


def _mutually_exclusive_property_groups(
    context: AgenticGenerationContext,
) -> list[dict[str, Any]]:
    return []


def _semantic_obligation(
    *,
    subject_key: str,
    failures: list[str] | None = None,
    warnings: list[str] | None = None,
    observed_artifacts: list[str] | None = None,
    evidence: dict[str, Any] | None = None,
    message: str | None = None,
) -> dict[str, Any]:
    """Describe one validator-owned obligation without deriving identity from prose."""
    return {
        "subject_key": subject_key,
        "failures": list(failures or []),
        "warnings": list(warnings or []),
        "observed_artifacts": list(observed_artifacts or []),
        "evidence": dict(evidence or {}),
        "message": message,
    }


def _import_generated_main_module(scripts_dir: Path, ontology_name: str):
    package_name = f"_agentic_generated_runtime_{ontology_name}_{abs(hash(str(scripts_dir.resolve())))}"
    for name in list(sys.modules):
        if name == package_name or name.startswith(package_name + "."):
            del sys.modules[name]
    package = types.ModuleType(package_name)
    package.__path__ = [str(scripts_dir.resolve())]  # type: ignore[attr-defined]
    sys.modules[package_name] = package
    module_name = f"{package_name}.main"
    spec = importlib.util.spec_from_file_location(module_name, scripts_dir / "main.py")
    if spec is None or spec.loader is None:
        raise AssertionError("Could not create import spec for generated main.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    source = (scripts_dir / "main.py").read_text(encoding="utf-8")
    exec(compile(source, str(scripts_dir / "main.py"), "exec"), module.__dict__)
    return module


def _first_ordered_leaf_class(context: AgenticGenerationContext) -> str:
    classes = context.parsed.get("classes") or {}
    ordered = [
        str(x).strip()
        for x in (
            (context.contract.get("ordered_member_profile") or {}).get(
                "ordered_member_classes"
            )
            or []
        )
        if str(x).strip()
    ]
    ordered_set = set(ordered)
    parent_classes = {
        parent
        for cls in ordered
        for parent in ((classes.get(cls) or {}).get("parent_classes") or [])
        if parent in ordered_set
    }
    for cls in ordered:
        if cls not in parent_classes:
            return cls
    return ordered[0] if ordered else ""


def _build_runtime_probe_hints(context: AgenticGenerationContext) -> dict[str, Any]:
    classes = context.parsed.get("classes") or {}
    top_local = str(
        (context.contract.get("top_entity") or {}).get("class_local") or ""
    ).strip()
    hints: dict[str, Any] = {}
    known_classes = set(classes)
    top_cls = classes.get(top_local) or {}
    ordered_classes = {
        str(x).strip()
        for x in (
            (context.contract.get("ordered_member_profile") or {}).get(
                "ordered_member_classes"
            )
            or []
        )
        if str(x).strip()
    }

    for prop, range_local in sorted((top_cls.get("object_properties") or {}).items()):
        prop = str(prop or "").strip()
        range_local = str(range_local or "").strip()
        predicate_stem = _normalized_symbol(_predicate_target_stem(prop))
        for class_local, class_spec in sorted(classes.items()):
            if class_local == top_local:
                continue
            if class_local in ordered_classes:
                continue
            ancestors = set((class_spec or {}).get("parent_classes") or [])
            if (
                class_local == range_local
                or range_local in ancestors
                or _normalized_symbol(class_local) == predicate_stem
            ):
                hints.setdefault(class_local, {"label": f"Validator {class_local}"})

    for spec in context.contract.get("required_links") or []:
        range_local = _local_name((spec or {}).get("target_class_iri"))
        if range_local and range_local in known_classes and range_local != top_local:
            hints.setdefault(range_local, {"label": f"Validator {range_local}"})

    required_step_specs = (
        context.contract.get("required_step_scoped_object_properties") or []
    )
    for spec in required_step_specs:
        range_local = str((spec or {}).get("range_local") or "").strip()
        if range_local and range_local in known_classes:
            hints.setdefault(range_local, {"label": f"Validator {range_local}"})

    ordered_class = _first_ordered_leaf_class(context)
    if ordered_class and ordered_class in known_classes:
        order_props = [
            str(x).strip()
            for x in (
                (context.contract.get("ordered_member_profile") or {}).get(
                    "single_valued_ordering_properties"
                )
                or []
            )
            if str(x).strip()
        ]
        if not order_props:
            ordered_class = ""
    if ordered_class and ordered_class in known_classes:
        payload: dict[str, Any] = {
            "label": f"Validator {ordered_class}",
            order_props[0]: 1,
        }
        for spec in required_step_specs:
            if str((spec or {}).get("domain_local") or "").strip() == ordered_class:
                predicate = str((spec or {}).get("predicate_local") or "").strip()
                range_local = str((spec or {}).get("range_local") or "").strip()
                if predicate and range_local:
                    payload[f"{predicate}_label"] = f"Validator {range_local}"
        hints[ordered_class] = [payload, dict(payload)]

    if not hints and top_local:
        hints[top_local] = {"label": "Validator Top"}
    if not hints:
        for class_local in sorted(known_classes):
            if class_local:
                hints[class_local] = {"label": f"Validator {class_local}"}
                break
    return hints


def _ordered_violation_code_report(
    result: dict[str, Any],
) -> tuple[set[str], list[str]]:
    """Read ordered-check violation codes and report response-schema errors."""
    violations = result.get("violations")
    if violations is None:
        return set(), []
    if not isinstance(violations, list):
        return set(), ["`violations` must be an array of objects"]

    codes: set[str] = set()
    schema_errors: list[str] = []
    for index, item in enumerate(violations):
        if not isinstance(item, dict):
            schema_errors.append(f"`violations[{index}]` must be an object")
            continue
        code = item.get("code")
        if isinstance(code, str) and code.strip():
            codes.add(code.strip())
            continue
        if "violation_code" in item:
            schema_errors.append(
                f"`violations[{index}]` uses unsupported discriminator "
                "`violation_code`; rename it to the required key `code`"
            )
            continue
        aliases = [alias for alias in ("type", "kind") if alias in item]
        if aliases:
            schema_errors.append(
                f"`violations[{index}]` uses unsupported discriminator "
                f"`{aliases[0]}`; rename it to the required key `code`"
            )
            continue
        schema_errors.append(
            f"`violations[{index}]` is missing required non-empty string field `code`"
        )
    return codes, schema_errors
