"""LLM semantic review of generated MCP capabilities using runtime evidence."""

from __future__ import annotations

import importlib.util
import inspect
import json
import os
import re
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
    build_validation_report,
)
from src.agents.scripts_and_prompts_generation.artifact_surface_contract import (
    LIFECYCLE_TOOL_NAMES,
)
from src.agents.scripts_and_prompts_generation.level1_code_repair import (
    _env_int,
    _generation_timeout_disabled,
    invoke_json,
)


def _paired_review_timeout_seconds() -> int | None:
    """Paired review bundles full prompt pairs; allow longer than patch/edit calls."""
    if _generation_timeout_disabled():
        return None
    configured = os.environ.get("TWA_PAIRED_REVIEW_TIMEOUT", "").strip()
    if configured:
        value = _env_int("TWA_PAIRED_REVIEW_TIMEOUT", 0)
        return value if value > 0 else None
    return _env_int("TWA_PAIRED_REVIEW_TIMEOUT", 1800)
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
        creator_contracts: list[dict[str, Any]] = []
        if context is not None:
            from src.agents.scripts_and_prompts_generation.creator_atomicity import (
                creator_call_recipe,
                probe_generated_creator_atomicity,
            )

            creator_contracts = _owned_entity_tool_contracts(context)
            atomicity_evidence = probe_generated_creator_atomicity(
                module=module,
                runtime=runtime,
                creator_contracts=creator_contracts,
            )
        creators = {
            name: value
            for name, value in vars(module).items()
            if name.startswith("create_")
            and name != "create_om2_quantity"
            and callable(value)
        }
        invalid_results: dict[str, Any] = {}
        creator_contract = next(
            (
                contract
                for contract in creator_contracts
                if str(contract.get("public_tool") or "") in creators
            ),
            None,
        )
        creator = (
            creators.get(str(creator_contract.get("public_tool") or ""))
            if creator_contract is not None
            else next(iter(creators.values()), None)
        )
        duplicate_reuse = False
        om2_bounded_behavior: dict[str, Any] = {"applicable": False}
        if creator is not None:
            def invoke_creator(label: Any) -> Any:
                if creator_contract is None:
                    return creator(label)
                recipe = creator_call_recipe(
                    creator_contract,
                    creator,
                    label=label,
                    include_optional_datatypes=False,
                )
                return creator(*recipe["args"], **recipe["kwargs"])

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
            try:
                first = json.loads(invoke_creator("Semantic identity probe"))
                second = json.loads(invoke_creator("  Semantic identity probe  "))
                duplicate_reuse = first.get("iri") == second.get("iri")
            except Exception:
                duplicate_reuse = False
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
        "calls reuse one IRI, and all creators share the retained graph. "
        "Python parameter names are chosen by the generated script and need not equal the "
        "T-Box local name. Judge whether each T-Box datatype and ordering property is bound "
        "to some public parameter and written to its exact property IRI; do not require "
        "`order`, `hasOrder`, or any other prescribed identifier. "
        "Treat runtime_behavior_probes.creator_atomicity as observational evidence, including "
        "parameter_bindings and unbound properties. A name mismatch is not itself a defect. "
        "If a probe supplied a bound value, that value must be written; invalid supplied "
        "input must leave the graph unchanged. Unbound properties must be judged from the "
        "signature and implementation data flow, not from missing mechanical kwargs. "
        "When a creator contract contains required_edges, treat it as one composite atomic "
        "operation: every projected existing-IRI, dependent-label, and dependent-datatype input "
        "must be public; every declared owner/dependent/membership mutation must occur inside one "
        "rollback boundary; and failure at any point must leave the graph unchanged. Predicates "
        "listed as merged are intentionally absent from the standalone relationship module and "
        "must not be reported missing or reintroduced there. "
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
        "property remains one required integer input on each ordered creator, under whatever "
        "public name the generated signature chose. Separate public "
        "`set_<property>` tools are forbidden because datatype writes are creator-owned. When "
        "reviewing main.py, verify that the creators carrying these inputs—not separate setters—"
        "are present in the runtime registry. Optional properties are not optional capabilities: "
        "omitting a creator input for any applicable domain is a critical completeness error. "
        "For each missing or invalid path, report the exact datatype property, domain creator, "
        "expected range/Python type, and artifact to repair. Do not use keyword presence as proof; "
        "reason from signatures, registrations, and implementation data flow. "
        "For relationship tools, judge Field(description) text semantically: subject and object "
        "must be absolute IRIs rather than labels, and the description must identify the "
        "applicable domain and range classes. Do not require any exact English phrase. "
        "For a Markdown runtime prompt template, evaluate its instructions and placeholders, "
        "not whether it already contains runtime extraction or A-Box instances. A template must "
        "not pre-populate fixture entities, quantities, source quotations, ordered members, or "
        "links; missing runtime instances before source injection is correct and never a repair "
        "reason. It must implement only the supplied iteration_spec responsibility and use only "
        "the supplied tbox_scope; repeating the ontology-wide extraction task is a critical scope "
        "error. When generation_contract.agent_tool_contract is present, require every operative "
        "tool instruction to use its exact tool and parameter names; generic creator/relation/"
        "lifecycle descriptions are not executable. When semantic_scalar_output_contract is "
        "present, require every scalar's complete source-grounded value as a standalone "
        "key-value line under the owning occurrence; prose must not be the sole carrier "
        "for semantic-text.v1. "
        "For an extraction prompt whose supplied artifact role requires the "
        "ref-entity-relations.v1 interchange contract, reason from the complete instructions: "
        "the runtime output must be one authoritative object whose only top-level collections "
        "are entities and relations; entity identity, class, canonical label, datatype payload, "
        "and relation endpoints must remain separated as specified by the role contract. A "
        "second records, evidence-accounting, label-transport, wrapper, or replacement-anchor "
        "output is a critical interface error. Merely mentioning one of those concepts while "
        "explicitly prohibiting it is compliant and must not be treated as evidence that the "
        "prompt authorizes it. Likewise, determine workflow ownership, missing-reference "
        "behavior, and reuse restrictions from the operative instruction as a whole rather than "
        "from isolated words or phrases. Every domain-specific trigger, example, exclusion, "
        "disambiguation rule, and scientific interpretation must have direct support in "
        "tbox_scope; plausible but unsupported domain knowledge is a critical provenance error. "
        + (
            "For a KG_BUILDING_ITER_N_ONEPASS fragment, iteration_spec defines the fragment's "
            "focused positive semantic contribution, not a global execution boundary. Require "
            "the fragment to preserve its T-Box semantics and non-lifecycle tool contracts while "
            "omitting init_memory/export_memory, this-iteration-only scope, later-iteration "
            "deferral, ordered-hint ignore rules, prohibitions on creators owned by sibling "
            "iterations, and independent completion/success/failure declarations. Lifecycle, "
            "union scope, and final completion belong exclusively to the outer combined prompt. "
            "Treat `use only the tools listed above`, `do not introduce classes or properties "
            "beyond this fragment`, and equivalent wording as critical global-scope leakage, "
            "not as harmless focus language. "
            if re.fullmatch(r"KG_BUILDING_ITER_\d+_ONEPASS\.md", path.name)
            else ""
        )
        + (
            "For an extension KG prompt, runtime_binding_contract is authoritative. The prompt "
            "must reuse the inherited scoped root identified by tbox_scope and must never create "
            "or retype it. Enrichment-target IRIs are pipeline-bound identities; init_memory "
            "already types them in scoped memory. The prompt must not mint a replacement IRI "
            "for a bound target class. Calling the matching create_* tool is allowed only when "
            "it adopts the bound IRI. It must materialize only extension-focus facts supported "
            "by the source and extension T-Box, then export through the extension MCP lifecycle. "
            "Mode A consumes the extraction JSON through the declared source-content slot "
            "(`paper_content`); demanding a main-ontology hints slot is a reviewer error, not "
            "a prompt defect. Do not apply main-ontology KG_BUILDING_ITER_1 hint-wrapper or "
            "root-creator requirements. "
            if context.ontology.role == "extension"
            and path.name.startswith("KG_BUILDING_ITER_")
            else (
                "For an extension extraction prompt, entity_label and entity_uri identify the "
                "inherited upstream scope, while tbox_scope.extension_focus identifies the class "
                "to extract. Treating the inherited entity itself as an instance of the extension "
                "focus, limiting output to that one entity, or failing to allow multiple relevant "
                "extension-focus instances is a critical scope error. Paper content and the T-Box "
                "arrive through wrapper channels and must not appear as runtime placeholders. "
                if context.ontology.role == "extension"
                and path.name.startswith("EXTRACTION_")
                else (
                "For KG_BUILDING_ITER_1, the pipeline-seeded identity lock/dossier is the sole "
                "root-identity authority. The prompt must call init_memory with the fixed public "
                "signature, bind every hinted root label to its exact locked URI, attach only "
                "source-supported active-T-Box facts when applicable, and call export_memory with "
                "the same fixed signature as the final action. Requiring or invoking any top-root "
                "creator, minting a replacement for a missing lock entry, or retyping a locked "
                "root is a critical interface error. The prompt must not invent or hardcode a "
                "runtime scope name because the orchestrator supplies that policy at runtime, and "
                "it must not enumerate domain-specific non-root examples even as exclusions. It "
                "must accept the generic upstream `<type-prefix>-<index> [<label>]` text wrapper "
                "by extracting only the bracketed label. Generic exclusions must not name optional "
                "domain extensions or ontology-specific technologies. "
                if path.name == "KG_BUILDING_ITER_1.md"
                else ""
                )
            )
        )
        + "Return JSON only:\n"
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


