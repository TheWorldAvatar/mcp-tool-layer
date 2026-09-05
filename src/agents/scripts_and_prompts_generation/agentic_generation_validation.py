from __future__ import annotations

import ast
import asyncio
import importlib
import importlib.util
import inspect
import json
import hashlib
import os
import re
import sys
import tempfile
import types
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from rdflib import Graph, Literal, URIRef
from rdflib.namespace import RDF, RDFS

from src.agents.scripts_and_prompts_generation.agentic_generation_context import (
    AgenticGenerationContext,
)
from src.agents.scripts_and_prompts_generation.artifact_surface_contract import (
    _literal_all_manifest,
    derive_main_surface_contract,
)
from src.agents.scripts_and_prompts_generation.generation_contracts import (
    build_validation_observation,
    validate_generated_artifacts,
)
from src.agents.scripts_and_prompts_generation.materialization_closure import (
    compile_materialization_obligation_graph,
    materialization_closure_failures,
)
from src.agents.scripts_and_prompts_generation.extension_prompt_contract import (
    ensure_extension_kg_mode_a_handoff_file,
    extension_kg_mode_a_handoff_present,
    is_extension_kg_prompt,
)
from src.agents.scripts_and_prompts_generation.reuse_policy import (
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


def _syntax_report(
    scripts_dir: Path,
    paths: list[Path] | None = None,
) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    failures: list[str] = []
    warnings: list[str] = []
    obligations: list[dict[str, Any]] = []
    if not scripts_dir.exists():
        warnings.append(f"Scripts directory does not exist yet: {scripts_dir}")
        return failures, warnings, obligations
    python_paths = (
        sorted(path for path in paths if path.suffix == ".py")
        if paths is not None
        else sorted(scripts_dir.glob("*.py"))
    )
    for path in python_paths:
        artifact = path.name
        item_failures: list[str] = []
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            message = f"{path.name}: syntax error line {exc.lineno}: {exc.msg}"
            failures.append(message)
            item_failures.append(message)
        obligations.append(
            _semantic_obligation(
                subject_key=f"artifact:{artifact}#python-syntax",
                failures=item_failures,
                observed_artifacts=[artifact],
                evidence={"subject_kind": "artifact", "artifact": artifact},
            )
        )
    return failures, warnings, obligations


def _import_report(
    context: AgenticGenerationContext,
) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    failures: list[str] = []
    warnings: list[str] = []
    import_failures: list[str] = []
    mcp_failures: list[str] = []
    obligations: list[dict[str, Any]] = []
    scripts_dir = Path(context.scripts_dir)
    main_path = scripts_dir / "main.py"
    if not main_path.exists():
        warnings.append("main.py not present; import smoke skipped")
        return failures, warnings, obligations
    package_name = f"_agentic_generated_{context.ontology.name}_{abs(hash(str(scripts_dir.resolve())))}"
    for name in list(sys.modules):
        if name == package_name or name.startswith(package_name + "."):
            del sys.modules[name]
    package = types.ModuleType(package_name)
    package.__path__ = [str(scripts_dir.resolve())]  # type: ignore[attr-defined]
    sys.modules[package_name] = package
    module_name = f"{package_name}.main"
    try:
        spec = importlib.util.spec_from_file_location(module_name, main_path)
        if spec is None or spec.loader is None:
            message = "main.py import failed: could not create import spec"
            failures.append(message)
            import_failures.append(message)
            return failures, warnings, [
                _semantic_obligation(
                    subject_key="artifact:main.py#importable",
                    failures=import_failures,
                    observed_artifacts=["main.py"],
                    evidence={"subject_kind": "artifact", "artifact": "main.py"},
                )
            ]
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        if getattr(module, "mcp", None) is None:
            message = "main.py imports but does not expose `mcp`"
            failures.append(message)
            mcp_failures.append(message)
    except Exception as exc:
        message = f"main.py import failed: {type(exc).__name__}: {exc}"
        failures.append(message)
        import_failures.append(message)
    obligations.extend(
        [
            _semantic_obligation(
                subject_key="artifact:main.py#importable",
                failures=import_failures,
                observed_artifacts=["main.py"],
                evidence={"subject_kind": "artifact", "artifact": "main.py"},
            ),
            _semantic_obligation(
                subject_key="tool:mcp#main-export",
                failures=mcp_failures,
                observed_artifacts=["main.py"],
                evidence={"subject_kind": "tool", "tool_name": "mcp"},
            ),
        ]
    )
    return failures, warnings, obligations


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


def _runtime_graph_hygiene_report(
    context: AgenticGenerationContext,
) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    failures: list[str] = []
    warnings: list[str] = []
    obligations: list[dict[str, Any]] = []
    scripts_dir = Path(context.scripts_dir)

    def fail(subject_key: str, message: str, **evidence: Any) -> None:
        failures.append(message)
        obligations.append(
            _semantic_obligation(
                subject_key=subject_key,
                failures=[message],
                observed_artifacts=["main.py"],
                evidence=evidence,
                message=message,
            )
        )
    bypass_patterns = {
        "generic object-property mutation": (
            "add_object_property",
            "add_object_triple",
        ),
        "generic entity mutation": (
            "create_individual",
            "add_type",
        ),
    }
    bypasses: list[str] = []
    for script_path in sorted(scripts_dir.glob("*.py")):
        if script_path.name in {
            "_fixed_rdf_runtime.py",
            "_fixed_om2_runtime.py",
            "_reuse_pair_judge.py",
        }:
            continue
        try:
            source = script_path.read_text(encoding="utf-8")
        except OSError:
            continue
        try:
            tree = ast.parse(source, filename=str(script_path))
        except SyntaxError:
            tree = None
        if tree is not None:
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "add"
                ):
                    bypasses.append(
                        f"{script_path.name}: direct graph mutation at line "
                        f"{getattr(node, 'lineno', '?')}"
                    )
        for category, patterns in bypass_patterns.items():
            for pattern in patterns:
                if pattern in source:
                    bypasses.append(
                        f"{script_path.name}: {category} via `{pattern}`"
                    )
    if bypasses:
        warnings.append(
            "Generated package contains internal generic RDF mutation paths; "
            "MCP exposure validation owns the hard security boundary: "
            + "; ".join(bypasses[:12])
        )
    main_path = scripts_dir / "main.py"
    if not main_path.exists():
        warnings.append(
            "Runtime graph hygiene validation skipped because main.py is missing"
        )
        return failures, warnings, obligations
    try:
        main_tree = ast.parse(main_path.read_text(encoding="utf-8"), filename=str(main_path))
    except (OSError, SyntaxError):
        main_tree = None
    if main_tree is not None:
        runtime_tool_names = {"init_memory", "export_memory"}
        commit_gate = (
            (getattr(context, "contract", {}) or {}).get(
                "materialization_operation_units"
            )
            or {}
        )
        commit_gate_enabled = bool(commit_gate.get("merged_predicate_locals"))
        defined_runtime_tools = {
            node.name
            for node in main_tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in runtime_tool_names
        }
        forbidden_defined_runtime_tools = set(defined_runtime_tools)
        if commit_gate_enabled:
            forbidden_defined_runtime_tools.discard("export_memory")
        if forbidden_defined_runtime_tools:
            fail(
                "runtime-policy:lifecycle-tools#fixed-provenance",
                "main.py must import tested lifecycle tools from _fixed_rdf_runtime, not "
                "define them: " + ", ".join(sorted(forbidden_defined_runtime_tools)),
                subject_kind="runtime-policy",
                tool_names=sorted(forbidden_defined_runtime_tools),
            )
        for node in main_tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name not in runtime_tool_names:
                continue
            if node.args.vararg is not None or node.args.kwarg is not None:
                fail(
                    f"tool:{node.name}#fastmcp-publishable-signature",
                    f"main.py: runtime adapter `{node.name}` uses *args/**kwargs; "
                    "FastMCP tools require explicit publishable parameters",
                    subject_kind="tool",
                    tool_name=node.name,
                )
                continue
            parameter_names = [
                arg.arg
                for arg in list(node.args.posonlyargs) + list(node.args.args)
            ]
            if node.name == "init_memory" and node.args.vararg is None and node.args.kwarg is None:
                expected_names = ["doi", "top_level_entity_name"]
                if parameter_names != expected_names:
                    fail(
                        "tool:init_memory#open-or-resume-signature",
                        "main.py: init_memory must accept exactly "
                        "(doi, top_level_entity_name), with no caller-selected lifecycle mode",
                        subject_kind="tool",
                        tool_name=node.name,
                        expected_parameters=expected_names,
                        actual_parameters=parameter_names,
                    )
                lifecycle_evidence = _init_memory_ast_evidence(node)
                if lifecycle_evidence["destructive_calls"]:
                    fail(
                        "tool:init_memory#no-destructive-operation",
                        "main.py: init_memory must never reset, clear, or replace graph state",
                        subject_kind="tool",
                        tool_name=node.name,
                        calls=lifecycle_evidence["destructive_calls"],
                    )
                if not lifecycle_evidence["guarded_initializers"]:
                    fail(
                        "tool:init_memory#canonical-persistence-resume",
                        "main.py: init_memory must call fixed-runtime "
                        "initialize_retained_graph(source_path=str(<scoped memory path>)) "
                        "inside that path's is_file() guard",
                        subject_kind="tool",
                        tool_name=node.name,
                        lifecycle_evidence=lifecycle_evidence,
                    )
                if not lifecycle_evidence["path_variables"]:
                    fail(
                        "tool:init_memory#canonical-persistence-location",
                        "main.py: init_memory must unpack the first result of "
                        "rdf_runtime.scoped_memory_paths(doi, top_level_entity_name); "
                        "variable naming is unrestricted, but path normalization must not be reimplemented",
                        subject_kind="tool",
                        tool_name=node.name,
                        lifecycle_evidence=lifecycle_evidence,
                    )
            if node.name == "export_memory" and node.args.vararg is None and node.args.kwarg is None:
                source = ast.get_source_segment(
                    main_path.read_text(encoding="utf-8"), node
                ) or ""
                if commit_gate_enabled:
                    if "check_ordered_members" not in source:
                        fail(
                            "tool:export_memory#commit-gate",
                            "main.py: export_memory must call the generated integrity "
                            "check before delegating to fixed export",
                            subject_kind="tool",
                            tool_name=node.name,
                        )
                    if "export_memory" not in source.replace("def export_memory", ""):
                        fail(
                            "tool:export_memory#fixed-delegate",
                            "main.py: commit-gated export_memory must delegate successful "
                            "graphs to fixed-runtime export_memory",
                            subject_kind="tool",
                            tool_name=node.name,
                        )
                elif (
                    "export_graph_result" not in source
                    and "export_memory_wrapper" not in source
                ):
                    fail(
                        "tool:export_memory#abox-projection",
                        "main.py: export_memory must use fixed-runtime "
                        "export_graph_result so schema is excluded by default",
                        subject_kind="tool",
                        tool_name=node.name,
                    )
                if "doi" not in parameter_names or "top_level_entity_name" not in parameter_names:
                    fail(
                        "tool:export_memory#scoped-persistence",
                        "main.py: export_memory must pass DOI and scope to fixed-runtime "
                        "export_graph_result so the canonical memory and export artifacts "
                        "are persisted",
                        subject_kind="tool",
                        tool_name=node.name,
                        actual_parameters=parameter_names,
                    )
        if any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "materialize_hints"
            for node in main_tree.body
        ):
            fail(
                "tool:materialize_hints#forbidden-aggregate-tool",
                "main.py: aggregate `materialize_hints` is forbidden; the KG agent and prompt "
                "must orchestrate atomic create/add/export tools",
                subject_kind="tool",
                tool_name="materialize_hints",
            )
        if failures:
            return failures, warnings, obligations

    try:
        module = _import_generated_main_module(scripts_dir, context.ontology.name)
    except Exception as exc:
        warnings.append(
            f"Runtime graph hygiene validation skipped because generated main.py could not be imported: {type(exc).__name__}: {exc}"
        )
        return failures, warnings, obligations

    materialize = getattr(module, "materialize_hints", None)
    if materialize is not None:
        fail(
            "tool:materialize_hints#forbidden-exposure",
            "Generated package exposes forbidden aggregate `materialize_hints`; expose only "
            "atomic create/add/check tools plus init_memory and export_memory",
            subject_kind="tool",
            tool_name="materialize_hints",
        )
        return failures, warnings, obligations

    create_tools = sorted(
        (
            name,
            value,
        )
        for name, value in vars(module).items()
        if name.startswith("create_") and callable(value)
    )
    export_memory = getattr(module, "export_memory", None)
    init_memory = getattr(module, "init_memory", None)
    if create_tools and callable(export_memory) and callable(init_memory):
        runtime = getattr(module, "rdf_runtime", None)
        if runtime is None and module.__package__:
            runtime = importlib.import_module(
                f"{module.__package__}._fixed_rdf_runtime"
            )
        graph = runtime.retained_graph() if runtime is not None else None
        snapshot = str(graph.serialize(format="nt")) if isinstance(graph, Graph) else None
        try:
            init_signature = inspect.signature(init_memory)
            if len(init_signature.parameters) >= 2:
                init_memory("validator-doi", "Validator Top")
            elif len(init_signature.parameters) == 1:
                init_memory("validator-doi")
            else:
                init_memory()
            from src.agents.scripts_and_prompts_generation.creator_atomicity import (
                creator_call_recipe,
            )
            from src.agents.scripts_and_prompts_generation.pure_llm_generation import (
                _owned_entity_tool_contracts,
            )

            creator_contracts = {
                str(item.get("public_tool") or ""): item
                for item in _owned_entity_tool_contracts(context)
            }
            selected = next(
                (
                    (name, tool, creator_contracts[name])
                    for name, tool in create_tools
                    if name in creator_contracts
                ),
                None,
            )
            if selected is None:
                raise ValueError(
                    "No ontology-owned create tool has a projected creator contract"
                )
            create_name, create_tool, create_contract = selected
            call_recipe = creator_call_recipe(
                create_contract,
                create_tool,
                label="Validator shared graph probe",
                include_optional_datatypes=False,
            )
            if isinstance(graph, Graph):
                _seed_existing_operation_targets(
                    graph, create_contract, call_recipe["kwargs"]
                )
            raw_created = create_tool(
                *call_recipe["args"],
                **call_recipe["kwargs"],
            )
            created_result = _structured_result(raw_created)
            created_iri = str(created_result.get("iri") or "").strip()
            if created_result.get("status") != "ok" or not created_iri:
                raise ValueError(
                    f"{create_name} did not return a successful envelope with an IRI"
                )
            export_signature = inspect.signature(export_memory)
            export_kwargs: dict[str, Any] = {}
            if "doi" in export_signature.parameters:
                export_kwargs["doi"] = "validator-doi"
            if "top_level_entity_name" in export_signature.parameters:
                export_kwargs["top_level_entity_name"] = "Validator Top"
            if "scope" in export_signature.parameters:
                export_kwargs["scope"] = "Validator Top"
            if "top_iri" in export_signature.parameters:
                export_kwargs["top_iri"] = str(created_iri)
            missing_export_parameters = [
                parameter.name
                for parameter in export_signature.parameters.values()
                if parameter.default is inspect.Parameter.empty
                and parameter.kind
                in {
                    inspect.Parameter.POSITIONAL_ONLY,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    inspect.Parameter.KEYWORD_ONLY,
                }
                and parameter.name not in export_kwargs
            ]
            if missing_export_parameters:
                raise ValueError(
                    "export_memory has required parameters unsupported by the lifecycle "
                    f"contract probe: {missing_export_parameters}"
                )
            exported = export_memory(**export_kwargs)
            if isinstance(exported, str):
                try:
                    exported = json.loads(exported)
                except json.JSONDecodeError:
                    exported = {"ttl": exported}
            exported_ttl = (
                str(exported.get("ttl") or exported.get("turtle") or "")
                if isinstance(exported, Mapping)
                else ""
            )
            if not exported_ttl.strip() or str(created_iri) not in exported_ttl:
                fail(
                    "runtime-policy:create-export-shared-graph",
                    "Generated create/export tools do not share graph state: "
                    f"`{create_name}` created an IRI absent from exported Turtle",
                    subject_kind="runtime-policy",
                    create_tool=create_name,
                    export_tool="export_memory",
                    create_call_recipe=call_recipe,
                )
                return failures, warnings, obligations
        except Exception as exc:
            fail(
                "runtime-policy:create-export-shared-graph",
                "Generated create/export shared-graph probe failed: "
                f"{type(exc).__name__}: {exc}",
                subject_kind="runtime-policy",
            )
            return failures, warnings, obligations
        finally:
            if isinstance(graph, Graph) and snapshot is not None:
                graph.remove((None, None, None))
                if snapshot.strip():
                    graph.parse(data=snapshot, format="nt")

    # Full hardcoded A-Box orchestration is a development harness responsibility,
    # not a generated MCP tool. Package validation stops after proving that atomic
    # create and export tools share retained graph state.
    return failures, warnings, obligations

    hints = _build_runtime_probe_hints(context)
    previous_data_dir = os.environ.get("TWA_AGENTIC_DATA_DIR")
    runtime = getattr(module, "rdf_runtime", None)
    if runtime is None and module.__package__:
        runtime = importlib.import_module(f"{module.__package__}._fixed_rdf_runtime")
    runtime_graph = runtime.retained_graph() if runtime is not None else None
    snapshot = (
        str(runtime_graph.serialize(format="nt"))
        if isinstance(runtime_graph, Graph)
        else None
    )
    materialized_graph = Graph()
    try:
        with tempfile.TemporaryDirectory(prefix="agentic_runtime_hygiene_") as tmp_dir:
            os.environ["TWA_AGENTIC_DATA_DIR"] = tmp_dir
            init_memory = getattr(module, "init_memory", None)
            if not callable(init_memory) and callable(getattr(init_memory, "fn", None)):
                init_memory = init_memory.fn
            if callable(init_memory):
                init_signature = inspect.signature(init_memory)
                if len(init_signature.parameters) >= 2:
                    init_memory("validator-doi", "Validator Top")
                elif len(init_signature.parameters) == 1:
                    init_memory("validator-doi")
                else:
                    init_memory()

            materialize_signature = inspect.signature(materialize)
            parameters = list(materialize_signature.parameters.values())
            accepts_varargs = any(
                parameter.kind == inspect.Parameter.VAR_POSITIONAL
                for parameter in parameters
            )
            positional_capacity = sum(
                parameter.kind
                in {
                    inspect.Parameter.POSITIONAL_ONLY,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                }
                for parameter in parameters
            )
            if accepts_varargs or positional_capacity >= 4:
                raw_result = materialize(
                    "validator-doi",
                    "validator-top",
                    "Validator Top",
                    json.dumps(hints, ensure_ascii=False),
                )
            elif positional_capacity >= 1:
                raw_result = materialize(hints)
            else:
                raw_result = materialize()

            result: dict[str, Any] = {}
            ttl = ""
            if isinstance(raw_result, Mapping):
                result = dict(raw_result)
            elif isinstance(raw_result, str):
                try:
                    parsed_result = json.loads(raw_result)
                except json.JSONDecodeError:
                    ttl = raw_result
                else:
                    if isinstance(parsed_result, Mapping):
                        result = dict(parsed_result)
                    elif isinstance(parsed_result, str):
                        ttl = parsed_result
            if result.get("status") not in {None, "ok", "success"}:
                fail(
                    "runtime-policy:materialize-status-ok",
                    "Generated materialize_hints failed runtime graph hygiene validation: "
                    + str(result.get("message") or result),
                    subject_kind="runtime-policy",
                )
                return failures, warnings, obligations

            ttl = str(result.get("ttl") or ttl)
            materialized_top_iri = str(result.get("top_iri") or "").strip()
            if not materialized_top_iri:
                fail(
                    "runtime-policy:materialize-top-iri",
                    "Generated materialize_hints did not return the materialized top_iri",
                    subject_kind="runtime-policy",
                )
                return failures, warnings, obligations
            if not ttl.strip():
                export_memory = getattr(module, "export_memory", None)
                if not callable(export_memory) and callable(
                    getattr(export_memory, "fn", None)
                ):
                    export_memory = export_memory.fn
                if callable(export_memory):
                    export_signature = inspect.signature(export_memory)
                    required_export_parameters = [
                        parameter
                        for parameter in export_signature.parameters.values()
                        if parameter.default is inspect.Parameter.empty
                        and parameter.kind
                        in {
                            inspect.Parameter.POSITIONAL_ONLY,
                            inspect.Parameter.POSITIONAL_OR_KEYWORD,
                        }
                    ]
                    if not required_export_parameters:
                        exported = export_memory()
                        if isinstance(exported, Mapping):
                            ttl = str(exported.get("ttl") or "")
                        else:
                            ttl = str(exported or "")
            if not ttl.strip():
                fail(
                    "runtime-policy:nonempty-ttl",
                    "Generated package exposed no Turtle through materialize_hints or export_memory",
                    subject_kind="runtime-policy",
                )
                return failures, warnings, obligations
            materialized_graph.parse(data=ttl, format="turtle")
            if len(materialized_graph) == 0:
                fail(
                    "runtime-policy:materialize-abox-nonempty",
                    "Generated materialize_hints returned Turtle with no A-Box triples",
                    subject_kind="runtime-policy",
                )
                return failures, warnings, obligations
            if not any(
                materialized_graph.triples(
                    (URIRef(materialized_top_iri), RDF.type, None)
                )
            ):
                fail(
                    "runtime-policy:materialize-top-typing",
                    "Generated materialize_hints returned a top_iri without an A-Box rdf:type",
                    subject_kind="runtime-policy",
                    top_iri=materialized_top_iri,
                )
                return failures, warnings, obligations
    except Exception as exc:
        fail(
            "runtime-policy:materialization-probe",
            f"Generated runtime graph hygiene validation failed: {type(exc).__name__}: {exc}",
            subject_kind="runtime-policy",
        )
        return failures, warnings, obligations
    finally:
        if isinstance(runtime_graph, Graph) and snapshot is not None:
            runtime_graph.remove((None, None, None))
            if snapshot.strip():
                runtime_graph.parse(data=snapshot, format="nt")
        if previous_data_dir is None:
            os.environ.pop("TWA_AGENTIC_DATA_DIR", None)
        else:
            os.environ["TWA_AGENTIC_DATA_DIR"] = previous_data_dir

    class_iris = {
        str((spec or {}).get("iri") or "").strip(): local
        for local, spec in (context.parsed.get("classes") or {}).items()
        if str((spec or {}).get("iri") or "").strip()
    }
    typed_nodes: dict[URIRef, set[str]] = {}
    label_groups: dict[tuple[str, str], set[URIRef]] = {}
    for subject, _, class_iri in materialized_graph.triples((None, RDF.type, None)):
        if not isinstance(subject, URIRef):
            continue
        class_local = class_iris.get(str(class_iri))
        if not class_local:
            continue
        typed_nodes.setdefault(subject, set()).add(class_local)
        for label in materialized_graph.objects(subject, RDFS.label):
            label_text = str(label or "").strip()
            if label_text:
                label_groups.setdefault((class_local, label_text), set()).add(subject)

    duplicate_labels = [
        f"{class_local}:{label}"
        for (class_local, label), nodes in sorted(label_groups.items())
        if len(nodes) > 1
    ]
    if duplicate_labels:
        fail(
            "runtime-policy:unique-same-class-label",
            "Generated runtime graph contains duplicate same-class labels after materialize_hints: "
            + ", ".join(duplicate_labels[:8]),
            subject_kind="runtime-policy",
        )

    top_iri = str(result.get("top_iri") or "").strip()
    if not top_iri:
        top_class_iri = str(
            (context.contract.get("top_entity") or {}).get("class_iri") or ""
        ).strip()
        if top_class_iri:
            top_iri = str(
                next(
                    materialized_graph.subjects(RDF.type, URIRef(top_class_iri)),
                    "",
                )
                or ""
            )
    reachable: set[URIRef] = set()
    if top_iri:
        frontier = [URIRef(top_iri)]
        while frontier:
            current = frontier.pop()
            if current in reachable:
                continue
            reachable.add(current)
            for _, predicate, obj in materialized_graph.triples(
                (current, None, None)
            ):
                if predicate in {RDF.type, RDFS.label}:
                    continue
                if isinstance(obj, URIRef) and obj not in reachable:
                    frontier.append(obj)
    else:
        warnings.append(
            "Runtime graph hygiene validation could not inspect reachability because top_iri is missing"
        )

    top_contract = context.contract.get("top_entity") or {}
    top_role_known = (
        str(top_contract.get("status") or "").casefold() not in {"", "unknown"}
        and bool(str(top_contract.get("class_iri") or "").strip())
    )
    if reachable and top_role_known:
        unreachable = [
            f"{sorted(classes)[0]}:{node}"
            for node, classes in sorted(
                typed_nodes.items(), key=lambda item: str(item[0])
            )
            if node not in reachable
        ]
        if unreachable:
            fail(
                "runtime-policy:reachable-from-top",
                "Generated runtime graph contains typed nodes unreachable from the materialized top entity: "
                + ", ".join(unreachable[:8]),
                subject_kind="runtime-policy",
            )
    elif reachable and not top_role_known:
        warnings.append(
            "Runtime reachability hard gate skipped because the active T-Box does not "
            "declare an authoritative top entity role"
        )

    om2_namespace = "http://www.ontology-of-units-of-measure.org/resource/om-2/"
    ontology_namespace = str(
        (context.parsed.get("ontology") or {}).get("namespace") or ""
    )
    non_om2_required_predicates = {
        str((item or {}).get("predicate_iri") or "").strip()
        for item in (context.contract.get("required_links") or [])
        if not str((item or {}).get("target_class_iri") or "").startswith(om2_namespace)
    }
    ontology_class_ranges = {
        str(predicate).strip()
        for class_spec in (context.parsed.get("classes") or {}).values()
        for predicate, range_local in (
            ((class_spec or {}).get("object_properties") or {}).items()
        )
        if str(range_local or "").strip()
        and not str(range_local or "").startswith(om2_namespace)
    }
    for spec in context.contract.get("om2_quantity_properties") or []:
        predicate_iri = URIRef(str((spec or {}).get("predicate_iri") or "").strip())
        range_iri = URIRef(str((spec or {}).get("range_iris") or "").strip())
        if not str(predicate_iri) or not str(range_iri):
            continue
        if (
            str(predicate_iri) in non_om2_required_predicates
            or str((spec or {}).get("predicate_local") or "").strip()
            in ontology_class_ranges
        ):
            warnings.append(
                f"OM-2 runtime probe skipped ambiguous predicate {predicate_iri}"
            )
            continue
        links = list(graph.triples((None, predicate_iri, None)))
        if not links:
            predicate_local = str((spec or {}).get("predicate_local") or predicate_iri)
            fail(
                f"property:{predicate_local}#om2-link",
                "Generated materializer did not emit OM-2 link for "
                + predicate_local,
                subject_kind="property",
                property_local=predicate_local,
            )
            continue
        conforming_quantities = [
            quantity
            for _, _, quantity in links
            if isinstance(quantity, URIRef)
            and (quantity, RDF.type, range_iri) in graph
        ]
        if not conforming_quantities:
            predicate_local = str((spec or {}).get("predicate_local") or predicate_iri)
            fail(
                f"property:{predicate_local}#om2-range-type",
                f"OM-2 predicate {predicate_iri} has no target of expected type {range_iri}",
                subject_kind="property",
                property_local=predicate_local,
            )
            continue
        for quantity in conforming_quantities:
            values = list(graph.objects(quantity, URIRef(om2_namespace + "hasNumericalValue")))
            units = list(graph.objects(quantity, URIRef(om2_namespace + "hasUnit")))
            if len(values) != 1:
                predicate_local = str((spec or {}).get("predicate_local") or predicate_iri)
                fail(
                    f"property:{predicate_local}#om2-single-numerical-value",
                    f"OM-2 quantity {quantity} must have exactly one numerical value",
                    subject_kind="property",
                    property_local=predicate_local,
                )
            if len(units) != 1 or not isinstance(units[0], URIRef):
                predicate_local = str((spec or {}).get("predicate_local") or predicate_iri)
                fail(
                    f"property:{predicate_local}#om2-single-iri-unit",
                    f"OM-2 quantity {quantity} must have exactly one IRI-valued unit",
                    subject_kind="property",
                    property_local=predicate_local,
                )
            if ontology_namespace and str(range_iri).startswith(ontology_namespace):
                predicate_local = str((spec or {}).get("predicate_local") or predicate_iri)
                fail(
                    f"property:{predicate_local}#external-range-namespace",
                    f"OM-2 quantity range must not use ontology namespace: {range_iri}",
                    subject_kind="property",
                    property_local=predicate_local,
                )
    return failures, warnings, obligations


