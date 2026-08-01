"""LLM semantic review of generated MCP capabilities using runtime evidence."""

from __future__ import annotations

import importlib.util
import inspect
import json
import sys
import types
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from src.agents.scripts_and_prompts_generation.agentic_generation_context import (
    AgenticGenerationContext,
)
from src.agents.scripts_and_prompts_generation.agentic_generation_validation import (
    _import_generated_main_module,
    _expected_tool_surface_report,
)
from src.agents.scripts_and_prompts_generation.level1_code_repair import invoke_json
from src.agents.scripts_and_prompts_generation.pure_llm_generation import (
    _artifact_generation_contract,
    _artifact_role_contract,
    _fixed_rdf_runtime_api_contract,
    _owned_entity_tool_contracts,
)


def _entity_behavior_evidence(
    path: Path,
    context: AgenticGenerationContext | None = None,
) -> dict[str, Any]:
    """Probe creator identity and rejection semantics for soft review evidence."""
    if not path.name.endswith("_creation_entities.py"):
        return {"applicable": False}
    package_name = f"_semantic_entity_probe_{uuid4().hex}"
    package = types.ModuleType(package_name)
    package.__path__ = [str(path.parent.resolve())]  # type: ignore[attr-defined]
    sys.modules[package_name] = package
    module_name = f"{package_name}.{path.stem}"
    runtime = None
    canonical_registry_key = ""
    probe_registry_key = ""
    try:
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ImportError("could not create module spec")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        source = path.read_text(encoding="utf-8")
        exec(compile(source, str(path), "exec"), module.__dict__)
        runtime = getattr(module, "rdf_runtime")
        canonical_registry_key = runtime._REGISTRY_KEY
        probe_registry_key = (
            f"{canonical_registry_key}::semantic-probe::{package_name}"
        )
        runtime._REGISTRY_KEY = probe_registry_key
        graph = runtime.reset_retained_graph()
        atomicity_evidence: dict[str, Any] = {"applicable": context is not None}
        if context is not None:
            from src.agents.scripts_and_prompts_generation.creator_atomicity import (
                probe_generated_creator_atomicity,
            )

            atomicity_evidence = probe_generated_creator_atomicity(
                module=module,
                runtime=runtime,
                creator_contracts=_owned_entity_tool_contracts(context),
            )
        creators = {
            name: value
            for name, value in vars(module).items()
            if name.startswith("create_")
            and name != "create_om2_quantity"
            and callable(value)
        }
        invalid_results: dict[str, Any] = {}
        creator = next(
            (
                value
                for value in creators.values()
                if "order" not in inspect.signature(value).parameters
            ),
            next(iter(creators.values()), None),
        )
        duplicate_reuse = False
        om2_bounded_behavior: dict[str, Any] = {"applicable": False}
        if creator is not None:
            signature = inspect.signature(creator)

            def invoke_creator(label: Any) -> Any:
                return (
                    creator(label, order=1)
                    if "order" in signature.parameters
                    else creator(label)
                )

            for case, value in {
                "empty": "",
                "whitespace": "   ",
                "none": None,
                "integer": 7,
            }.items():
                before = set(graph)
                try:
                    invoke_creator(value)
                except Exception as exc:
                    invalid_results[case] = {
                        "rejected": True,
                        "exception_type": type(exc).__name__,
                        "graph_unchanged": set(graph) == before,
                    }
                else:
                    invalid_results[case] = {
                        "rejected": False,
                        "graph_unchanged": set(graph) == before,
                    }
            first = json.loads(invoke_creator("Semantic identity probe"))
            second = json.loads(invoke_creator("  Semantic identity probe  "))
            duplicate_reuse = first.get("iri") == second.get("iri")
        om2_creator = getattr(module, "create_om2_quantity", None)
        if callable(om2_creator) and context is not None:
            allowed_classes = sorted(
                {
                    str(range_iri)
                    for item in (
                        (
                            context.contract.get("ontology_publish_contract")
                            or {}
                        ).get("object_properties")
                        or []
                    )
                    for range_iri in item.get("range_iris") or []
                    if "ontology-of-units-of-measure.org/resource/om-2/"
                    in str(range_iri)
                }
            )
            invalid_class = "https://example.invalid/NotAllowed"
            before = set(graph)
            invalid_raw = om2_creator(invalid_class, "Semantic invalid OM-2 probe")
            invalid_result = (
                json.loads(invalid_raw) if isinstance(invalid_raw, str) else invalid_raw
            )
            om2_bounded_behavior = {
                "applicable": True,
                "tbox_allowed_class_iris": allowed_classes,
                "invalid_class_iri": invalid_class,
                "invalid_result": invalid_result,
                "invalid_rejected": isinstance(invalid_result, Mapping)
                and invalid_result.get("status") in {"rejected", "error"},
                "graph_unchanged_on_invalid_class": set(graph) == before,
            }
        return {
            "applicable": True,
            "import_ok": True,
            "public_creators": sorted(creators),
            "creator_atomicity": atomicity_evidence,
            "om2_creator_present": callable(
                getattr(module, "create_om2_quantity", None)
            ),
            "shared_retained_graph": graph is runtime.retained_graph(),
            "invalid_label_results": invalid_results,
            "normalized_duplicate_reuses_iri": duplicate_reuse,
            "om2_bounded_behavior": om2_bounded_behavior,
        }
    except Exception as exc:
        return {
            "applicable": True,
            "import_ok": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
    finally:
        if runtime is not None and canonical_registry_key:
            runtime._graph_registry().pop(probe_registry_key, None)
            runtime._REGISTRY_KEY = canonical_registry_key
        for loaded_name in list(sys.modules):
            if loaded_name == package_name or loaded_name.startswith(package_name + "."):
                sys.modules.pop(loaded_name, None)


def _callable_evidence(value: Any) -> dict[str, Any]:
    callable_value = getattr(value, "fn", value)
    try:
        signature = str(inspect.signature(callable_value))
    except (TypeError, ValueError):
        signature = "<unavailable>"
    try:
        source = inspect.getsource(callable_value)
    except (OSError, TypeError):
        source = ""
    return {
        "signature": signature,
        "docstring": inspect.getdoc(callable_value) or "",
        "module": str(getattr(callable_value, "__module__", "")),
        "qualname": str(getattr(callable_value, "__qualname__", "")),
        "source": source,
        "capability_provenance": getattr(
            callable_value, "__mcp_capability_provenance__", None
        ),
    }


def collect_mcp_semantic_evidence(
    context: AgenticGenerationContext,
) -> dict[str, Any]:
    """Collect facts for semantic review without classifying tool capabilities."""
    hard_failures, hard_warnings, _ = _expected_tool_surface_report(context)
    module = _import_generated_main_module(
        Path(context.scripts_dir), context.ontology.name
    )
    registry = getattr(module, "mcp", None)
    getter = getattr(registry, "get_tools", None)
    inventory = {}
    if callable(getter):
        import asyncio

        inventory = asyncio.run(getter())
    tools: dict[str, Any] = {}
    if isinstance(inventory, Mapping):
        tools = {
            str(name): _callable_evidence(value)
            for name, value in sorted(inventory.items(), key=lambda item: str(item[0]))
        }
    sources = {
        path.name: path.read_text(encoding="utf-8", errors="replace")
        for path in sorted(Path(context.scripts_dir).glob("*.py"))
        if not path.name.startswith("_fixed_")
    }
    entity_paths = sorted(Path(context.scripts_dir).glob("*_creation_entities.py"))
    bounded_capability_probes = (
        _entity_behavior_evidence(entity_paths[0], context)
        if len(entity_paths) == 1
        else {
            "applicable": False,
            "error": "Expected exactly one generated entities module",
        }
    )
    return {
        "ontology": context.ontology.name,
        "hard_code_validation": {
            "ok": not hard_failures,
            "failures": hard_failures,
            "warnings": hard_warnings,
        },
        "runtime_tool_inventory": tools,
        "bounded_capability_probes": bounded_capability_probes,
        "tbox_generation_contract": context.contract,
        "generated_sources": sources,
    }


def review_mcp_semantics_with_llm(
    *,
    context: AgenticGenerationContext,
    model_name: str,
) -> dict[str, Any]:
    """Ask an independent LLM to classify semantic capability exposure."""
    evidence = collect_mcp_semantic_evidence(context)
    prompt = (
        "You are an independent semantic security reviewer for a generated ontology MCP. "
        "Determine whether any Agent-visible tool grants a caller generic RDF mutation "
        "power instead of a T-Box-bounded capability. Do not classify by function name, "
        "identifier substring, naming convention, or a blacklist. A suspicious name is "
        "not evidence and a harmless name is not safety evidence. Reason from the actual "
        "runtime registry, callable signatures and implementations, provenance, data flow, "
        "and the supplied T-Box contract.\n\n"
        "Treat bounded_capability_probes as authoritative runtime evidence. A caller-selected "
        "parameter remains T-Box-bounded when probes demonstrate that values outside the "
        "T-Box-derived allowed set return structured rejection without graph mutation; do not "
        "require duplicate allowlist logic in a public wrapper. "
        "A tool is generic when the caller can select an arbitrary RDF class, predicate, "
        "datatype predicate, or equivalent unconstrained mutation target beyond one "
        "T-Box-authorized bound capability. Lifecycle/read/export tools are not mutation "
        "capabilities merely because they handle graph state. For each critical finding, "
        "identify the exact caller-controlled parameter, how it reaches mutation, why the "
        "T-Box does not bound it, and which generated artifact should be repaired. If the "
        "evidence is insufficient, request repair/review rather than inferring safety from "
        "names. Return JSON only with this schema:\n"
        '{"decision":"pass|repair","summary":"...",'
        '"critical_errors":[{"tool":"...","capability":"...",'
        '"caller_controlled_inputs":["..."],"data_flow_evidence":["..."],'
        '"tbox_boundary_evidence":["..."],"repair_targets":["exact generated filename"]}],'
        '"noncritical_observations":["..."],"confidence":0.0}\n\n'
        + json.dumps(evidence, ensure_ascii=False)
    )
    response = invoke_json(
        model_name,
        prompt,
        timeout_seconds=300,
        max_attempts=3,
        provider_max_retries=0,
    )
    review = dict(response.data)
    if review.get("decision") not in {"pass", "repair"}:
        raise ValueError("Semantic script reviewer returned an invalid decision")
    errors = review.get("critical_errors")
    if not isinstance(errors, list):
        raise ValueError("Semantic script reviewer must return critical_errors as a list")
    if review["decision"] == "pass" and errors:
        raise ValueError("Semantic script reviewer pass cannot contain critical errors")
    if review["decision"] == "repair" and not errors:
        raise ValueError("Semantic script reviewer repair requires critical errors")
    review["evidence"] = evidence
    return review


def review_generated_artifact_semantics_with_llm(
    *,
    context: AgenticGenerationContext,
    artifact_path: str | Path,
    model_name: str,
) -> dict[str, Any]:
    """Review one generated artifact against its role, fixed API, and T-Box."""
    path = Path(artifact_path)
    entity_paths = sorted(path.parent.glob("*_creation_entities.py"))
    behavior_probe_path = (
        entity_paths[0]
        if path.name == "main.py" and len(entity_paths) == 1
        else path
    )
    is_prompt = path.suffix == ".md"
    evidence = {
        "artifact": path.name,
        "artifact_source": path.read_text(encoding="utf-8", errors="replace"),
        "artifact_role_contract": _artifact_role_contract(path),
        "owned_entity_tool_contracts": (
            {} if is_prompt else _owned_entity_tool_contracts(context)
        ),
        "fixed_runtime_api": (
            {} if is_prompt else _fixed_rdf_runtime_api_contract()
        ),
        "tbox_generation_contract": _artifact_generation_contract(context, path),
        "artifact_lifecycle_state": (
            "runtime_template_not_executed" if path.suffix == ".md" else "generated_code"
        ),
        "framework_contract": {
            "fastmcp_parameter_schema": (
                "For public relationship tools, typing.Annotated and the real "
                "pydantic.Field are required framework dependencies used by FastMCP to publish "
                "range-aware parameter schemas. Their use is authorized and must not be treated "
                "as an undeclared dependency or replaced with a local fallback shim."
            ),
        },
        "runtime_behavior_probes": (
            {} if is_prompt else _entity_behavior_evidence(behavior_probe_path, context)
        ),
        "read_only_dependency_sources": {
            dependency.name: dependency.read_text(
                encoding="utf-8", errors="replace"
            )
            for dependency in ([] if is_prompt else sorted(path.parent.glob("*.py")))
            if dependency.is_file()
            and dependency != path
            and not dependency.name.startswith("main_part_")
        },
    }
    prompt = (
        "You are the independent soft-semantic reviewer for one generated ontology "
        "pipeline artifact. Compare the implementation with the supplied artifact role, "
        "the exact fixed-runtime API, its read-only dependencies, and the T-Box-derived "
        "contract. Do not judge by keyword presence, identifier names, formatting, or "
        "preferred code style. Determine whether the code's actual behavior and ownership "
        "match its layer. Generic domain-independent behavior already implemented by the "
        "fixed runtime should be called, not guessed or reimplemented. Domain-bound behavior "
        "must occur only in the layer that owns it. A pass requires no critical semantic or "
        "architectural errors. Treat dependencies explicitly authorized by framework_contract "
        "as part of the production environment. Noncritical redundancy may be reported without "
        "forcing repair. Treat runtime_behavior_probes as authoritative behavioral evidence. "
        "For entity ownership, treat owned_entity_tool_contracts as the exhaustive authority; "
        "do not infer ownership from namespace, comments, or incomplete dependency source. "
        "For entity creators, repair is required unless invalid labels are rejected before "
        "mutation, surrounding whitespace is normalized, duplicate exact-class normalized-label "
        "calls reuse one IRI, and all creators share the retained graph. Treat each "
        "runtime_behavior_probes.creator_atomicity creator result as authoritative: every valid "
        "complex call must write its supplied datatype values, while invalid input must leave the "
        "graph unchanged for both new and reused entities. "
        "The public `create_om2_quantity` callable is fixed infrastructure: it must be imported "
        "directly from `._fixed_rdf_runtime`, retain `_fixed_rdf_runtime` provenance, and must "
        "not be locally wrapped or reimplemented in the generated entity module. "
        "Datatype-property completeness is an independent mandatory semantic dimension for the "
        "entity-tools module and main.py. Read every datatype property in the supplied T-Box "
        "contract, including all domains, the subclass closure, and its range datatype. Every "
        "direct domain class and every subclass of a declared domain "
        "`create_<class>` must expose that property directly as an explicitly typed input and "
        "must route a supplied value to the exact bound writer from "
        "package_datatype_capabilities(). Non-ordering datatype inputs are optional; the ordering "
        "property remains the ordered creator's required atomic `order` input. Separate public "
        "`set_<property>` tools are forbidden because datatype writes are creator-owned. When "
        "reviewing main.py, verify that the creators carrying these inputs—not separate setters—"
        "are present in the runtime registry. Optional properties are not optional capabilities: "
        "omitting a creator input for any applicable domain is a critical completeness error. "
        "For each missing or invalid path, report the exact datatype property, domain creator, "
        "expected range/Python type, and artifact to repair. Do not use keyword presence as proof; "
        "reason from signatures, registrations, and implementation data flow. "
        "For a Markdown runtime prompt template, evaluate its instructions and placeholders, "
        "not whether it already contains runtime extraction or A-Box instances. A template must "
        "not pre-populate fixture entities, quantities, source quotations, ordered members, or "
        "links; missing runtime instances before source injection is correct and never a repair "
        "reason. It must implement only the supplied iteration_spec responsibility and use only "
        "the supplied tbox_scope; repeating the ontology-wide extraction task is a critical scope "
        "error. Extraction semantics are format-independent: requiring canonical JSON, fixed "
        "section keys, or one serialization syntax is a critical contract error. Every "
        "domain-specific trigger, example, exclusion, disambiguation rule, and scientific "
        "interpretation must have direct support in tbox_scope; plausible but unsupported domain "
        "knowledge is a critical provenance error. "
        "For KG_BUILDING_ITER_1, hints need only contain source-supported root labels: requiring "
        "each hint to repeat the T-Box-projected root class is a critical interface error. The "
        "prompt must not invent or hardcode a runtime scope name because the orchestrator supplies "
        "that policy at runtime, and it must not enumerate domain-specific non-root examples even "
        "as exclusions. It must accept the generic upstream `<type-prefix>-<index> [<label>]` "
        "text wrapper by extracting only the bracketed label, and it must use the exact "
        "tbox_scope.top_entity.creator_tool rather than guessing among create_* tools. The "
        "generated runtime prompt must render that exact creator as a concrete tool name because "
        "the runtime agent does not receive the generation-contract object; leaving a symbolic "
        "`tbox_scope...` reference is a critical executability error. Equal normalized labels "
        "must be deduplicated before creator invocation. Generic exclusions must not name optional "
        "domain extensions or ontology-specific technologies. "
        "Return JSON only:\n"
        '{"decision":"pass|repair","summary":"...",'
        '"critical_errors":[{"finding":"...","behavioral_evidence":["..."],'
        '"contract_evidence":["..."],"repair_target":"exact filename"}],'
        '"noncritical_observations":["..."],"confidence":0.0}\n\n'
        + json.dumps(evidence, ensure_ascii=False)
    )
    response = invoke_json(
        model_name,
        prompt,
        timeout_seconds=300,
        max_attempts=3,
        provider_max_retries=0,
    )
    review = dict(response.data)
    decision = review.get("decision")
    errors = review.get("critical_errors")
    if decision not in {"pass", "repair"}:
        raise ValueError("Artifact semantic reviewer returned an invalid decision")
    if not isinstance(errors, list):
        raise ValueError("Artifact semantic reviewer requires critical_errors as a list")
    if decision == "pass" and errors:
        raise ValueError("Artifact semantic pass cannot contain critical errors")
    if decision == "repair" and not errors:
        raise ValueError("Artifact semantic repair requires critical errors")
    review["evidence"] = evidence
    return review


def review_generated_prompt_semantics_with_llm(
    *,
    context: AgenticGenerationContext,
    artifact_path: str | Path,
    model_name: str,
) -> dict[str, Any]:
    """Review one runtime prompt semantically without phrase or keyword gates."""
    path = Path(artifact_path)
    if path.suffix != ".md":
        raise ValueError("Prompt semantic reviewer requires a Markdown artifact")
    return review_generated_artifact_semantics_with_llm(
        context=context,
        artifact_path=path,
        model_name=model_name,
    )