def _validate_paired_materialization_review(value: Any) -> dict[str, Any]:
    """Validate the paired-review response as a closed JSON schema."""
    if not isinstance(value, Mapping):
        raise ValueError("Paired materialization review must be a JSON object")
    required = {
        "decision",
        "summary",
        "critical_errors",
        "noncritical_observations",
        "confidence",
    }
    if set(value) != required:
        raise ValueError(
            "Paired materialization review returned unexpected keys: "
            f"{sorted(set(value) ^ required)}"
        )
    decision = value.get("decision")
    errors = value.get("critical_errors")
    observations = value.get("noncritical_observations")
    confidence = value.get("confidence")
    if decision not in {"pass", "repair"}:
        raise ValueError("Paired materialization review decision must be pass or repair")
    if not isinstance(value.get("summary"), str):
        raise ValueError("Paired materialization review summary must be a string")
    if not isinstance(errors, list) or not isinstance(observations, list):
        raise ValueError("Paired materialization review lists have invalid types")
    if not all(isinstance(item, str) for item in observations):
        raise ValueError("Paired materialization observations must be strings")
    finding_keys = {
        "finding",
        "iteration",
        "evidence",
        "expected_behavior",
        "contract_evidence",
        "repair_targets",
    }
    for item in errors:
        if not isinstance(item, Mapping) or set(item) != finding_keys:
            raise ValueError("Paired materialization finding violates its JSON schema")
        if not isinstance(item.get("finding"), str):
            raise ValueError("Paired materialization finding must be a string")
        if not isinstance(item.get("iteration"), str):
            raise ValueError("Paired materialization iteration must be a string")
        if not isinstance(item.get("evidence"), list) or not all(
            isinstance(entry, str) for entry in item.get("evidence") or []
        ):
            raise ValueError("Paired materialization evidence must be a string list")
        if not isinstance(item.get("expected_behavior"), str):
            raise ValueError(
                "Paired materialization expected_behavior must be a string"
            )
        if not isinstance(item.get("contract_evidence"), list) or not all(
            isinstance(entry, str)
            for entry in item.get("contract_evidence") or []
        ):
            raise ValueError(
                "Paired materialization contract_evidence must be a string list"
            )
        if not isinstance(item.get("repair_targets"), list) or not all(
            isinstance(entry, str) for entry in item.get("repair_targets") or []
        ):
            raise ValueError("Paired materialization repair_targets must be a string list")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise ValueError("Paired materialization confidence must be numeric")
    if not 0.0 <= float(confidence) <= 1.0:
        raise ValueError("Paired materialization confidence must be between zero and one")
    if decision == "pass" and errors:
        raise ValueError("Paired materialization pass cannot contain critical errors")
    if decision == "repair" and not errors:
        raise ValueError("Paired materialization repair requires critical errors")
    return dict(value)