def _expected_tool_surface_report(
    context: AgenticGenerationContext,
) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    failures: list[str] = []
    warnings: list[str] = []
    obligations: list[dict[str, Any]] = []
    scripts_dir = Path(context.scripts_dir)

    def fail(subject_key: str, message: str, **evidence: Any) -> None:
        failures.append(message)
        obligations.append(
            _semantic_obligation(
                subject_key=subject_key,
                failures=[message],
                observed_artifacts=[str(scripts_dir)],
                evidence=evidence,
                message=message,
            )
        )

    script_text = "\n".join(_read_texts(scripts_dir, "*.py").values())
    if not script_text.strip():
        warnings.append("No generated scripts found for tool-surface validation")
        return failures, warnings, obligations
    forbidden_extension_imports = (
        "src.ontomops_extension",
        "src.ontospecies_extension",
    )
    if context.ontology.role == "extension":
        used = [name for name in forbidden_extension_imports if name in script_text]
        if used:
            fail(
                "policy:extension-server-independence",
                "Generated extension MCP scripts must be T-Box-derived and must not wrap handwritten extension servers: "
                + ", ".join(used),
                subject_kind="policy",
            )

    main_path = scripts_dir / "main.py"
    if not main_path.is_file():
        warnings.append("main.py not present; exact MCP surface validation skipped")
        return failures, warnings, obligations
    try:
        main_tree = ast.parse(main_path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError) as exc:
        fail(
            "artifact:main.py#stdio-entry-point",
            f"main.py could not be parsed for stdio entry-point validation: "
            f"{type(exc).__name__}: {exc}",
            subject_kind="artifact",
            artifact="main.py",
        )
    else:
        has_stdio_entry_point = any(
            isinstance(node, ast.If)
            and isinstance(node.test, ast.Compare)
            and isinstance(node.test.left, ast.Name)
            and node.test.left.id == "__name__"
            and len(node.test.ops) == 1
            and isinstance(node.test.ops[0], ast.Eq)
            and len(node.test.comparators) == 1
            and isinstance(node.test.comparators[0], ast.Constant)
            and node.test.comparators[0].value == "__main__"
            and any(
                isinstance(child, ast.Expr)
                and isinstance(child.value, ast.Call)
                and isinstance(child.value.func, ast.Attribute)
                and isinstance(child.value.func.value, ast.Name)
                and child.value.func.value.id == "mcp"
                and child.value.func.attr == "run"
                for child in node.body
            )
            for node in main_tree.body
        )
        if not has_stdio_entry_point:
            fail(
                "artifact:main.py#stdio-entry-point",
                "main.py must keep stdio service alive with "
                "`if __name__ == '__main__': mcp.run()`",
                subject_kind="artifact",
                artifact="main.py",
            )
    try:
        surface_contract = derive_main_surface_contract(scripts_dir)
    except Exception as exc:
        fail(
            "artifact:main.py#mcp-surface-contract",
            "Could not derive the MCP surface from generated sibling manifests: "
            f"{type(exc).__name__}: {exc}",
            subject_kind="artifact",
            artifact="main.py",
        )
        return failures, warnings, obligations
    expected_tools = set(surface_contract["expected_mcp_tools"])
    probe_inventories: list[set[str]] = []
    probe_tools: list[dict[str, Any]] = []
    for probe_index in range(3):
        try:
            module = _import_generated_main_module(scripts_dir, context.ontology.name)
            registry = getattr(module, "mcp", None)
            inventory = _fastmcp_tools(registry)
            exposed = {str(name) for name in inventory}
            probe_inventories.append(exposed)
            probe_tools.append(dict(inventory))
        except Exception as exc:
            fail(
                f"artifact:main.py#mcp-surface-probe-{probe_index + 1}",
                "main.py MCP tool-surface probe failed on independent startup "
                f"{probe_index + 1}/3: {type(exc).__name__}: {exc}",
                subject_kind="artifact",
                artifact="main.py",
                probe_index=probe_index + 1,
            )
    if probe_inventories and any(
        inventory != probe_inventories[0] for inventory in probe_inventories[1:]
    ):
        fail(
            "artifact:main.py#mcp-surface-stability",
            "main.py exposes an unstable MCP tool surface across three independent starts",
            subject_kind="artifact",
            artifact="main.py",
            probe_inventories=[sorted(value) for value in probe_inventories],
        )
    for probe_index, actual_tools in enumerate(probe_inventories, start=1):
        missing = sorted(expected_tools - actual_tools)
        extra = sorted(actual_tools - expected_tools)
        if missing or extra:
            fail(
                f"artifact:main.py#mcp-surface-equality-{probe_index}",
                "main.py MCP tool registry differs from the runtime-derived closed "
                f"surface on startup {probe_index}/3: missing={missing}, extra={extra}",
                subject_kind="artifact",
                artifact="main.py",
                probe_index=probe_index,
                expected_tools=sorted(expected_tools),
                actual_tools=sorted(actual_tools),
                missing_tools=missing,
                extra_tools=extra,
            )

    allowed_modules = {
        Path(owner).stem for owner in surface_contract["tool_owners"].values()
    }
    lifecycle_tools = set(surface_contract["lifecycle_tools"])
    fixed_runtime_tools = {
        *lifecycle_tools,
        *(
            {"create_om2_quantity"}
            if "create_om2_quantity" in surface_contract["expected_mcp_tools"]
            else set()
        ),
    }
    if probe_tools:
        for tool_name, tool in probe_tools[0].items():
            handler = getattr(tool, "fn", None)
            module_name = str(getattr(handler, "__module__", ""))
            owner_module = module_name.rsplit(".", 1)[-1]
            commit_gated_export = (
                tool_name == "export_memory"
                and bool(
                    (
                        context.contract.get("materialization_operation_units")
                        or {}
                    ).get("merged_predicate_locals")
                )
            )
            if commit_gated_export:
                provenance_ok = handler is not None and owner_module == "main"
            elif tool_name in fixed_runtime_tools:
                provenance_ok = handler is not None and owner_module == "_fixed_rdf_runtime"
            else:
                provenance_ok = (
                    handler is not None
                    and owner_module in allowed_modules
                    and Path(surface_contract["tool_owners"].get(tool_name, "")).stem
                    == owner_module
                )
            if not provenance_ok:
                fail(
                    f"tool:{tool_name}#handler-provenance",
                    f"MCP tool {tool_name!r} has disallowed handler provenance "
                    f"{module_name!r}",
                    subject_kind="tool",
                    tool_name=tool_name,
                    handler_module=module_name,
                )
            parameters = getattr(tool, "parameters", None)
            properties = (
                parameters.get("properties")
                if isinstance(parameters, Mapping)
                else None
            )
            if not isinstance(properties, Mapping):
                fail(
                    f"tool:{tool_name}#parameter-schema",
                    f"MCP tool {tool_name!r} does not expose an inspectable object schema",
                    subject_kind="tool",
                    tool_name=tool_name,
                )
                continue
            forbidden_parameters = sorted(
                str(name)
                for name in properties
                if re.search(
                    r"(?:^|_)(?:predicate|triple|graph|file|path|turtle)(?:_|$)",
                    str(name),
                    flags=re.IGNORECASE,
                )
                or (
                    re.search(
                        r"(?:^|_)class(?:_|$)",
                        str(name),
                        flags=re.IGNORECASE,
                    )
                    and str(name) != "quantity_class_iri"
                )
            )
            if forbidden_parameters:
                fail(
                    f"tool:{tool_name}#parameter-schema",
                    f"MCP tool {tool_name!r} exposes caller-selected generic parameters: "
                    f"{forbidden_parameters}",
                    subject_kind="tool",
                    tool_name=tool_name,
                    forbidden_parameters=forbidden_parameters,
                )

    for tool_name in sorted(expected_tools):
        if probe_inventories and tool_name not in probe_inventories[0]:
            fail(
                f"tool:{tool_name}#declared",
                f"Missing runtime-derived MCP tool: {tool_name}",
                subject_kind="tool",
                tool_name=tool_name,
            )

    return failures, warnings, obligations