def _paired_authoritative_contract_evidence(
    context: AgenticGenerationContext,
) -> dict[str, Any]:
    """Expose the exact Agent-visible contracts needed for paired prompt review."""
    from src.agents.scripts_and_prompts_generation import fixed_rdf_runtime

    lifecycle = []
    for name in LIFECYCLE_TOOL_NAMES:
        value = getattr(fixed_rdf_runtime, name)
        lifecycle.append(
            {
                "name": name,
                "signature": str(inspect.signature(value)),
                "docstring": inspect.getdoc(value) or "",
            }
        )
    return {
        "surface_policy": "closed_world_exact_agent_visible_contract",
        "lifecycle_tools": lifecycle,
        "datatype_write_policy": {
            "creator_owned_inputs_only": True,
            "public_setter_tools_forbidden": True,
            "forbidden_tool_shape": "set_<datatype_property>",
        },
        "scoped_contract_location": (
            "prompt_generation_contracts.<filename> contains the exact T-Box, "
            "creator, relationship, check, lifecycle, and interchange surface "
            "shown to that prompt's generator"
        ),
    }


def _paired_prompt_contract_projection(contract: Mapping[str, Any]) -> dict[str, Any]:
    """Keep only fields used to judge one extraction/KG materialization path."""
    iteration = contract.get("iteration_spec") or {}
    tbox_scope = contract.get("tbox_scope") or {}
    reuse_policy = contract.get("reuse_policy") or {}
    return {
        "prompt_artifact": contract.get("prompt_artifact"),
        "iteration_spec": {
            key: iteration.get(key)
            for key in (
                "iteration_number",
                "name",
                "description",
                "hint_representation",
                "linked_materialization_classes",
                "responsibilities",
            )
            if key in iteration
        },
        "iteration_owned_scope": contract.get("iteration_owned_scope") or {},
        "tbox_scope": {
            key: tbox_scope.get(key)
            for key in (
                "classes",
                "properties",
                "top_entity",
                "pipeline_selected_top_entity",
                "pipeline_top_entity_semantics",
                "inherited_scoped_root",
                "extension_focus",
            )
            if key in tbox_scope
        },
        "subclass_decision_contract": (
            contract.get("subclass_decision_contract") or {}
        ),
        "relationship_target_contracts": {
            local: {
                key: spec.get(key)
                for key in (
                    "predicate_iri",
                    "domain_locals",
                    "range_locals",
                    "materialization_target_locals",
                    "target_handling",
                    "creator_tools",
                    "fixed_runtime_range_iris",
                )
                if key in spec
            }
            for local, spec in (
                contract.get("relationship_target_contracts") or {}
            ).items()
        },
        "agent_tool_contract": contract.get("agent_tool_contract") or {},
        "semantic_scalar_output_contract": (
            contract.get("semantic_scalar_output_contract") or []
        ),
        "lexical_quantity_hint_contract": (
            contract.get("lexical_quantity_hint_contract") or {}
        ),
        "required_links": contract.get("required_links") or [],
        "pipeline_required_link_contracts": (
            contract.get("pipeline_required_link_contracts") or []
        ),
        "reuse_policy": {
            "authorized_checks": [
                {
                    key: item.get(key)
                    for key in (
                        "class_local",
                        "public_tool",
                        "lookup_scope",
                        "reuse_scope",
                        "match_basis",
                    )
                    if key in item
                }
                for item in reuse_policy.get("authorized_checks") or []
            ],
            "rules": list(reuse_policy.get("rules") or []),
        },
        "runtime_binding_contract": contract.get("runtime_binding_contract") or {},
        "representation_policy": contract.get("representation_policy") or {},
        "evidence_accounting_contract": (
            contract.get("evidence_accounting_contract") or {}
        ),
    }


def _paired_closure_projection(closure: Mapping[str, Any]) -> dict[str, Any]:
    """Project deterministic closure without repeating full package contracts."""
    return {
        "schema_version": closure.get("schema_version"),
        "ok": closure.get("ok"),
        "contradictions": list(closure.get("contradictions") or []),
        "obligations": [
            {
                key: item.get(key)
                for key in (
                    "id",
                    "iteration",
                    "kind",
                    "source",
                    "class_local",
                    "predicate_local",
                    "creator_tool",
                    "datatype_input",
                    "input_requirement",
                    "required_when",
                    "materialization_paths",
                    "transitive_contract_path",
                )
                if key in item
            }
            for item in closure.get("obligations") or []
        ],
    }


_PAIRED_OBJECT_PROPERTY_PATH_CHECKLIST = (
    "MANDATORY OBJECT-PROPERTY PATH CHECKLIST (domain-neutral; run for every "
    "EXTRACTION_ITER_N / KG_BUILDING_ITER_N pair before deciding pass):\n"
    "1) Enumerate every owned object property from that iteration's "
    "prompt_generation_contracts.<EXTRACTION|KG>.relationship_target_contracts. "
    "Also include every pipeline_required_link_contracts entry owned by the KG side. "
    "Empty owned classes does not waive this checklist.\n"
    "2) For each enumerated property P with range class R and creator_tools C, choose "
    "exactly one authorized handoff mode from the contracts below. If none fits, "
    "decision=repair.\n"
    "   Mode A — extraction-emitted range entity: EXTRACTION must authorize emitting an "
    "entity of class R (or a concrete subclass required by the contract) with a stable "
    "ref, and must emit a relation for P whose object_ref resolves to that entity. KG "
    "must consume that ref and call an exact tool from C, or reuse an exact prior IRI.\n"
    "   Mode B — lexical quantity interchange: allowed only when P appears in that "
    "iteration's lexical_quantity_hint_contract.properties. Match the iteration "
    "hint_representation exactly: for semantic-text.v1, EXTRACTION must preserve the "
    "complete source lexical value as a standalone `P: <lexeme>` line under the owning "
    "occurrence and must not invent a quantity object/ref or JSON "
    "datatype_properties field; KG must recover that same lexeme from the key-value line and call "
    "the exact fixed/runtime creator from C before asserting P. For "
    "ref-entity-relations.v1, EXTRACTION must preserve the lexeme under "
    "datatype_properties[P] and KG must read that field before calling the creator. Topic "
    "mention of P without this lexical→creator bridge is incomplete.\n"
    "   Mode C — pipeline-owned prior identity: allowed only when a "
    "pipeline_required_link_contract or equivalent deterministic identity path supplies R. "
    "EXTRACTION may omit R. KG must execute the deterministic identity/create/link path "
    "and must not wait for EXTRACTION to invent R.\n"
    "   Mode D — prior-iteration identity only: EXTRACTION/KG may omit creating R only when "
    "the operative instructions require an exact prior ref/IRI already present in "
    "accumulated_hints or retained memory, and forbid substitution. Missing prior identity "
    "must become an upstream blocker, not silent omission of a supported source fact.\n"
    "3) Fail closed on these contradictions for the chosen mode:\n"
    "   - EXTRACTION forbids minting/creating R, or forbids emitting P unless an object ref "
    "already exists, while the same iteration still depends on Mode A and provides no Mode "
    "B/C/D bridge.\n"
    "   - EXTRACTION stores P only as a free-text/datatype field while P is absent from "
    "lexical_quantity_hint_contract (illegal reclassification of an object property).\n"
    "   - lexical_quantity_hint_contract lists P (Mode B), but KG never instructs reading "
    "the representation-correct lexical interchange (semantic-text occurrence prose or "
    "datatype_properties[P]) and instead expects an EXTRACTION-emitted quantity "
    "entity/ref that EXTRACTION was told not to mint.\n"
    "   - KG prompt requires JSON datatype_properties interchange while the same iteration's "
    "hint_representation is semantic-text.v1, or vice versa.\n"
    "   - KG exposes no exact tool from C for a property that is not Mode C/D, including "
    "omitting a creator that appears in agent_tool_contract.creator_tools or "
    "linked_materialization_classes.\n"
    "   - materialization_closure.contradictions=[] is not proof that EXTRACTION↔KG prose "
    "implements a complete handoff; closure only certifies that a package creator path "
    "exists. You must still execute this checklist against operative prompt prose.\n"
    "4) Every checklist failure is a critical_error. Name P, R, the intended mode, the "
    "broken EXTRACTION and/or KG instruction, and the expected repair. Do not pass on "
    "topic-level mentions of P, tool-surface completeness alone, or lifecycle-only fixes.\n"
)