def _ordered_member_contract_report(
    context: AgenticGenerationContext,
) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    """Defer ordering semantics to runtime graph probes, not source-code shape."""
    profile = context.contract.get("ordered_member_profile") or {}
    if not (
        profile.get("ordered_member_classes")
        and profile.get("single_valued_ordering_properties")
    ):
        return [], [], []
    return [], [
        "Ordered-member implementation shape is not statically constrained; "
        "runtime graph validation owns datatype, positivity, uniqueness, and parent typing."
    ], []


def _ttl_export_report(
    context: AgenticGenerationContext,
) -> tuple[list[str], list[str]]:
    failures: list[str] = []
    warnings: list[str] = []
    ttl_files = sorted(
        Path(context.output_root).glob(f"**/{context.ontology.name}*.ttl")
    )
    if not ttl_files:
        warnings.append("No generated TTL exports found; TTL parse validation skipped")
        return failures, warnings
    for ttl_file in ttl_files:
        try:
            Graph().parse(ttl_file, format="turtle")
        except Exception as exc:
            failures.append(f"{ttl_file}: Turtle parse failed: {exc}")
    return failures, warnings


def _prompt_quality_report(
    context: AgenticGenerationContext,
    prompt_paths: list[Path] | None = None,
) -> tuple[list[str], list[str]]:
    """Validate generic prompt usability without prescribing wording or layout."""
    failures: list[str] = []
    warnings: list[str] = []
    prompt_texts = (
        {
            path.name: path.read_text(encoding="utf-8", errors="replace")
            for path in prompt_paths or []
            if path.is_file()
        }
        if prompt_paths is not None
        else _read_texts(Path(context.prompts_dir), "*.md")
    )
    if not prompt_texts:
        warnings.append("No prompt files found; prompt quality validation skipped")
        return failures, warnings
    placeholder_re = re.compile(r"TODO|FIXME|\{\{[^}\n]+\}\}", re.IGNORECASE)
    for name, text in prompt_texts.items():
        if not text.strip():
            failures.append(f"{name}: prompt artifact is empty")
            continue
        matches = sorted(set(m.group(0) for m in placeholder_re.finditer(text)))
        if matches:
            failures.append(
                f"{name}: unresolved prompt placeholder/residue: {', '.join(matches[:8])}"
            )
    return failures, warnings


def _prompt_tbox_fidelity_report(
    context: AgenticGenerationContext,
    prompt_paths: list[Path] | None = None,
) -> tuple[list[str], list[str]]:
    """Reserve T-Box fidelity decisions for the single-artifact LLM reviewer."""
    return [], []


def validate_prompt_runtime_bindings(
    path: Path,
    context: AgenticGenerationContext | None = None,
) -> dict[str, Any]:
    """Validate that a generated prompt exposes its runtime data channel."""
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    name = path.name
    required_groups: list[tuple[str, ...]] = []
    allowed_slots: set[str]
    forbidden_slots: set[str] = set()
    is_extension = bool(
        context is not None
        and getattr(getattr(context, "ontology", None), "role", "") == "extension"
    )
    if is_extension and name.startswith("KG_BUILDING_ITER_"):
        required_groups.extend(
            [
                ("{doi}",),
                ("{entity_label}",),
                ("{entity_uri}",),
                ("{enrichment_targets}",),
                ("{main_ontology_a_box}",),
                ("{paper_content}",),
            ]
        )
        allowed_slots = {
            "doi",
            "entity_label",
            "entity_uri",
            "enrichment_targets",
            "main_ontology_a_box",
            "paper_content",
        }
        forbidden_slots = {
            "iteration_hints",
            "top_entities",
            "hints",
            "ontosynthesis_a_box",
        }
    elif is_extension and name.startswith("EXTRACTION_"):
        required_groups.extend(
            [
                ("{entity_label}",),
                ("{entity_uri}",),
            ]
        )
        allowed_slots = {"entity_label", "entity_uri"}
        forbidden_slots = {
            "paper_content",
            "iteration_hints",
            "top_entities",
            "hints",
            "main_ontology_a_box",
            "ontosynthesis_a_box",
        }
    elif name == "KG_BUILDING_ITER_1.md":
        required_groups.extend(
            [
                ("{paper_content}",),
                ("{doi}", "{hash}"),
                ("{top_entities}", "{hints}"),
            ]
        )
        allowed_slots = {
            "paper_content",
            "doi",
            "hash",
            "top_entities",
            "hints",
        }
    elif name.startswith("KG_BUILDING_"):
        required_groups.extend(
            [
                ("{iteration_hints}",),
                ("{doi}",),
                ("{entity_label}",),
                ("{entity_uri}",),
            ]
        )
        # The KG 2+ pipeline replaces exactly these four slots. In particular,
        # source text must not re-enter a KG prompt through a legacy alias.
        allowed_slots = {"iteration_hints", "doi", "entity_label", "entity_uri"}
        forbidden_slots = {
            "paper_content",
            "context",
            "hash",
            "hints",
            "iteration_input",
            "top_entities",
        }
    else:
        required_groups.append(("{paper_content}",))
        allowed_slots = {
            "paper_content",
            "context",
            "entity_label",
            "entity_uri",
            "doi",
            "hash",
            "top_entities",
            "hints",
            "iteration_input",
            "iteration_hints",
            "accumulated_hints",
            "entity_identity_dossier",
        }
    if name.startswith("EXTRACTION_") and name != "EXTRACTION_ITER_1.md":
        required_groups.extend(
            [
                ("{entity_label}",),
                ("{entity_uri}",),
            ]
        )
    slot_matches = re.findall(
        r"(?<!\{)\{([A-Za-z_][A-Za-z0-9_]*)\}(?!\})",
        text,
    )
    slot_counts = {
        slot: slot_matches.count(slot) for slot in sorted(set(slot_matches))
    }
    observed_slots = set(slot_counts)
    failures: list[str] = []
    missing_groups = [
        group for group in required_groups if not any(slot in text for slot in group)
    ]
    for group in missing_groups:
        failures.append(
            f"{name}: missing runtime binding; accepted slot group: "
            + " | ".join(group)
        )
    unknown_slots = sorted(observed_slots - allowed_slots)
    if unknown_slots:
        failures.append(
            f"{name}: unknown runtime binding slot(s): "
            + ", ".join(f"{{{slot}}}" for slot in unknown_slots)
        )
    observed_forbidden_slots = sorted(observed_slots & forbidden_slots)
    if observed_forbidden_slots:
        failures.append(
            f"{name}: forbidden runtime binding slot(s) for this runtime contract: "
            + ", ".join(f"{{{slot}}}" for slot in observed_forbidden_slots)
        )
    duplicated_slots = {
        slot: count for slot, count in slot_counts.items() if count > 1
    }
    if duplicated_slots:
        failures.append(
            f"{name}: each runtime binding must appear exactly once; duplicates: "
            + ", ".join(
                f"{{{slot}}}={count}"
                for slot, count in sorted(duplicated_slots.items())
            )
        )
    onepass_control_plane_hits: list[str] = []
    if re.fullmatch(r"KG_BUILDING_ITER_\d+_ONEPASS\.md", name):
        forbidden_patterns = (
            r"\binit_memory\b",
            r"\bexport_memory\b",
            r"\bthis iteration only\b",
            r"\bonly (?:in|for) this iteration\b",
            r"\bdefer\b.{0,80}\b(?:later|next) iteration\b",
            r"\bignore\b.{0,80}\bordered[- ]member hints?\b",
            r"\buse only (?:the )?tools (?:listed|above|in this fragment)\b",
            r"\bdo not (?:introduce|use|create|materialize)\b.{0,80}"
            r"\b(?:classes|properties|tools|creators)\b.{0,80}\b(?:beyond|outside)\b",
            r"\bfinish by\b",
            r"\bdo not claim success\b",
            r"\bfinal response\b",
            r"\bfailed run\b",
        )
        onepass_control_plane_hits = sorted(
            {
                match.group(0)
                for pattern in forbidden_patterns
                for match in re.finditer(pattern, text, re.IGNORECASE | re.DOTALL)
            }
        )
        if onepass_control_plane_hits:
            failures.append(
                f"{name}: one-pass fragment contains per-iteration control-plane "
                "instructions: "
                + ", ".join(onepass_control_plane_hits[:8])
            )
    return {
        "ok": not failures,
        "failures": failures,
        "observed_artifacts": [str(path)],
        "evidence": {
            "required_slot_groups": [list(group) for group in required_groups],
            "missing_slot_groups": [list(group) for group in missing_groups],
            "unknown_slots": unknown_slots,
            "forbidden_slots": observed_forbidden_slots,
            "slot_counts": slot_counts,
            "duplicated_slots": duplicated_slots,
            "onepass_control_plane_hits": onepass_control_plane_hits,
        },
    }