def review_paired_prompt_materialization_with_llm(
    *,
    context: AgenticGenerationContext,
    model_name: str,
    closure_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Review EXTRACTION/KG pairs against validator and closure evidence."""
    from src.agents.scripts_and_prompts_generation.materialization_closure import (
        compile_materialization_obligation_graph,
        derive_creator_surface,
    )
    from src.agents.scripts_and_prompts_generation.pure_llm_generation import (
        _prompt_artifact_generation_contract,
    )

    from src.agents.scripts_and_prompts_generation.extension_prompt_contract import (
        ensure_extension_kg_mode_a_handoff_file,
        sanitize_paired_extension_handoff_review,
    )

    prompts_dir = Path(context.prompts_dir)
    if context.ontology.role == "extension":
        for path in prompts_dir.glob("KG_BUILDING_ITER_*.md"):
            ensure_extension_kg_mode_a_handoff_file(path)
    prompt_paths = sorted(
        path
        for path in prompts_dir.glob("*.md")
        if not re.fullmatch(r"KG_BUILDING_ITER_\d+_ONEPASS\.md", path.name)
    )
    prompt_contracts = {
        path.name: _prompt_artifact_generation_contract(context, path)
        for path in prompt_paths
    }
    closure = closure_report or compile_materialization_obligation_graph(
        context,
        prompt_generation_contracts=prompt_contracts,
        creator_surface=derive_creator_surface(context),
    )
    prompt_sources = {
        path.name: path.read_text(encoding="utf-8", errors="replace")
        for path in prompt_paths
        if path.name.startswith(("EXTRACTION_ITER_", "KG_BUILDING_ITER_"))
    }
    validation = build_validation_report(
        context,
        write_report=False,
        prompts_required=False,
        include_prompt_checks=True,
        active_artifacts=[
            path.resolve()
            .relative_to(Path(context.output_root).resolve())
            .as_posix()
            for path in prompt_paths
        ],
    )
    evidence = {
        "prompt_pairs": prompt_sources,
        "prompt_generation_contracts": {
            name: _paired_prompt_contract_projection(contract)
            for name, contract in prompt_contracts.items()
            if name.startswith(("EXTRACTION_ITER_", "KG_BUILDING_ITER_"))
        },
        "validator_evidence": {
            "failures": list(validation.get("failures") or []),
            "observations": [
                {
                    "check_id": item.get("check_id"),
                    "subject_key": item.get("subject_key"),
                    "stage": item.get("stage"),
                    "status": item.get("status"),
                    "message": item.get("message"),
                    "failures": list(
                        (item.get("evidence") or {}).get("failures") or []
                    ),
                }
                for item in validation.get("observations") or []
                if item.get("stage") in {"prompt", "contract"}
            ],
        },
        "materialization_closure": _paired_closure_projection(closure),
        "authoritative_agent_tool_and_tbox_surface": (
            _paired_authoritative_contract_evidence(context)
        ),
        "mandatory_object_property_path_checklist": (
            _PAIRED_OBJECT_PROPERTY_PATH_CHECKLIST
        ),
    }
    prompt = (
        "You are the paired semantic reviewer for generated runtime prompts. Review each "
        "EXTRACTION_ITER_N and KG_BUILDING_ITER_N pair together with validator evidence and "
        "the per-iteration materialization closure. Optional source facts do not require an "
        "instance to exist. They do require a complete capability path when evidence is found: "
        "extraction must preserve enough identity/class/relation information and KG instructions "
        "must permit the exact generated creator, fixed OM-2 creator, or prior-identity path. "
        "Check that required validator links can be completed and that prohibited classes are "
        "never demanded. Treat authoritative_agent_tool_and_tbox_surface as closed-world truth. "
        + _PAIRED_OBJECT_PROPERTY_PATH_CHECKLIST
        + "For KG_BUILDING_ITER_1, top_entities are mechanically injected from Extraction 1, "
        "the T-Box supplies their class, and the identity lock supplies their IRIs. The authored "
        "prompt must use the mechanically appended orchestrator-owned shared memory scope for "
        "top_level_entity_name; hardcoding the top class or an extracted entity label as that "
        "lifecycle argument is a critical error. "
        "For an extension KG prompt, Mode A is the declared source-content slot carrying "
        "ref-entity-relations.v1. Demanding a main-ontology hints placeholder is invalid. "
        "A pipeline_required_link_contract is an authoritative deterministic identity path: "
        "the KG prompt must execute it even when extraction hints do not repeat its target, and "
        "the extraction prompt must not be faulted for omitting that pipeline-owned target. "
        "For a main EXTRACTION prompt, accumulated_hints are facts from completed earlier "
        "iterations, not an existing output for the current iteration; treating the main pass "
        "as enrichment-only or forbidding it to add/retype current-iteration occurrences is a "
        "critical semantic defect unless iteration_spec explicitly describes a sub-iteration. "
        "Any lifecycle name other than the exact listed lifecycle tool names is nonexistent and "
        "is a critical error; legacy aliases, including init_or_resume_scoped_memory and "
        "export_retained_memory, are not acceptable. Datatype writes are creator-owned inputs, "
        "so any instructed set_<datatype_property> tool is nonexistent and is a critical error. "
        "For step-local target creation, evaluate every concrete source subclass separately "
        "against its own T-Box class comment and parent contract. Permission for one subclass to "
        "introduce a named target must never be generalized to a sibling whose comment permits "
        "only an existing target. For every ordered creator, trace the T-Box ordering datatype "
        "from source extraction hints into the required creator input. Also trace every explicit "
        "optional datatype fact (for example a boolean state) from source hints into the same "
        "creator call when present. Merely narrating a scalar while prohibiting its emission, or "
        "forbidding datatype payloads in KG instructions, breaks that path and is critical. "
        "Compare operative prompt prose against each scoped agent_tool_contract.creator_tools "
        "and relationship_target_contracts; do not pass on topic-level mentions alone. For every KG "
        "prompt, its prompt_generation_contracts.<filename>.agent_tool_contract is the exact API "
        "shown to the generator. Require explicit init_memory and export_memory calls and every "
        "invoked creator/check/relationship tool by its exact name and exact parameter names. "
        "A creator entry marked atomic_operation owns every listed required_edge: the prompt must "
        "pass all projected parameters in that one call and must not instruct a second creator or "
        "relationship call for those effects. "
        "Generic descriptions such as creator tool, relation-add tool, open memory, or export "
        "retained memory are critical executable-interface omissions. A creator datatype input "
        "must be passed directly by the generated public parameter name shown in "
        "agent_tool_contract; a nested `creator_input.*` namespace is invalid. For "
        "semantic-text.v1 extraction, require every semantic_scalar_output_contract entry's "
        "complete source-grounded value as a standalone property-local key-value line under "
        "the owning occurrence. A topic-only mention without the value is "
        "not a complete extraction-to-creator interchange path. Judge relationship "
        "Field(description) text semantically: callers must understand that endpoints are "
        "absolute IRIs rather than labels, and which domain/range classes apply. Do not "
        "require any exact English phrase. "
        "materialization_closure.contradictions are authoritative deterministic "
        "evidence: every such contradiction requires decision=repair and must be represented in "
        "critical_errors; never override or dismiss one. Each critical error must state both the "
        "observed defect and the exact expected behavior, and contract_evidence must contain only "
        "the direct T-Box/tool/creator facts needed to repair that defect. Return JSON only with "
        "exactly this schema:\n"
        '{"decision":"pass|repair","summary":"...",'
        '"critical_errors":[{"finding":"...","iteration":"...",'
        '"evidence":["..."],"expected_behavior":"...",'
        '"contract_evidence":["..."],'
        '"repair_targets":["exact prompt filename(s)"]}],'
        '"noncritical_observations":["..."],"confidence":0.0}\n\n'
        + json.dumps(evidence, ensure_ascii=False)
    )
    response = invoke_json(
        model_name,
        prompt,
        timeout_seconds=_paired_review_timeout_seconds(),
        max_attempts=3,
        provider_max_retries=0,
    )
    review = sanitize_paired_extension_handoff_review(
        _validate_paired_materialization_review(response.data),
        is_extension=context.ontology.role == "extension",
    )

    deterministic = list(closure.get("contradictions") or [])
    represented = "\n".join(
        str(item.get("finding") or "") for item in review["critical_errors"]
    ).casefold()
    for contradiction in deterministic:
        code = str(contradiction.get("code") or "")
        message = str(contradiction.get("message") or "")
        if code.casefold() in represented or message.casefold() in represented:
            continue
        review["critical_errors"].append(
            {
                "finding": f"{code}: {message}",
                "iteration": str(contradiction.get("iteration") or ""),
                "evidence": [
                    "Authoritative deterministic materialization_closure contradiction",
                    json.dumps(contradiction.get("evidence") or {}, ensure_ascii=False),
                ],
                "expected_behavior": (
                    "The extraction-to-KG path must satisfy this structured "
                    "materialization obligation."
                ),
                "contract_evidence": [
                    f"{code}: {message}",
                ],
                "repair_targets": [
                    f"EXTRACTION_ITER_{contradiction.get('iteration')}.md",
                    f"KG_BUILDING_ITER_{contradiction.get('iteration')}.md",
                ],
            }
        )
    if deterministic:
        review["decision"] = "repair"
        review["summary"] = (
            "Deterministic materialization contradictions require repair. "
            + review["summary"]
        )
    return review


def review_paired_prompt_finding_with_llm(
    *,
    model_name: str,
    finding: Mapping[str, Any],
    target_sources: Mapping[str, str],
    accepted_findings: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Judge one paired-prompt finding without re-running the global review."""
    evidence = {
        "current_finding": {
            "finding": str(finding.get("finding") or ""),
            "iteration": str(finding.get("iteration") or ""),
            "evidence": list(finding.get("evidence") or []),
            "expected_behavior": str(finding.get("expected_behavior") or ""),
            "contract_evidence": list(finding.get("contract_evidence") or []),
            "repair_targets": list(finding.get("repair_targets") or []),
        },
        "current_target_sources": dict(target_sources),
        "accepted_findings": [
            {
                "finding": str(item.get("finding") or ""),
                "expected_behavior": str(item.get("expected_behavior") or ""),
                "decision": "resolved",
            }
            for item in accepted_findings
        ],
    }
    prompt = (
        "You are the focused semantic judge for exactly one previously reported paired-prompt "
        "finding. Judge only whether current_finding is now resolved in the supplied current "
        "target sources. Do not perform a new global review and do not introduce unrelated "
        "findings. Use expected_behavior and contract_evidence as the authoritative correction. "
        "Previously accepted findings are preservation constraints. Return JSON only with exactly "
        "this schema:\n"
        '{"decision":"resolved|repair","summary":"...",'
        '"unresolved_reasons":["..."],"confidence":0.0}\n\n'
        + json.dumps(evidence, ensure_ascii=False)
    )
    response = invoke_json(
        model_name,
        prompt,
        timeout_seconds=_paired_review_timeout_seconds(),
        max_attempts=3,
        provider_max_retries=0,
    )
    result = dict(response.data)
    if set(result) != {
        "decision",
        "summary",
        "unresolved_reasons",
        "confidence",
    }:
        raise ValueError("Focused paired finding review violates its JSON schema")
    if result.get("decision") not in {"resolved", "repair"}:
        raise ValueError("Focused paired finding review has an invalid decision")
    if not isinstance(result.get("summary"), str):
        raise ValueError("Focused paired finding review summary must be a string")
    reasons = result.get("unresolved_reasons")
    if not isinstance(reasons, list) or not all(
        isinstance(item, str) for item in reasons
    ):
        raise ValueError(
            "Focused paired finding review unresolved_reasons must be strings"
        )
    if result["decision"] == "resolved" and reasons:
        raise ValueError("Resolved focused paired finding cannot have unresolved reasons")
    if result["decision"] == "repair" and not reasons:
        raise ValueError("Focused paired finding repair requires unresolved reasons")
    confidence = result.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise ValueError("Focused paired finding confidence must be numeric")
    if not 0.0 <= float(confidence) <= 1.0:
        raise ValueError(
            "Focused paired finding confidence must be between zero and one"
        )
    return result