def _prompt_runtime_binding_report(
    context: AgenticGenerationContext,
    prompt_paths: list[Path] | None = None,
) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    failures: list[str] = []
    obligations: list[dict[str, Any]] = []
    paths = (
        sorted(prompt_paths)
        if prompt_paths is not None
        else sorted(Path(context.prompts_dir).glob("*.md"))
    )
    for path in paths:
        result = validate_prompt_runtime_bindings(path, context)
        item_failures = list(result["failures"])
        failures.extend(item_failures)
        obligations.append(
            {
                "subject_key": path.name,
                "failures": item_failures,
                "observed_artifacts": [str(path)],
                "evidence": result["evidence"],
            }
        )
    return failures, [], obligations


def _prompt_tool_signature_alignment_report(
    context: AgenticGenerationContext,
    prompt_paths: list[Path] | None = None,
) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    """Require KG prompts to expose the creators' validated runtime signatures."""
    from src.agents.scripts_and_prompts_generation.pure_llm_generation import (
        _generated_runtime_signature,
        _prompt_artifact_generation_contract,
    )
    from src.pipelines.utils.kg_full_hints_onepass import extract_mcp_tool_lines

    failures: list[str] = []
    obligations: list[dict[str, Any]] = []
    generated_main = Path(context.scripts_dir) / "main.py"
    if not generated_main.is_file() or not generated_main.read_text(
        encoding="utf-8", errors="replace"
    ).strip():
        return [], [], []
    try:
        generated_module = _import_generated_main_module(
            Path(context.scripts_dir), context.ontology.name
        )
    except Exception as exc:
        failure = (
            "KG prompt/runtime signature alignment requires an importable generated "
            f"main.py: {type(exc).__name__}: {exc}"
        )
        return [failure], [], [
            {
                "subject_key": context.ontology.name,
                "failures": [failure],
                "observed_artifacts": [str(generated_main)],
                "evidence": {},
            }
        ]
    paths = (
        sorted(prompt_paths)
        if prompt_paths is not None
        else sorted(Path(context.prompts_dir).glob("KG_BUILDING_ITER_*.md"))
    )
    for path in paths:
        if not path.name.startswith("KG_BUILDING_ITER_"):
            continue
        item_failures: list[str] = []
        expected: list[dict[str, str]] = []
        try:
            contract = _prompt_artifact_generation_contract(context, path)
        except Exception as exc:
            item_failures.append(
                f"{path.name}: could not project the generated runtime tool signatures: "
                f"{type(exc).__name__}: {exc}"
            )
            contract = {}
        prompt_lines = [
            " ".join(line.split())
            for line in path.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines()
        ]
        agent_tool_contract = contract.get("agent_tool_contract") or {}
        tool_groups = (
            "lifecycle_tools",
            "creator_tools",
            "relationship_tools",
            "check_tools",
            "fixed_creator_tools",
        )
        expected_tools = [
            tool
            for group in tool_groups
            for tool in (agent_tool_contract.get(group) or [])
        ]
        extracted_lines = extract_mcp_tool_lines(
            path.read_text(encoding="utf-8", errors="replace")
        )
        extracted_by_name = {
            line.split("(", 1)[0].strip(): " ".join(line.split())
            for line in extracted_lines
        }
        for tool in expected_tools:
            name = str(tool.get("name") or "").strip()
            signature = " ".join(
                str(tool.get("exact_call_signature") or "").split()
            )
            if not name or not signature:
                continue
            observed = extracted_by_name.get(name, "")
            if not observed:
                item_failures.append(
                    f"{path.name}: missing one-pass parseable signature line "
                    f"`  - {signature}`"
                )
            elif observed != signature:
                item_failures.append(
                    f"{path.name}: one-pass signature for {name} is `{observed}`, "
                    f"expected `{signature}`"
                )

        for tool in agent_tool_contract.get("creator_tools") or []:
            name = str(tool.get("name") or "").strip()
            if not name:
                continue
            try:
                inspected = _generated_runtime_signature(generated_module, name)
            except RuntimeError as exc:
                item_failures.append(f"{path.name}: {exc}")
                continue
            runtime_signature = f"{name}{inspected}"
            parameter_names = [
                parameter.name
                for parameter in inspected.parameters.values()
                if parameter.kind
                not in {
                    inspect.Parameter.VAR_POSITIONAL,
                    inspect.Parameter.VAR_KEYWORD,
                }
            ]
            declaration_lines = [
                line
                for line in prompt_lines
                if name in line and ("accepts only" in line or f"{name}(" in line)
            ]
            complete_declaration = next(
                (
                    line
                    for line in declaration_lines
                    if all(
                        re.search(rf"\b{re.escape(parameter)}\b", line)
                        for parameter in parameter_names
                    )
                ),
                "",
            )
            if not complete_declaration:
                item_failures.append(
                    f"{path.name}: creator {name} has no executable declaration containing "
                    "all validated runtime parameter names "
                    f"{parameter_names}; prompt/runtime keyword drift is unsafe"
                )
            semantic_bindings = tool.get("semantic_parameter_bindings") or {}
            for semantic_key, parameter_name in semantic_bindings.items():
                if (
                    semantic_key != parameter_name
                    and any(
                        re.search(rf"\b{re.escape(str(semantic_key))}\b", line)
                        for line in declaration_lines
                    )
                ):
                    item_failures.append(
                        f"{path.name}: creator {name} exposes semantic property "
                        f"{semantic_key!r} as a keyword, but the validated runtime "
                        f"parameter is {parameter_name!r}"
                    )
            expected.append(
                {"name": name, "exact_call_signature": runtime_signature}
            )
        failures.extend(item_failures)
        obligations.append(
            {
                "subject_key": path.name,
                "failures": item_failures,
                "observed_artifacts": [str(path), str(context.scripts_dir)],
                "evidence": {"expected_creator_signatures": expected},
            }
        )
    return failures, [], obligations


def _extension_kg_mode_a_handoff_report(
    context: AgenticGenerationContext,
    prompt_paths: list[Path] | None = None,
) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    """Hard-require the mechanical Mode A block on extension KG prompts."""
    if getattr(getattr(context, "ontology", None), "role", "") != "extension":
        return [], [], []
    failures: list[str] = []
    obligations: list[dict[str, Any]] = []
    paths = (
        sorted(prompt_paths)
        if prompt_paths is not None
        else sorted(Path(context.prompts_dir).glob("KG_BUILDING_ITER_*.md"))
    )
    for path in paths:
        if not is_extension_kg_prompt(path):
            continue
        ensure_extension_kg_mode_a_handoff_file(path)
        text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
        item_failures: list[str] = []
        if not extension_kg_mode_a_handoff_present(text):
            item_failures.append(
                f"{path.name}: missing mechanical Mode A handoff block; "
                "extension KG must consume ref-entity-relations.v1 from the "
                "declared source-content slot"
            )
        failures.extend(item_failures)
        obligations.append(
            {
                "subject_key": path.name,
                "failures": item_failures,
                "observed_artifacts": [str(path)],
                "evidence": {
                    "mode_a_present": extension_kg_mode_a_handoff_present(text)
                },
            }
        )
    return failures, [], obligations


def _foreign_symbol_report(
    context: AgenticGenerationContext,
    foreign_contracts: list[dict[str, Any]] | None,
) -> tuple[list[str], list[str]]:
    failures: list[str] = []
    warnings: list[str] = []
    if not foreign_contracts:
        return failures, warnings
    allowed = {str(x) for x in context.contract.get("ontology_symbol_locals") or []}
    common_tokens = {
        "label",
        "name",
        "type",
        "class",
        "property",
        "comment",
        "range",
        "domain",
        "literal",
        "ontology",
        "document",
    }
    foreign_symbols: set[str] = set()
    for bundle in foreign_contracts:
        foreign_symbols.update(
            str(x) for x in bundle.get("ontology_symbol_locals") or []
        )
    foreign_symbols = {
        s
        for s in foreign_symbols
        if len(s) >= 4
        and s not in allowed
        and s.lower() not in common_tokens
        and not s.islower()
    }
    # Prompt prose is reviewed semantically from structured contract evidence.
    # Symbol regex checks remain limited to generated Python source.
    texts = {
        name: text
        for name, text in _read_texts(Path(context.scripts_dir), "*.py").items()
        if name
        not in {
            "_fixed_rdf_runtime.py",
            "_fixed_om2_runtime.py",
            "_reuse_pair_judge.py",
        }
    }
    offenders: list[str] = []
    for name, text in texts.items():
        hits = sorted(
            symbol
            for symbol in foreign_symbols
            if re.search(rf"(?<![A-Za-z0-9_]){re.escape(symbol)}(?![A-Za-z0-9_])", text)
        )
        if hits:
            offenders.append(f"{name}: {', '.join(hits[:8])}")
    if offenders:
        failures.append("Foreign ontology symbols found: " + "; ".join(offenders[:8]))
    return failures, warnings


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


def _stage_artifact_contract_report(
    context: AgenticGenerationContext,
    active_artifacts: list[str],
) -> tuple[list[str], list[str], list[str]]:
    """Validate obligations owned by the most recently generated artifact."""
    if not active_artifacts:
        return ["Stage validation requires at least one active artifact"], [], []
    relative = Path(active_artifacts[-1]).as_posix()
    root = Path(
        getattr(
            context,
            "output_root",
            Path(context.scripts_dir).resolve().parents[1],
        )
    )
    path = root / relative
    failures: list[str] = []
    warnings: list[str] = []
    if not path.is_file():
        return [f"Active stage artifact is missing: {relative}"], warnings, [relative]
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.strip():
        failures.append(f"{path.name}: generated artifact is empty")
        return failures, warnings, [relative]

    name = path.name
    if path.suffix == ".py":
        package_name = (
            f"_agentic_stage_{context.ontology.name}_"
            f"{abs(hash(str(path.parent.resolve())))}"
        )
        for module_name in list(sys.modules):
            if module_name == package_name or module_name.startswith(package_name + "."):
                del sys.modules[module_name]
        package = types.ModuleType(package_name)
        package.__path__ = [str(path.parent.resolve())]  # type: ignore[attr-defined]
        sys.modules[package_name] = package
        imported_module_name = f"{package_name}.{path.stem}"
        try:
            spec = importlib.util.spec_from_file_location(imported_module_name, path)
            if spec is None or spec.loader is None:
                raise ImportError("could not create module spec")
            imported_module = importlib.util.module_from_spec(spec)
            sys.modules[imported_module_name] = imported_module
            exec(compile(text, str(path), "exec"), imported_module.__dict__)
        except Exception as exc:
            failures.append(
                f"{name}: active artifact import failed with frozen dependencies: "
                f"{type(exc).__name__}: {exc}"
            )
            imported_module = None
        try:
            artifact_tree = ast.parse(text, filename=str(path))
        except SyntaxError:
            artifact_tree = None
        if artifact_tree is not None:
            public_prefixes = ("create_", "add_")
            for node in artifact_tree.body:
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if not node.name.startswith(public_prefixes):
                    continue
                if node.args.vararg is not None or node.args.kwarg is not None:
                    failures.append(
                        f"{name}: public tool `{node.name}` uses *args/**kwargs; "
                        "the actual definition must have an explicit publishable signature"
                    )
                if imported_module is not None and not callable(
                    getattr(imported_module, node.name, None)
                ):
                    failures.append(
                        f"{name}: public tool `{node.name}` is not callable after real import"
                    )
        if imported_module is not None and not name.endswith("_creation_base.py"):
            try:
                runtime = importlib.import_module(f"{package_name}._fixed_rdf_runtime")
                canonical_registry_key = runtime._REGISTRY_KEY
                probe_registry_key = f"{canonical_registry_key}::stage-probe::{package_name}"
                runtime._REGISTRY_KEY = probe_registry_key
                graph = runtime.retained_graph()
                runtime.reset_graph(graph)
            except Exception as exc:
                failures.append(
                    f"{name}: retained-graph behavior probe could not start: "
                    f"{type(exc).__name__}: {exc}"
                )
            else:
                publish_contract = (
                    context.contract.get("ontology_publish_contract") or {}
                )
                class_iris = {
                    str(item.get("class_iri") or "")
                    for item in publish_contract.get("classes") or []
                    if str(item.get("class_iri") or "")
                }
                class_by_local = {
                    iri.rsplit("#", 1)[-1].rsplit("/", 1)[-1]: iri
                    for iri in class_iris
                }
                external_creator_specs = list(
                    context.contract.get("external_class_creators") or []
                )
                creators: dict[str, Any] = {
                    local: getattr(imported_module, f"create_{local}", None)
                    for local in class_by_local
                }
                ordered_classes = {
                    str(value).strip()
                    for value in (
                        (context.contract.get("ordered_member_profile") or {}).get(
                            "ordered_member_classes"
                        )
                        or []
                    )
                    if str(value).strip()
                }
                from src.agents.scripts_and_prompts_generation.creator_atomicity import (
                    creator_call_recipe,
                )
                from src.agents.scripts_and_prompts_generation.pure_llm_generation import (
                    _owned_entity_tool_contracts,
                )

                creator_contracts = _owned_entity_tool_contracts(context)
                creator_contract_by_local = {
                    str(contract.get("class_local") or ""): contract
                    for contract in creator_contracts
                }
                if name.endswith("_creation_entities.py"):
                    actual_creator_names = {
                        symbol
                        for symbol, value in vars(imported_module).items()
                        if symbol.startswith("create_") and callable(value)
                    }
                    from src.agents.scripts_and_prompts_generation.pure_llm_generation import (
                        _owned_entity_tool_contracts,
                    )

                    expected_creator_names = {
                        str(item.get("public_tool") or "")
                        for item in _owned_entity_tool_contracts(context)
                        if str(item.get("public_tool") or "")
                    }
                    om2_range_iris = {
                        str(range_iri)
                        for item in publish_contract.get("object_properties") or []
                        for range_iri in item.get("range_iris") or []
                        if "ontology-of-units-of-measure.org/resource/om-2/"
                        in str(range_iri)
                    }
                    if om2_range_iris:
                        expected_creator_names.add("create_om2_quantity")
                    expected_creator_names.update(
                        str((spec or {}).get("tool_name") or "").strip()
                        for spec in external_creator_specs
                        if str((spec or {}).get("tool_name") or "").strip()
                    )
                    if actual_creator_names != expected_creator_names:
                        failures.append(
                            f"{name}: public creator surface differs from ontology-owned classes; "
                            f"missing={sorted(expected_creator_names - actual_creator_names)}, "
                            f"unexpected={sorted(actual_creator_names - expected_creator_names)}"
                        )
                    if om2_range_iris:
                        om2_creator = getattr(
                            imported_module, "create_om2_quantity", None
                        )
                        om2_owner = str(
                            getattr(om2_creator, "__module__", "")
                        ).rsplit(".", 1)[-1]
                        if om2_owner != "_fixed_rdf_runtime":
                            failures.append(
                                f"{name}: create_om2_quantity must be imported directly from "
                                "_fixed_rdf_runtime; local wrappers or definitions are forbidden"
                            )
                    for spec in external_creator_specs:
                        tool_name = str((spec or {}).get("tool_name") or "").strip()
                        class_iri = str((spec or {}).get("class_iri") or "").strip()
                        creator = getattr(imported_module, tool_name, None)
                        if not tool_name or not class_iri or not callable(creator):
                            continue
                        before = _graph_fingerprint(graph)
                        try:
                            payload = json.loads(creator(f"Validator {tool_name}"))
                            created_iri = str(payload.get("iri") or "")
                        except Exception as exc:
                            failures.append(
                                f"{name}: {tool_name} external-class behavior probe failed: "
                                f"{type(exc).__name__}: {exc}"
                            )
                            continue
                        if not created_iri or (
                            URIRef(created_iri),
                            RDF.type,
                            URIRef(class_iri),
                        ) not in graph:
                            failures.append(
                                f"{name}: {tool_name} must create exact external rdf:type "
                                f"{class_iri}"
                            )
                        if _graph_fingerprint(graph) == before:
                            failures.append(
                                f"{name}: {tool_name} reported success without graph mutation"
                            )
                created: dict[str, str] = {}
                for local, class_iri in sorted(class_by_local.items()):
                    creator = creators.get(local)
                    if not callable(creator):
                        continue
                    try:
                        contract = creator_contract_by_local.get(local)
                        if contract is not None:
                            recipe = creator_call_recipe(
                                contract,
                                creator,
                                label=f"Validator {local}",
                                include_optional_datatypes=False,
                            )
                            _seed_existing_operation_targets(
                                graph, contract, recipe["kwargs"]
                            )
                            before = _graph_fingerprint(graph)
                            raw_result = creator(
                                *recipe["args"],
                                **recipe["kwargs"],
                            )
                        else:
                            before = _graph_fingerprint(graph)
                            raw_result = creator(f"Validator {local}")
                    except TypeError:
                        # Freely named required parameters are judged by LLM review.
                        continue
                    except Exception as exc:
                        failures.append(
                            f"{name}: create_{local} behavior probe failed: "
                            f"{type(exc).__name__}: {exc}"
                        )
                        continue
                    if not isinstance(raw_result, str):
                        failures.append(
                            f"{name}: create_{local} must return a JSON string envelope"
                        )
                        continue
                    try:
                        result = json.loads(raw_result)
                    except json.JSONDecodeError:
                        failures.append(
                            f"{name}: create_{local} returned a non-JSON string"
                        )
                        continue
                    if not isinstance(result, dict) or result.get("status") != "ok":
                        failures.append(
                            f"{name}: create_{local} did not return a successful standard envelope"
                        )
                        continue
                    created_iri = str(result.get("iri") or "").strip()
                    if not created_iri:
                        failures.append(
                            f"{name}: create_{local} success envelope has no IRI"
                        )
                        continue
                    if before == _graph_fingerprint(graph):
                        failures.append(
                            f"{name}: create_{local} returned without mutating the retained graph"
                        )
                    if (
                        URIRef(created_iri),
                        RDF.type,
                        URIRef(class_iri),
                    ) not in graph:
                        failures.append(
                            f"{name}: create_{local} did not assert its T-Box class in the retained graph"
                        )
                    else:
                        created[local] = created_iri
                    class_spec = (context.parsed.get("classes") or {}).get(local) or {}
                    for parent_local in class_spec.get("parent_classes") or []:
                        parent_spec = (
                            (context.parsed.get("classes") or {}).get(parent_local) or {}
                        )
                        parent_iri = str(parent_spec.get("iri") or "").strip()
                        if parent_iri and (
                            URIRef(created_iri),
                            RDF.type,
                            URIRef(parent_iri),
                        ) not in graph:
                            failures.append(
                                f"{name}: create_{local} did not explicitly assert ancestor "
                                f"type {parent_local}"
                            )

                if name.endswith("_creation_entities.py") and om2_range_iris:
                    om2_creator = getattr(imported_module, "create_om2_quantity", None)
                    if callable(om2_creator):
                        allowed_class = sorted(om2_range_iris)[0]
                        before = _graph_fingerprint(graph)
                        try:
                            raw_result = om2_creator(allowed_class, "1 s")
                            result = json.loads(raw_result)
                            quantity_iri = str(result.get("iri") or "")
                        except Exception as exc:
                            failures.append(
                                f"{name}: create_om2_quantity valid probe failed: "
                                f"{type(exc).__name__}: {exc}"
                            )
                        else:
                            if (
                                not quantity_iri
                                or (
                                    URIRef(quantity_iri),
                                    RDF.type,
                                    URIRef(allowed_class),
                                )
                                not in graph
                                or before == _graph_fingerprint(graph)
                            ):
                                failures.append(
                                    f"{name}: create_om2_quantity did not create the allowed "
                                    "T-Box range class"
                                )
                        before = _graph_fingerprint(graph)
                        try:
                            rejected_raw = om2_creator(
                                "https://example.invalid/NotAllowed",
                                "1 s",
                            )
                        except Exception:
                            if before != _graph_fingerprint(graph):
                                failures.append(
                                    f"{name}: create_om2_quantity mutated graph while rejecting "
                                    "an unapproved quantity class"
                                )
                        else:
                            try:
                                rejected_result = json.loads(rejected_raw)
                            except (TypeError, json.JSONDecodeError):
                                rejected_result = {}
                            rejected = (
                                isinstance(rejected_result, dict)
                                and rejected_result.get("status")
                                in {"rejected", "error"}
                            )
                            if not rejected:
                                failures.append(
                                    f"{name}: create_om2_quantity accepted a class outside the "
                                    "T-Box OM-2 range set"
                                )
                            if before != _graph_fingerprint(graph):
                                failures.append(
                                    f"{name}: create_om2_quantity mutated graph while returning "
                                    "rejection for an unapproved quantity class"
                                )

                relationship_probe_baseline = set(graph)
                for prop in publish_contract.get("object_properties") or []:
                    graph.remove((None, None, None))
                    for triple in relationship_probe_baseline:
                        graph.add(triple)
                    predicate_iri = str(prop.get("property_iri") or "")
                    property_local = predicate_iri.rsplit("#", 1)[-1].rsplit("/", 1)[-1]
                    writer = getattr(imported_module, f"add_{property_local}", None)
                    if not callable(writer):
                        continue
                    domains = [
                        str(value) for value in prop.get("domain_iris") or [] if str(value)
                    ]
                    ranges = [
                        str(value) for value in prop.get("range_iris") or [] if str(value)
                    ]
                    if not domains or not ranges:
                        continue
                    domain_local = domains[0].rsplit("#", 1)[-1].rsplit("/", 1)[-1]
                    range_local = ranges[0].rsplit("#", 1)[-1].rsplit("/", 1)[-1]
                    subject_iri = created.get(domain_local)
                    if not subject_iri:
                        continue
                    # Probe each property with its own typed object occurrence.
                    # Reusing the single class-level fixture across properties can
                    # legitimately trigger OBJECT_OCCURRENCE_REUSE_FORBIDDEN for
                    # non-reusable inputs/quantities owned by ordered steps.
                    object_iri = f"urn:validator:{property_local}:{range_local}"
                    graph.add(
                        (
                            URIRef(object_iri),
                            RDF.type,
                            URIRef(ranges[0]),
                        )
                    )
                    signature = inspect.signature(writer)
                    parameters = signature.parameters
                    kwargs: dict[str, Any] = {}
                    if "object_iri" in parameters:
                        kwargs["object_iri"] = object_iri
                    subject_candidates = [
                        parameter
                        for parameter in parameters.values()
                        if parameter.name != "object_iri"
                        and parameter.default is inspect.Parameter.empty
                    ]
                    if not subject_candidates:
                        failures.append(
                            f"{name}: add_{property_local} has no explicit required subject parameter"
                        )
                        continue
                    kwargs[subject_candidates[0].name] = subject_iri
                    try:
                        writer(**kwargs)
                    except Exception as exc:
                        failures.append(
                            f"{name}: add_{property_local} rejected a T-Box-valid call: "
                            f"{type(exc).__name__}: {exc}"
                        )
                        continue
                    expected_triple = (
                        URIRef(subject_iri),
                        URIRef(predicate_iri),
                        URIRef(object_iri),
                    )
                    if expected_triple not in graph:
                        failures.append(
                            f"{name}: add_{property_local} returned without writing its bound T-Box predicate"
                        )
                    incompatible = next(
                        (
                            iri
                            for iri in sorted(class_iris)
                            if iri not in set(ranges)
                            and iri not in set(domains)
                        ),
                        "",
                    )
                    if not incompatible:
                        continue
                    wrong_object = URIRef(f"urn:validator:wrong:{property_local}")
                    graph.add((wrong_object, RDF.type, URIRef(incompatible)))
                    wrong_kwargs = dict(kwargs)
                    wrong_kwargs["object_iri"] = str(wrong_object)
                    before_wrong = _graph_fingerprint(graph)
                    rejected = False
                    try:
                        wrong_result = writer(**wrong_kwargs)
                    except Exception:
                        rejected = True
                    else:
                        rejected = _is_structured_rejection(wrong_result)
                    after_wrong = _graph_fingerprint(graph)
                    if not rejected:
                        failures.append(
                            f"{name}: add_{property_local} accepted a T-Box-incompatible range"
                        )
                    if before_wrong != after_wrong:
                        failures.append(
                            f"{name}: add_{property_local} mutated the graph during a rejected wrong-range call"
                        )
                graph.remove((None, None, None))
                for triple in relationship_probe_baseline:
                    graph.add(triple)
                if name.endswith("_creation_entities.py"):
                    for prop in publish_contract.get("object_properties") or []:
                        predicate_iri = str(prop.get("property_iri") or "")
                        property_local = _local_name(predicate_iri)
                        ranges = [
                            str(value)
                            for value in prop.get("range_iris") or []
                            if str(value)
                        ]
                        domains = [
                            str(value)
                            for value in prop.get("domain_iris") or []
                            if str(value)
                        ]
                        if len(ranges) != 1 or not domains:
                            continue
                        range_iri = ranges[0]
                        domain_local = _local_name(domains[0])
                        creator = creators.get(domain_local)
                        if not callable(creator):
                            continue
                        parameter_name = f"{property_local}_label"
                        if parameter_name not in inspect.signature(creator).parameters:
                            continue
                        before = _graph_fingerprint(graph)
                        try:
                            raw_result = creator(
                                f"Validator {domain_local} {property_local}",
                                **{parameter_name: f"Validator {property_local} target"},
                            )
                            result = json.loads(raw_result)
                        except Exception as exc:
                            failures.append(
                                f"{name}: create_{domain_local} range-materialization probe for "
                                f"{property_local} failed: {type(exc).__name__}: {exc}"
                            )
                            continue
                        if not isinstance(result, dict) or result.get("status") != "ok":
                            failures.append(
                                f"{name}: create_{domain_local} could not materialize its "
                                f"T-Box object-property target for {property_local}"
                            )
                            continue
                        subject_iri = URIRef(str(result.get("iri") or ""))
                        targets = list(graph.objects(subject_iri, URIRef(predicate_iri)))
                        if not targets:
                            failures.append(
                                f"{name}: create_{domain_local} did not materialize "
                                f"{property_local} from its label parameter"
                            )
                            continue
                        if not any(
                            (target, RDF.type, URIRef(range_iri)) in graph
                            for target in targets
                        ):
                            observed_types = sorted(
                                {
                                    str(value)
                                    for target in targets
                                    for value in graph.objects(target, RDF.type)
                                    if isinstance(value, URIRef)
                                }
                            )
                            failures.append(
                                f"{name}: create_{domain_local} materialized {property_local} "
                                f"with target types {observed_types}, expected {range_iri}"
                            )
                        if before == _graph_fingerprint(graph):
                            failures.append(
                                f"{name}: create_{domain_local} range-materialization probe "
                                f"for {property_local} did not mutate the retained graph"
                            )
                if name.endswith("_creation_checks.py"):
                    checker = getattr(imported_module, "check_ordered_members", None)
                    from src.agents.scripts_and_prompts_generation.pure_llm_generation import (
                        _existing_entity_check_contracts,
                        _existing_entity_check_manifest,
                    )

                    expected_existing_checks = [
                        str(item.get("public_tool") or "")
                        for item in _existing_entity_check_contracts(context)
                        if str(item.get("public_tool") or "")
                    ]
                    expected_checks = {
                        "check_ordered_members",
                        *expected_existing_checks,
                    }
                    public_checks = {
                        symbol
                        for symbol, value in vars(imported_module).items()
                        if symbol.startswith("check_") and callable(value)
                    }
                    if public_checks != expected_checks:
                        failures.append(
                            f"{name}: check surface differs from T-Box-derived checks; "
                            f"expected={sorted(expected_checks)} actual={sorted(public_checks)}"
                        )
                    expected_manifest = _existing_entity_check_manifest(context)
                    if getattr(imported_module, "__all__", None) != expected_manifest:
                        failures.append(
                            f"{name}: __all__ must equal {expected_manifest}"
                        )
                    try:
                        if _literal_all_manifest(path) != expected_manifest:
                            failures.append(
                                f"{name}: literal __all__ must equal {expected_manifest}"
                            )
                    except Exception as exc:
                        failures.append(
                            f"{name}: invalid literal __all__ manifest: "
                            f"{type(exc).__name__}: {exc}"
                        )
                    check_contracts = _existing_entity_check_contracts(context)
                    if any(
                        str(item.get("lookup_scope") or "")
                        in EXISTING_CHECK_EVIDENCE_REQUIRED_SCOPES
                        for item in check_contracts
                    ):
                        tree = ast.parse(path.read_text(encoding="utf-8"))
                        called_names = {
                            (
                                node.func.id
                                if isinstance(node.func, ast.Name)
                                else node.func.attr
                                if isinstance(node.func, ast.Attribute)
                                else ""
                            )
                            for node in ast.walk(tree)
                            if isinstance(node, ast.Call)
                        }
                        for required_call in (
                            "judge_reuse_pairs",
                            "register_central_reuse_authorization",
                        ):
                            if required_call not in called_names:
                                failures.append(
                                    f"{name}: evidence-required existing checks "
                                    f"must call {required_call}"
                                )
                        judge_calls = [
                            node
                            for node in ast.walk(tree)
                            if isinstance(node, ast.Call)
                            and (
                                (
                                    isinstance(node.func, ast.Name)
                                    and node.func.id == "judge_reuse_pairs"
                                )
                                or (
                                    isinstance(node.func, ast.Attribute)
                                    and node.func.attr == "judge_reuse_pairs"
                                )
                            )
                        ]
                        if not judge_calls or any(
                            len(node.args) != 1 or node.keywords
                            for node in judge_calls
                        ):
                            failures.append(
                                f"{name}: judge_reuse_pairs must be called with "
                                "exactly one positional requests list"
                            )
                        authorization_calls = [
                            node
                            for node in ast.walk(tree)
                            if isinstance(node, ast.Call)
                            and (
                                (
                                    isinstance(node.func, ast.Name)
                                    and node.func.id
                                    == "register_central_reuse_authorization"
                                )
                                or (
                                    isinstance(node.func, ast.Attribute)
                                    and node.func.attr
                                    == "register_central_reuse_authorization"
                                )
                            )
                        ]
                        required_authorization_keywords = {
                            "candidate_iri",
                            "pair_id",
                            "judgement",
                        }
                        if not authorization_calls or any(
                            node.args
                            or {keyword.arg for keyword in node.keywords}
                            != required_authorization_keywords
                            for node in authorization_calls
                        ):
                            failures.append(
                                f"{name}: register_central_reuse_authorization must "
                                "use exactly candidate_iri, pair_id, and judgement"
                            )
                    for check_contract in check_contracts:
                        tool_name = str(check_contract.get("public_tool") or "")
                        check_tool = getattr(imported_module, tool_name, None)
                        if not callable(check_tool):
                            continue
                        signature = inspect.signature(check_tool)
                        proposed_parameter = signature.parameters.get(
                            "proposed_entity_json"
                        )
                        label_parameter = signature.parameters.get("label")
                        if (
                            proposed_parameter is None
                            or proposed_parameter.default != ""
                            or label_parameter is None
                            or label_parameter.default != ""
                            or label_parameter.kind
                            is not inspect.Parameter.KEYWORD_ONLY
                        ):
                            failures.append(
                                f"{name}: {tool_name} must expose "
                                "proposed_entity_json: str = '' and keyword-only "
                                "label: str = '' compatibility inputs"
                            )
                        try:
                            payload = json.loads(check_tool())
                        except Exception as exc:
                            failures.append(
                                f"{name}: {tool_name} did not return a JSON report: "
                                f"{type(exc).__name__}: {exc}"
                            )
                            continue
                        failures.extend(
                            empty_existing_check_probe_failures(
                                artifact_name=name,
                                tool_name=tool_name,
                                lookup_scope=str(
                                    check_contract.get("lookup_scope") or ""
                                ),
                                payload=payload,
                                expected_reuse_authorized=bool(
                                    check_contract.get("reuse_authorized")
                                ),
                                expected_reference_resolution_only=bool(
                                    check_contract.get("reference_resolution_only")
                                ),
                            )
                        )
                    profile = context.contract.get("ordered_member_profile") or {}
                    member_locals = list(
                        profile.get("individually_linked_object_properties") or []
                    )
                    order_locals = list(
                        profile.get("single_valued_ordering_properties") or []
                    )
                    properties = context.parsed.get("properties") or {}
                    classes = context.parsed.get("classes") or {}
                    member_iri = str(
                        (properties.get(member_locals[0]) or {}).get("iri") or ""
                    ) if member_locals else ""
                    order_iri = str(
                        (properties.get(order_locals[0]) or {}).get("iri") or ""
                    ) if order_locals else ""
                    ordered_locals = list(profile.get("ordered_member_classes") or [])
                    concrete_local = next(
                        (
                            local
                            for local in ordered_locals
                            if (classes.get(local) or {}).get("parent_classes")
                        ),
                        ordered_locals[0] if ordered_locals else "",
                    )
                    concrete_iri = str(
                        (classes.get(concrete_local) or {}).get("iri") or ""
                    )
                    parent_types = [
                        str((classes.get(parent) or {}).get("iri") or "")
                        for parent in (
                            (classes.get(concrete_local) or {}).get("parent_classes")
                            or []
                        )
                        if str((classes.get(parent) or {}).get("iri") or "")
                    ]
                    ordered_response_schema_failed = False

                    def run_order_probe(
                        triples: list[tuple[URIRef, URIRef, Any]],
                        expected_codes: set[str],
                    ) -> None:
                        nonlocal ordered_response_schema_failed
                        graph.remove((None, None, None))
                        for triple in triples:
                            graph.add(triple)
                        before = _graph_fingerprint(graph)
                        try:
                            raw_result = checker()
                            result = json.loads(raw_result)
                        except Exception as exc:
                            failures.append(
                                f"{name}: ordered-member behavior probe failed: "
                                f"{type(exc).__name__}: {exc}"
                            )
                            return
                        if before != _graph_fingerprint(graph):
                            failures.append(
                                f"{name}: check_ordered_members mutated the graph"
                            )
                        codes, response_schema_errors = (
                            _ordered_violation_code_report(result)
                        )
                        if expected_codes:
                            if result.get("status") not in {"rejected", "error"}:
                                failures.append(
                                    f"{name}: invalid ordered graph did not return rejection"
                                )
                            if response_schema_errors:
                                if not ordered_response_schema_failed:
                                    failures.append(
                                        f"{name}: FIELD_SCHEMA_ERROR ordered violation "
                                        "response schema invalid: "
                                        + response_schema_errors[0]
                                        + "; repair_hint=Every `violations` item must use "
                                        "the exact required discriminator key `code`, for example "
                                        "`{\"code\": \"missing_order\", ...}`. Rename "
                                        "`violation_code` to `code`; do not change the "
                                        "ordered-member detection algorithm to repair this "
                                        "schema failure."
                                    )
                                    ordered_response_schema_failed = True
                                return
                            missing_codes = expected_codes - codes
                            if missing_codes:
                                repair_hint = ""
                                if "non_contiguous_order" in missing_codes:
                                    repair_hint = (
                                        "; repair_hint=For each parent, collect every linked "
                                        "ordered member before filtering invalid/missing order "
                                        "values. Let N be that full ordered-member count and "
                                        "compare the set of valid observed orders with "
                                        "set(range(1, N + 1)). Do not derive N from "
                                        "max(observed), len(unique observed orders), or only "
                                        "members having valid order literals. Generic example: "
                                        "three linked ordered members with observed orders 1, 2, "
                                        "and missing must report both missing_order and "
                                        "non_contiguous_order because expected={1,2,3} and "
                                        "observed={1,2}."
                                    )
                                failures.append(
                                    f"{name}: ordered check missed violations "
                                    f"{sorted(missing_codes)}{repair_hint}"
                                )
                        elif result.get("status") != "ok" or codes:
                            failures.append(
                                f"{name}: valid ordered graph was not accepted; "
                                f"observed violation codes={sorted(codes)}"
                            )

                    if (
                        callable(checker)
                        and member_iri
                        and order_iri
                        and concrete_iri
                    ):
                        member_predicate = URIRef(member_iri)
                        order_predicate = URIRef(order_iri)
                        class_ref = URIRef(concrete_iri)
                        parent = URIRef("urn:validator:ordered-parent")
                        other_parent = URIRef("urn:validator:other-parent")
                        first = URIRef("urn:validator:ordered-1")
                        second = URIRef("urn:validator:ordered-2")
                        required_operation_triples = [
                            *_required_operation_fixture_triples(
                                operation_units=(
                                    context.contract.get(
                                        "materialization_operation_units"
                                    )
                                    or {}
                                ),
                                owner_class_iri=concrete_iri,
                                owner=first,
                                parent=parent,
                                token="ordered-1",
                            ),
                            *_required_operation_fixture_triples(
                                operation_units=(
                                    context.contract.get(
                                        "materialization_operation_units"
                                    )
                                    or {}
                                ),
                                owner_class_iri=concrete_iri,
                                owner=second,
                                parent=parent,
                                token="ordered-2",
                            ),
                        ]
                        type_triples = [
                            (first, RDF.type, class_ref),
                            (second, RDF.type, class_ref),
                            *[
                                (node, RDF.type, URIRef(parent_iri))
                                for node in (first, second)
                                for parent_iri in parent_types
                            ],
                            *required_operation_triples,
                        ]
                        links = [
                            (parent, member_predicate, first),
                            (parent, member_predicate, second),
                        ]
                        run_order_probe(
                            [
                                *type_triples,
                                *links,
                                (first, order_predicate, Literal(1)),
                                (second, order_predicate, Literal(2)),
                            ],
                            set(),
                        )
                        run_order_probe(
                            [
                                *type_triples,
                                *links,
                                (second, order_predicate, Literal(2)),
                            ],
                            {"missing_order", "non_contiguous_order"},
                        )
                        run_order_probe(
                            [
                                *type_triples,
                                *links,
                                (first, order_predicate, Literal(1)),
                                (second, order_predicate, Literal(1)),
                            ],
                            {"duplicate_order", "non_contiguous_order"},
                        )
                        run_order_probe(
                            [
                                *type_triples,
                                *links,
                                (first, order_predicate, Literal(1)),
                                (second, order_predicate, Literal(3)),
                            ],
                            {"non_contiguous_order"},
                        )
                        run_order_probe(
                            [
                                *type_triples,
                                *links,
                                (other_parent, member_predicate, first),
                                (first, order_predicate, Literal(1)),
                                (second, order_predicate, Literal(2)),
                            ],
                            {"multiple_parents"},
                        )
                        if parent_types:
                            run_order_probe(
                                [
                                    (first, RDF.type, class_ref),
                                    *links,
                                    (first, order_predicate, Literal(1)),
                                    (second, order_predicate, Literal(2)),
                                    (second, RDF.type, class_ref),
                                    *[
                                        (second, RDF.type, URIRef(parent_iri))
                                        for parent_iri in parent_types
                                    ],
                                    *required_operation_triples,
                                ],
                                {"missing_explicit_ancestor_type"},
                            )
                try:
                    runtime.reset_graph(graph)
                finally:
                    runtime._graph_registry().pop(probe_registry_key, None)
                    runtime._REGISTRY_KEY = canonical_registry_key
    if name.endswith("_creation_base.py"):
        if imported_module is not None:
            if getattr(imported_module, "__all__", None) != ["rdf_runtime"]:
                failures.append(
                    f"{name}: shared base must expose exactly the stable `rdf_runtime` module alias"
                )
            leaked_domain_tools = sorted(
                symbol
                for symbol, value in vars(imported_module).items()
                if symbol.startswith(("create_", "add_")) and callable(value)
            )
            if leaked_domain_tools:
                failures.append(
                    f"{name}: shared base defines domain-bound tools owned by later layers: "
                    + ", ".join(leaked_domain_tools)
                )
            wrapper_functions = [
                node.name
                for node in (artifact_tree.body if artifact_tree is not None else [])
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            if wrapper_functions:
                failures.append(
                    f"{name}: minimal fixed-runtime adapter must not duplicate function wrappers: "
                    + ", ".join(wrapper_functions)
                )
        # AST-aware OM-2 runtime obligation
        helper_names = {
            "find_or_create_om2_quantity",
            "find_or_create_om2_quantity_from_label",
            "resolve_om2_unit",
            "parse_om2_quantity_label",
        }
        has_rel_fixed_from_import = False
        has_bad_fixed_import = False
        imports_any_om2 = False
        references_helpers = False
        try:
            tree = ast.parse(text)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    if node.level >= 1 and module == "_fixed_om2_runtime":
                        has_rel_fixed_from_import = True
                        imports_any_om2 = True
                    if module in ("fixed_om2_runtime", "om2.runtime.fixed"):
                        has_bad_fixed_import = True
                        imports_any_om2 = True
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name in ("fixed_om2_runtime", "om2.runtime.fixed"):
                            has_bad_fixed_import = True
                            imports_any_om2 = True
                if isinstance(node, ast.Name) and node.id in helper_names:
                    references_helpers = True
                elif isinstance(node, ast.Attribute) and node.attr in helper_names:
                    references_helpers = True
        except SyntaxError:
            has_rel_fixed_from_import = "from ._fixed_om2_runtime import" in text
            has_bad_fixed_import = (
                "from fixed_om2_runtime import" in text
                or "import fixed_om2_runtime" in text
                or "om2.runtime.fixed" in text
            )
            references_helpers = any(name in text for name in helper_names)
            imports_any_om2 = has_rel_fixed_from_import or has_bad_fixed_import

        uses_om2 = imports_any_om2 or references_helpers
        if uses_om2:
            if not has_rel_fixed_from_import:
                failures.append(
                    f"{name}: OM-2 foundation must use a package-relative import "
                    "from `._fixed_om2_runtime`"
                )
            if has_bad_fixed_import:
                failures.append(
                    f"{name}: OM-2 foundation uses a non-package fixed-runtime import"
                )
    elif name.endswith("_creation_entities.py"):
        from src.agents.scripts_and_prompts_generation.pure_llm_generation import (
            _owned_entity_tool_contracts,
        )

        owned_class_locals = {
            str(contract.get("class_local") or "")
            for contract in _owned_entity_tool_contracts(context)
            if str(contract.get("class_local") or "")
        }
        missing = [
            class_local
            for class_local in sorted(owned_class_locals)
            if f"def create_{class_local}" not in text
        ]
        if missing:
            failures.append(
                f"{name}: missing stage create tools: "
                + ", ".join(f"create_{class_local}" for class_local in missing[:20])
            )
        forbidden_entity_apis = (
            "create_individual",
            "add_type",
            "GRAPH.add(",
            "retained_graph().add(",
        )
        used_forbidden = [
            api for api in forbidden_entity_apis if api in text
        ]
        if used_forbidden:
            warnings.append(
                f"{name}: entity implementation uses internal generic capabilities via "
                + ", ".join(used_forbidden)
            )
        if "package_entity_capabilities" not in text:
            warnings.append(
                f"{name}: entity implementation does not use the optional package-bound capability helper"
            )
    elif name.endswith("_creation_relationships.py"):
        from src.agents.scripts_and_prompts_generation.materialization_operation_units import (
            standalone_relationship_tool_contracts,
        )

        relationship_contracts = standalone_relationship_tool_contracts(
            context.contract.get("relationship_tool_contracts") or {},
            context.contract.get("materialization_operation_units") or {},
        )
        object_properties = sorted(relationship_contracts)
        missing = [
            prop_local
            for prop_local in object_properties
            if f"def add_{prop_local}" not in text
        ]
        if missing:
            failures.append(
                f"{name}: missing stage relationship tools: "
                + ", ".join(f"add_{prop_local}" for prop_local in missing[:20])
            )
        try:
            relationship_tree = ast.parse(text, filename=str(path))
        except SyntaxError:
            relationship_tree = None
        if relationship_tree is not None:
            functions = {
                node.name: node
                for node in relationship_tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            for prop_local, contract in sorted(relationship_contracts.items()):
                function = functions.get(f"add_{prop_local}")
                if function is None:
                    continue
                expected_predicate = str(
                    (contract or {}).get("predicate_iri") or ""
                ).strip()
                range_iris = {
                    str(value).strip()
                    for value in (contract or {}).get("range_iris") or []
                    if str(value).strip()
                }
                binding = _relationship_binding_evidence(
                    function,
                    module=relationship_tree,
                )
                bound_iris = set(binding["bound_iris"])
                if binding["call_count"] != 1:
                    failures.append(
                        f"{name}: add_{prop_local} must perform exactly one relationship "
                        "capability call that receives both subject_iri and object_iri; "
                        f"observed {binding['call_count']}"
                    )
                if (
                    expected_predicate
                    and bound_iris
                    and expected_predicate not in bound_iris
                ):
                    failures.append(
                        f"{name}: add_{prop_local} must bind predicate IRI "
                        f"{expected_predicate}, observed {sorted(bound_iris) or ['none']}"
                    )
                elif expected_predicate and not bound_iris:
                    warnings.append(
                        f"{name}: add_{prop_local} predicate binding could not be proven "
                        "by conservative static analysis; runtime behavior probes remain "
                        "authoritative"
                    )
                mistaken_ranges = sorted(bound_iris & range_iris)
                if mistaken_ranges:
                    failures.append(
                        f"{name}: add_{prop_local} binds range class IRI as predicate: "
                        + ", ".join(mistaken_ranges)
                    )
        forbidden_mutation_apis = (
            "add_object_property",
            "add_object_triple",
            ".add((",
            "Graph.add(",
        )
        used_forbidden = [
            api for api in forbidden_mutation_apis if api in text
        ]
        if used_forbidden:
            warnings.append(
                f"{name}: relationship implementation uses internal generic capabilities via "
                + ", ".join(used_forbidden)
            )
        if "package_relationship_capabilities" not in text:
            warnings.append(
                f"{name}: relationship implementation does not use the optional package-bound capability helper"
            )
        metadata_failures, metadata_warnings = (
            _relationship_param_description_report(context)
        )
        failures.extend(metadata_failures)
        warnings.extend(metadata_warnings)
    elif name == "main.py":
        runtime_tool_names = ("init_memory", "export_memory")
        for tool_name in runtime_tool_names:
            if tool_name not in text:
                failures.append(f"{name}: missing runtime adapter `{tool_name}`")
        try:
            main_tree = ast.parse(text, filename=str(path))
        except SyntaxError:
            main_tree = None
        if main_tree is not None:
            for node in main_tree.body:
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if node.name not in runtime_tool_names:
                    continue
                if node.args.vararg is not None or node.args.kwarg is not None:
                    failures.append(
                        f"{name}: runtime adapter `{node.name}` uses *args/**kwargs; "
                        "FastMCP tools require explicit publishable parameters"
                    )
            if any(
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == "materialize_hints"
                for node in main_tree.body
            ):
                failures.append(
                    f"{name}: aggregate `materialize_hints` is forbidden; expose atomic "
                    "create/add tools instead"
                )
        try:
            _import_generated_main_module(path.parent, context.ontology.name)
        except Exception as exc:
            failures.append(
                f"{name}: runtime adapter import smoke failed: "
                f"{type(exc).__name__}: {exc}"
            )
    elif path.suffix == ".md":
        binding_report = validate_prompt_runtime_bindings(path, context)
        failures.extend(binding_report.get("failures") or [])
        if "TODO" in text or "FIXME" in text:
            warnings.append(f"{name}: prompt contains unresolved TODO/FIXME residue")
        if (
            name == "KG_BUILDING_ITER_1.md"
            and getattr(getattr(context, "ontology", None), "role", "")
            != "extension"
        ):
            execution_failures, execution_warnings = (
                _iter1_kg_prompt_execution_contract_report(context)
            )
            failures.extend(execution_failures)
            warnings.extend(execution_warnings)
        unresolved = sorted(
            set(re.findall(r"\{\{[^}\n]+\}\}", text, flags=re.IGNORECASE))
        )
        if unresolved:
            failures.append(
                f"{name}: unresolved prompt placeholder/residue: "
                + ", ".join(unresolved[:8])
            )
    return failures, warnings, [relative]


def _iteration_prompt_schema_contract_report(
    context: AgenticGenerationContext,
    prompt_paths: list[Path] | None = None,
) -> tuple[list[str], list[str]]:
    """Defer natural-language interchange semantics to the LLM reviewer.

    Keyword and regex matching cannot distinguish an instruction that authorizes
    a competing output shape from one that explicitly prohibits it. Mechanical
    prompt validation is therefore limited elsewhere to objective file and
    runtime-slot structure; this semantic contract intentionally has no
    script-based gate.
    """
    del context, prompt_paths
    return [], []


def _iter1_kg_prompt_execution_contract_report(
    context: AgenticGenerationContext,
) -> tuple[list[str], list[str]]:
    """Defer KG Iter1 prompt executability semantics to the LLM reviewer."""
    del context
    return [], []


def _kg_iteration_owned_scope_report(
    context: AgenticGenerationContext,
    prompt_paths: list[Path] | None = None,
) -> tuple[list[str], list[str]]:
    """Defer KG ownership prose semantics to the LLM reviewer."""
    del context, prompt_paths
    return [], []


def _relationship_param_description_report(
    context: AgenticGenerationContext,
) -> tuple[list[str], list[str]]:
    failures: list[str] = []
    warnings: list[str] = []
    scripts_dir = Path(context.scripts_dir)
    rel_paths = sorted(scripts_dir.glob("*_creation_relationships.py"))
    if not rel_paths:
        warnings.append(
            "Relationship parameter description validation skipped because relationships module is missing"
        )
        return failures, warnings

    def _py_name_local(name: str) -> str:
        out = re.sub(r"\W+", "_", str(name or "")).strip("_")
        if not out:
            out = "unnamed"
        if out[:1].isdigit():
            out = "_" + out
        return out

    from src.agents.scripts_and_prompts_generation.materialization_operation_units import (
        standalone_relationship_tool_contracts,
    )

    object_props = standalone_relationship_tool_contracts(
        context.contract.get("relationship_tool_contracts") or {
        name: {"predicate_local": name}
        for name, prop in (context.parsed.get("properties") or {}).items()
        if (prop or {}).get("kind") == "object"
        },
        context.contract.get("materialization_operation_units") or {},
    )
    rel_source = rel_paths[0]
    try:
        tree = ast.parse(rel_source.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError) as exc:
        failures.append(f"{rel_source.name}: AST parse failed: {type(exc).__name__}: {exc}")
        return failures, warnings
    if any(
        isinstance(node, ast.ImportFrom)
        and node.module == "__future__"
        and any(alias.name == "annotations" for alias in node.names)
        for node in tree.body
    ):
        failures.append(
            f"{rel_source.name}: must not enable deferred annotations because the "
            "installed FastMCP/Pydantic runtime cannot resolve imported Field metadata"
        )

    fndefs: dict[str, ast.FunctionDef] = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }
    for prop_local, spec in object_props.items():
        fn_name = f"add_{_py_name_local(prop_local)}"
        fndef = fndefs.get(fn_name)
        if not fndef:
            # other validator covers missing tool surface
            continue
        subject_param = next(
            (p for p in fndef.args.args if p.arg == "subject_iri"),
            None,
        )
        obj_param = next(
            (p for p in fndef.args.args if p.arg == "object_iri"),
            None,
        )
        all_params = [*fndef.args.args, *fndef.args.kwonlyargs]
        token_param = next(
            (
                parameter
                for parameter in all_params
                if parameter.arg == "reuse_authorization_token"
            ),
            None,
        )
        if token_param is None:
            failures.append(
                f"{rel_source.name}: {fn_name} must expose optional "
                "reuse_authorization_token and pass it to the fixed relationship writer"
            )
        if subject_param is None:
            failures.append(
                f"{rel_source.name}: {fn_name} missing subject_iri parameter"
            )
        if obj_param is None:
            failures.append(
                f"{rel_source.name}: {fn_name} missing object_iri parameter"
            )
    return failures, warnings


def _operation_unit_contract_report(
    context: AgenticGenerationContext,
) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    """Validate operation units against generated signatures and public manifests."""
    failures: list[str] = []
    warnings: list[str] = []
    obligations: list[dict[str, Any]] = []
    compiled = context.contract.get("materialization_operation_units") or {}
    errors = [str(item) for item in compiled.get("errors") or []]
    failures.extend(errors)
    candidates = context.contract.get("materialization_operation_candidates") or {}
    decisions = context.contract.get("materialization_operation_decisions") or {}
    if candidates or decisions:
        from src.agents.scripts_and_prompts_generation.materialization_operation_inference import (
            validate_materialization_operation_decisions,
        )

        failures.extend(
            validate_materialization_operation_decisions(candidates, decisions)
        )
        from src.agents.scripts_and_prompts_generation.materialization_operation_units import (
            compile_materialization_operation_units,
        )

        expected_compiled = compile_materialization_operation_units(
            parsed=context.parsed,
            contract=context.contract,
            iteration_plan=context.iteration_blueprint,
        )
        if (
            expected_compiled.get("merged_predicate_locals")
            != compiled.get("merged_predicate_locals")
            or expected_compiled.get("units") != compiled.get("units")
        ):
            failures.append(
                "materialization operation units do not match the validated "
                "candidate decisions"
            )
    merged = {
        str(value)
        for value in compiled.get("merged_predicate_locals") or []
        if str(value)
    }
    edge_counts: dict[str, int] = {}
    for unit in compiled.get("units") or []:
        creator = (unit or {}).get("creator_contract") or {}
        for edge in creator.get("required_edges") or []:
            local = str(edge.get("predicate_local") or "")
            if local:
                edge_counts[local] = edge_counts.get(local, 0) + 1
    for local in sorted(merged):
        if edge_counts.get(local, 0) < 1:
            failures.append(
                f"Merged predicate {local} is not owned by any operation unit"
            )

    scripts_dir = Path(context.scripts_dir)
    relationship_paths = sorted(scripts_dir.glob("*_creation_relationships.py"))
    if len(relationship_paths) == 1:
        try:
            manifest = set(_literal_all_manifest(relationship_paths[0]))
        except (OSError, SyntaxError, ValueError) as exc:
            warnings.append(
                "Could not validate merged-edge exclusivity because the relationship "
                f"manifest is invalid: {type(exc).__name__}: {exc}"
            )
        else:
            leaked = sorted(
                f"add_{local}" for local in merged if f"add_{local}" in manifest
            )
            if leaked:
                failures.append(
                    "Creator-owned predicates remain publicly exposed: "
                    + ", ".join(leaked)
                )

    entity_paths = sorted(scripts_dir.glob("*_creation_entities.py"))
    if len(entity_paths) == 1:
        try:
            tree = ast.parse(entity_paths[0].read_text(encoding="utf-8"))
        except Exception:
            tree = None
        if tree is not None:
            functions = {
                node.name: node
                for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            for unit in compiled.get("units") or []:
                creator_contract = (unit or {}).get("creator_contract") or {}
                required_edges = creator_contract.get("required_edges") or []
                if not required_edges:
                    continue
                tool_name = str(creator_contract.get("public_tool") or "")
                tool = functions.get(tool_name)
                if tool is None:
                    continue
                parameter_names = {
                    argument.arg
                    for argument in [
                        *tool.args.posonlyargs,
                        *tool.args.args,
                        *tool.args.kwonlyargs,
                    ]
                }
                required_names = set()
                for edge in required_edges:
                    if edge.get("target_resolution") == "existing_iri_parameter":
                        required_names.add(str(edge.get("parameter_name") or ""))
                    if edge.get("target_resolution") == "same_operation_create":
                        required_names.add(str(edge.get("label_parameter") or ""))
                        required_names.update(
                            str(item.get("parameter_name") or "")
                            for item in edge.get("datatype_inputs") or []
                            if item.get("required")
                        )
                missing = sorted(
                    name for name in required_names if name and name not in parameter_names
                )
                if missing:
                    failures.append(
                        f"{tool_name} omits required atomic-operation parameters: {missing}"
                    )
    return failures, warnings, obligations


def build_validation_report(
    context: AgenticGenerationContext,
    *,
    foreign_contracts: list[dict[str, Any]] | None = None,
    write_report: bool = True,
    prompts_required: bool = False,
    include_prompt_checks: bool = True,
    extra_failures: list[str] | None = None,
    active_artifacts: list[str] | None = None,
) -> dict[str, Any]:
    scripts_dir = Path(context.scripts_dir)
    prompts_dir = Path(context.prompts_dir)
    failures: list[str] = []
    warnings: list[str] = []
    observations: list[dict[str, Any]] = []
    active_artifact_set = {
        Path(value).as_posix() for value in (active_artifacts or [])
    }
    stage_mode = active_artifacts is not None

    def record(
        *,
        check_id: str,
        stage: str,
        check_failures: list[str] | None = None,
        check_warnings: list[str] | None = None,
        observed_artifacts: list[str] | None = None,
        blocked_by: list[str] | None = None,
        evidence: dict[str, Any] | None = None,
        message: str | None = None,
        check_obligations: list[dict[str, Any]] | None = None,
    ) -> None:
        failure_items = [str(item) for item in (check_failures or [])]
        warning_items = [str(item) for item in (check_warnings or [])]
        failures.extend(failure_items)
        warnings.extend(warning_items)
        observations.append(
            build_validation_observation(
                check_id=check_id,
                subject_key=context.ontology.name,
                stage=stage,
                failures=failure_items,
                warnings=warning_items,
                observed_artifacts=observed_artifacts,
                blocked_by=blocked_by,
                evidence=evidence,
                message=message,
            )
        )
        for obligation in check_obligations or []:
            subject = str(obligation.get("subject_key") or "").strip()
            if not subject:
                raise ValueError(f"{check_id} obligation requires subject_key")
            observations.append(
                build_validation_observation(
                    check_id=check_id,
                    subject_key=f"{context.ontology.name}/{subject}",
                    stage=stage,
                    failures=list(obligation.get("failures") or []),
                    warnings=list(obligation.get("warnings") or []),
                    observed_artifacts=list(
                        obligation.get("observed_artifacts") or observed_artifacts or []
                    ),
                    blocked_by=list(obligation.get("blocked_by") or []),
                    evidence=dict(obligation.get("evidence") or {}),
                    message=obligation.get("message"),
                )
            )

    if extra_failures:
        record(
            check_id="generation.external_failures",
            stage="precondition",
            check_failures=[str(item) for item in extra_failures],
            evidence={"source": "caller"},
        )
    if stage_mode:
        f, w, observed = _stage_artifact_contract_report(
            context, [Path(value).as_posix() for value in (active_artifacts or [])]
        )
        record(
            check_id="generation.stage_artifact_contract",
            stage="artifact",
            check_failures=f,
            check_warnings=w,
            observed_artifacts=observed,
            evidence={
                "active_artifacts": sorted(active_artifact_set),
                "fixed_om2_import_contract": (
                    "Use a package-relative import from ._fixed_om2_runtime; "
                    "do not import fixed_om2_runtime or om2.runtime.fixed."
                ),
                "required_import_example": (
                    "from ._fixed_om2_runtime import "
                    "find_or_create_om2_quantity_from_label"
                ),
                "entity_tool_naming_contract": (
                    "Every class-creation tool must be a module-scope callable named "
                    "exactly create_<class_local> and must be registered/exported."
                ),
                "relationship_tool_naming_contract": (
                    "Every object-property tool must be a module-scope callable named "
                    "exactly add_<predicate_local> and must be registered/exported."
                ),
            },
        )
    active_prompt_paths = [
        (Path(context.output_root) / relative)
        for relative in active_artifact_set
        if relative.endswith(".md")
    ]
    run_prompt_checks = bool(include_prompt_checks) or bool(active_prompt_paths)
    prompt_files = sorted(prompts_dir.glob("*.md")) if prompts_dir.is_dir() else []
    if run_prompt_checks and prompts_required and not prompt_files:
        record(
            check_id="generation.prompt_artifacts_required",
            stage="precondition",
            check_failures=[
                "Prompt enhancement requires existing prompt artifacts; prompt validation cannot be skipped"
            ],
            observed_artifacts=[str(prompts_dir)],
        )
    if run_prompt_checks and prompts_required and prompt_files and not stage_mode:
        # Import lazily because pure_llm_generation also uses this validator.
        # These projections are the same contracts and creator surface shown to
        # the generation agents, so the closure does not reconstruct a second
        # approximation of their capabilities.
        from src.agents.scripts_and_prompts_generation.pure_llm_generation import (
            _owned_entity_tool_contracts,
            _prompt_artifact_generation_contract,
        )

        prompt_contracts = {
            path.name: _prompt_artifact_generation_contract(context, path)
            for path in prompt_files
        }
        closure = compile_materialization_obligation_graph(
            context,
            prompt_generation_contracts=prompt_contracts,
            creator_surface=_owned_entity_tool_contracts(context),
        )
        closure_failures = materialization_closure_failures(closure)
        record(
            check_id="generation.materialization_closure",
            stage="prompt",
            check_failures=closure_failures,
            observed_artifacts=[
                *(str(path) for path in prompt_files),
                str(scripts_dir),
            ],
            evidence={
                "hard_gate": True,
                "schema_version": closure["schema_version"],
                "closure": closure,
            },
            message=(
                "Materialization closure found deterministic contradictions"
                if closure_failures
                else "Materialization closure passed"
            ),
        )

    checks = (
        ("generation.syntax", "syntax", _syntax_report, True),
        (
            "generation.operation_unit_consistency",
            "contract",
            _operation_unit_contract_report,
            True,
        ),
        ("generation.tool_surface", "static", _expected_tool_surface_report, True),
        (
            "generation.relationship_param_description",
            "static",
            _relationship_param_description_report,
            True,
        ),
        (
            "generation.ordered_member_contract",
            "contract",
            _ordered_member_contract_report,
            False,
        ),
        ("generation.ttl_export", "runtime", _ttl_export_report, True),
        ("generation.prompt_quality", "prompt", _prompt_quality_report, False),
        (
            "generation.prompt_tbox_fidelity",
            "prompt",
            _prompt_tbox_fidelity_report,
            True,
        ),
        (
            "generation.prompt_runtime_binding",
            "prompt",
            _prompt_runtime_binding_report,
            True,
        ),
        (
            "generation.prompt_tool_signature_alignment",
            "prompt",
            _prompt_tool_signature_alignment_report,
            True,
        ),
        (
            "generation.extension_kg_mode_a_handoff",
            "prompt",
            _extension_kg_mode_a_handoff_report,
            True,
        ),
        (
            "generation.iteration_prompt_schema_contract",
            "prompt",
            _iteration_prompt_schema_contract_report,
            True,
        ),
        (
            "generation.iter1_kg_prompt_execution_contract",
            "prompt",
            _iter1_kg_prompt_execution_contract_report,
            True,
        ),
        (
            "generation.kg_iteration_owned_scope",
            "prompt",
            _kg_iteration_owned_scope_report,
            True,
        ),
        (
            "generation.runtime_graph_hygiene",
            "runtime",
            _runtime_graph_hygiene_report,
            True,
        ),
    )
    stage_prompt_checks = {
        "generation.prompt_quality",
        "generation.prompt_tbox_fidelity",
        "generation.prompt_runtime_binding",
        "generation.prompt_tool_signature_alignment",
        "generation.extension_kg_mode_a_handoff",
        "generation.iteration_prompt_schema_contract",
        "generation.kg_iteration_owned_scope",
    }
    package_prompt_check_ids = {
        *stage_prompt_checks,
        "generation.iter1_kg_prompt_execution_contract",
    }
    for check_id, stage, fn, hard_gate in checks:
        if check_id in package_prompt_check_ids and not run_prompt_checks:
            observations.append(
                build_validation_observation(
                    check_id=check_id,
                    subject_key=context.ontology.name,
                    stage=stage,
                    blocked_by=["generation.prompt_stage_not_requested"],
                    observed_artifacts=[str(prompts_dir)],
                    evidence={"include_prompt_checks": False},
                )
            )
            continue
        if (
            stage_mode
            and check_id != "generation.syntax"
            and not (active_prompt_paths and check_id in stage_prompt_checks)
        ):
            observations.append(
                build_validation_observation(
                    check_id=check_id,
                    subject_key=context.ontology.name,
                    stage=stage,
                    blocked_by=["generation.stage_dependencies_incomplete"],
                    observed_artifacts=sorted(active_artifact_set),
                    evidence={"active_artifacts": sorted(active_artifact_set)},
                )
            )
            continue
        if fn in {
            _prompt_quality_report,
            _prompt_tbox_fidelity_report,
            _prompt_runtime_binding_report,
            _prompt_tool_signature_alignment_report,
            _extension_kg_mode_a_handoff_report,
            _iteration_prompt_schema_contract_report,
            _kg_iteration_owned_scope_report,
        } and stage_mode:
            result = fn(context, active_prompt_paths)
        else:
            if fn is _syntax_report:
                active_python_paths = (
                    [
                        scripts_dir / Path(relative).name
                        for relative in active_artifact_set
                        if relative.endswith(".py")
                    ]
                    if stage_mode
                    else None
                )
                result = fn(scripts_dir, active_python_paths)
            else:
                result = fn(context)
        f, w = result[:2]
        obligations = result[2] if len(result) > 2 else []
        effective_hard_gate = hard_gate or (
            stage_mode and check_id == "generation.prompt_quality"
        )
        if not effective_hard_gate and f:
            w = [
                *(f"Advisory {check_id}: {message}" for message in f),
                *w,
            ]
            f = []
            obligations = []
        record(
            check_id=check_id,
            stage=stage,
            check_failures=f,
            check_warnings=w,
            observed_artifacts=[
                str(prompts_dir) if stage == "prompt" else str(scripts_dir)
            ],
            check_obligations=obligations,
            evidence={"hard_gate": effective_hard_gate},
        )

    if stage_mode:
        observations.append(
            build_validation_observation(
                check_id="generation.import_smoke",
                subject_key=context.ontology.name,
                stage="runtime",
                blocked_by=["generation.stage_dependencies_incomplete"],
                observed_artifacts=sorted(active_artifact_set),
                evidence={"active_artifacts": sorted(active_artifact_set)},
            )
        )
    else:
        f, w, obligations = _import_report(context)
        record(
            check_id="generation.import_smoke",
            stage="runtime",
            check_failures=f,
            check_warnings=w,
            observed_artifacts=[str(scripts_dir / "main.py")],
            check_obligations=obligations,
        )

    if scripts_dir.exists() and not stage_mode:
        prompts_for_contract = (
            prompts_dir
            if run_prompt_checks
            and prompts_dir.exists()
            and list(prompts_dir.glob("*.md"))
            else None
        )
        contract_report = validate_generated_artifacts(
            scripts_dir=scripts_dir,
            prompts_dir=prompts_for_contract,
            contract_bundle=context.contract,
        )
        failures.extend(contract_report.get("failures") or [])
        warnings.extend(contract_report.get("warnings") or [])
        observations.extend(contract_report.get("observations") or [])
    elif not scripts_dir.exists():
        record(
            check_id="generation.contract_bundle",
            stage="contract",
            check_warnings=[
                "Contract validation skipped because scripts directory is missing"
            ],
            observed_artifacts=[str(scripts_dir)],
            blocked_by=["generation.scripts_missing"],
        )
    else:
        observations.append(
            build_validation_observation(
                check_id="generation.contract_bundle",
                subject_key=context.ontology.name,
                stage="contract",
                blocked_by=["generation.stage_dependencies_incomplete"],
                observed_artifacts=sorted(active_artifact_set),
                evidence={"active_artifacts": sorted(active_artifact_set)},
            )
        )

    if not stage_mode:
        f, w = _foreign_symbol_report(context, foreign_contracts)
        record(
            check_id="generation.foreign_symbols",
            stage="cross_ontology",
            check_failures=f,
            check_warnings=w,
            observed_artifacts=[str(scripts_dir), str(prompts_dir)],
            evidence={
                "foreign_ontologies": [
                    str(bundle.get("ontology_name") or "")
                    for bundle in (foreign_contracts or [])
                ]
            },
        )

    feedback = {
        "coding_agent": [
            msg for msg in failures if "Prompt" not in msg and "prompt" not in msg
        ],
        "prompt_agent": [
            msg
            for msg in failures
            if "Prompt" in msg or "prompt" in msg or "Foreign ontology symbols" in msg
        ],
    }
    report = {
        "ontology": context.ontology.name,
        "ok": not failures,
        "scripts_dir": context.scripts_dir,
        "prompts_dir": context.prompts_dir,
        "failures": failures,
        "warnings": warnings,
        "observations": observations,
        "stage_ok": not any(
            observation.get("status") == "fail"
            for observation in observations
            if observation.get("status") != "blocked"
        ),
        "active_artifacts": sorted(active_artifact_set),
        "feedback": feedback,
    }
    if write_report:
        report_path = Path(context.report_path)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        summary_path = Path(context.output_root) / "reports" / "summary.json"
        try:
            summary = (
                json.loads(summary_path.read_text(encoding="utf-8"))
                if summary_path.is_file()
                else {}
            )
        except (OSError, json.JSONDecodeError):
            summary = {}
        prior_reports = [
            item
            for item in (summary.get("reports") or [])
            if isinstance(item, dict)
            and str(item.get("ontology") or "") != context.ontology.name
        ]
        current_reports = [*prior_reports, report]
        summary = {
            **summary,
            "ok": all(bool(item.get("ok")) for item in current_reports),
            "output_root": str(context.output_root),
            "reports": current_reports,
        }
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    return report
