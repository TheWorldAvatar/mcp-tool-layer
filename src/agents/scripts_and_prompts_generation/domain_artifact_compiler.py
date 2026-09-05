from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from rdflib import Graph, URIRef
from rdflib.namespace import OWL, RDF, RDFS

from src.agents.scripts_and_prompts_generation.agentic_generation_context import (
    AgenticGenerationContext,
    build_agentic_generation_context,
    runtime_publish_contract,
)
from src.agents.scripts_and_prompts_generation.domain_generation_config import (
    DomainGenerationConfig,
    load_domain_generation_config,
)
from src.agents.scripts_and_prompts_generation.domain_semantic_planner import (
    JsonPlanner,
    plan_domain_semantics,
)
from src.agents.scripts_and_prompts_generation.iteration_plan_compiler import (
    compile_iteration_plan,
)
from src.agents.scripts_and_prompts_generation.ttl_parser import (
    parse_ontology_ttl,
)
from src.agents.scripts_and_prompts_generation.reuse_policy import (
    attach_reuse_policy,
)
from src.agents.scripts_and_prompts_generation.occurrence_surface_units import (
    collect_extension_bridge_class_iris,
)
from src.agents.scripts_and_prompts_generation.tbox_runtime_contracts import (
    derive_ordered_member_contracts,
    derive_required_link_bindings,
)
from models.MCPConfig import load_mcp_set_extraction_validation


def _json_digest(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _tbox_bundle_contract(
    config: DomainGenerationConfig,
) -> dict[str, Any]:
    """Describe a primary/supporting bundle without local-name keyed merging."""
    declared = json.loads(config.path.read_text(encoding="utf-8")).get("tbox") or {}
    declared_primary = str(declared.get("primary") or "").strip()
    declared_supporting = [
        str(value).strip()
        for value in declared.get("supporting") or []
        if str(value).strip()
    ]

    def entry(path: Path, *, role: str, configured_path: str) -> dict[str, Any]:
        graph = Graph()
        graph.parse(str(path), format="turtle")
        class_iris = {
            str(node)
            for class_type in (OWL.Class, RDFS.Class)
            for node in graph.subjects(RDF.type, class_type)
            if isinstance(node, URIRef)
        }
        object_property_iris = {
            str(node)
            for node in graph.subjects(RDF.type, OWL.ObjectProperty)
            if isinstance(node, URIRef)
        }
        datatype_property_iris = {
            str(node)
            for node in graph.subjects(RDF.type, OWL.DatatypeProperty)
            if isinstance(node, URIRef)
        }
        return {
            "role": role,
            "configured_path": configured_path,
            "resolved_path": str(path.resolve()),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "iri_inventory": {
                "class_iris": sorted(class_iris),
                "object_property_iris": sorted(object_property_iris),
                "datatype_property_iris": sorted(datatype_property_iris),
            },
        }

    primary = entry(
        config.primary_tbox,
        role="primary",
        configured_path=declared_primary
        or _portable_tbox_path(config, config.primary_tbox),
    )
    supporting = [
        entry(
            path,
            role="supporting",
            configured_path=(
                declared_supporting[index]
                if index < len(declared_supporting)
                else _portable_tbox_path(config, path)
            ),
        )
        for index, path in enumerate(config.supporting_tboxes)
    ]
    return {
        "schema_version": "iri-aware-tbox-bundle.v1",
        "identity_key": "absolute_iri",
        "local_name_merge": False,
        "primary_semantic_authority": True,
        "primary": primary,
        "supporting": supporting,
        "reasoner_paths": [
            primary["resolved_path"],
            *(item["resolved_path"] for item in supporting),
        ],
    }


def _attach_tbox_bundle(
    context: AgenticGenerationContext,
    bundle: dict[str, Any],
) -> None:
    context.contract["tbox_bundle"] = bundle
    layers = context.contract.setdefault("contract_layers", {})
    layers["tbox_bundle"] = {
        "source": "domain_config_primary_plus_supporting",
        "identity_key": "absolute_iri",
        "local_name_merge": False,
        "generation_inventory": "primary",
        "reasoner_inventory": "primary_plus_supporting",
    }


def _apply_top_entity_decision(
    contract: dict[str, Any], decision: dict[str, Any]
) -> None:
    """Keep the full and runtime publish contracts on one accepted top entity."""
    top_entity = dict(decision)
    contract["top_entity"] = top_entity
    publish_contract = dict(contract.get("ontology_publish_contract") or {})
    publish_contract["top_entity"] = dict(top_entity)
    contract["ontology_publish_contract"] = publish_contract


def _domain_role(config: DomainGenerationConfig) -> str:
    return str((config.runtime.get("binding") or {}).get("role") or "main").strip()


def _portable_tbox_path(
    config: DomainGenerationConfig,
    path: Path,
    *,
    declared_path: str = "",
) -> str:
    """Keep repository-owned T-Box references portable across workspaces."""
    declared = Path(str(declared_path).strip()) if str(declared_path).strip() else None
    if declared is not None and not declared.is_absolute():
        return declared.as_posix()
    repository_root = config.path.parents[2]
    try:
        return path.resolve().relative_to(repository_root.resolve()).as_posix()
    except ValueError:
        return str(path)


def _runtime_blueprint(
    config: DomainGenerationConfig, decisions: dict[str, Any]
) -> dict[str, Any]:
    """Combine deterministic ownership with config-owned pipeline wiring."""
    semantic_iterations = (decisions.get("iteration_decomposition") or {}).get(
        "iterations"
    ) or []
    runtime_slots = list((config.runtime.get("workflow") or {}).get("iterations") or [])
    profile_slots = list(config.profile.get("slots") or [])
    if not (
        len(runtime_slots) == len(semantic_iterations) == len(profile_slots)
    ):
        raise ValueError(
            "domain config workflow iteration slots must match the selected workflow profile"
        )

    default_mcp = config.mcp_capabilities.get("kg_building") or {}
    external_mcp = config.mcp_capabilities.get("external_enrichment") or {}
    runtime_model = config.models.get("runtime_extraction", "gpt-5")
    compiled: list[dict[str, Any]] = []
    for index, (semantic, runtime, profile_slot) in enumerate(
        zip(semantic_iterations, runtime_slots, profile_slots, strict=True), start=2
    ):
        if not all(
            isinstance(value, dict) for value in (semantic, runtime, profile_slot)
        ):
            raise ValueError(
                "semantic, runtime, and profile iteration entries must be objects"
            )
        planned_number = int(semantic.get("iteration_number") or index)
        if planned_number != index:
            raise ValueError(
                f"iteration planner must number main iterations contiguously from 2; got {planned_number}"
            )
        iteration = {
            "profile_slot": str(semantic.get("profile_slot") or ""),
            "slot_kind": str(semantic.get("slot_kind") or ""),
            "iteration_number": index,
            "name": str(semantic.get("name") or f"iteration_{index}"),
            "description": str(semantic.get("description") or ""),
            "responsibilities": dict(semantic.get("responsibilities") or {}),
            "model_config_key": str(
                runtime.get("model_config_key") or f"model:{runtime_model}"
            ),
            "per_entity": True,
            "use_agent": bool(runtime.get("use_agent", False)),
            "inputs": dict(runtime.get("inputs") or {"source": "stitched_paper"}),
            "outputs": {
                "hints_file": f"mcp_run/iter{index}_hints_{{entity_safe}}.txt",
                "prompt_file": f"prompts/iter{index}_extraction/{{entity_safe}}.md",
                "response_file": f"responses/iter{index}_extraction/{{entity_safe}}.md",
                **dict(runtime.get("additional_outputs") or {}),
            },
            "mcp_set_name": str(default_mcp.get("set_name") or "run_created_mcp.json"),
            "mcp_tools": list(default_mcp.get("tools") or ["llm_created_mcp"]),
        }
        if str(runtime.get("hint_representation") or "").strip():
            iteration["hint_representation"] = str(runtime["hint_representation"]).strip()
        elif str(profile_slot.get("hint_representation") or "").strip():
            iteration["hint_representation"] = str(
                profile_slot.get("hint_representation") or ""
            ).strip()
        if iteration["use_agent"] and external_mcp:
            iteration["extraction_mcp_set_name"] = str(
                external_mcp.get("set_name") or ""
            )
            iteration["extraction_mcp_tools"] = list(external_mcp.get("tools") or [])
            mcp_validation = load_mcp_set_extraction_validation(
                iteration["extraction_mcp_set_name"]
            )
            if mcp_validation:
                iteration["extraction_validation"] = mcp_validation

        semantic_hint_mode = (
            iteration.get("hint_representation") == "semantic-text.v1"
        )
        if "enrichment_focus" in semantic or "sub_iterations" in semantic:
            raise ValueError(
                f"iteration {index} semantic plan must contain main slots only"
            )
        profile_enrichment_slots = list(
            profile_slot.get("semantic_enrichment_slots") or []
        )
        runtime_enrichment_slots = runtime.get("enrichment") or []
        if not isinstance(runtime_enrichment_slots, list):
            raise ValueError(f"iteration {index} runtime enrichment must be an array")
        if len(runtime_enrichment_slots) != len(profile_enrichment_slots):
            raise ValueError(
                f"iteration {index} runtime enrichment does not match semantic profile slots"
            )
        if profile_enrichment_slots:
            raise ValueError(
                f"iteration {index} domain semantic profile must contain main slots only"
            )
        if semantic.get("requires_pre_extraction"):
            iteration["has_pre_extraction"] = True
            iteration["pre_extraction_model_key"] = str(
                runtime.get("pre_extraction_model_key") or f"model:{runtime_model}"
            )
            if isinstance(runtime.get("pre_extraction_validation"), dict):
                iteration["pre_extraction_validation"] = dict(
                    runtime.get("pre_extraction_validation") or {}
                )
            iteration["inputs"] = {
                "pre_extraction_source": "stitched_paper",
                "extraction_source": "pre_extracted_text",
            }
            iteration["outputs"].update(
                {
                    "pre_extraction_file": "pre_extraction/entity_text_{entity_safe}.txt",
                    "pre_extraction_prompt_file": f"prompts/iter{index}_pre_extraction/{{entity_safe}}.md",
                    "pre_extraction_response_file": f"responses/iter{index}_pre_extraction/{{entity_safe}}.md",
                }
            )
        if (
            str(profile_slot.get("slot_kind") or "") == "ordered"
            and semantic_hint_mode
            and not profile_enrichment_slots
            and not bool(
                (
                    (iteration.get("pre_extraction_validation") or {}).get(
                        "closed_ledger"
                    )
                    or {}
                ).get("enabled")
            )
        ):
            raise ValueError(
                f"iteration {index} semantic ordered slot without enrichment requires "
                "closed-ledger pre-extraction validation"
            )
        compiled.append(iteration)
    return {
        "ontology": config.ontology_name,
        "description": "",
        "iterations": compiled,
    }


def _legacy_adapter(
    config: DomainGenerationConfig,
    *,
    blueprint_path: Path | None,
    top_entity: dict[str, Any] | None = None,
    extension_focus: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Render the legacy shape required by current pipeline consumers."""
    output = config.runtime.get("output") or {}
    binding = config.runtime.get("binding") or {}
    declared_config = json.loads(config.path.read_text(encoding="utf-8"))
    declared_primary = str(
        ((declared_config.get("tbox") or {}).get("primary") or "")
    ).strip()
    role = _domain_role(config)
    if role not in {"main", "extension"}:
        raise ValueError("runtime.binding.role must be 'main' or 'extension'")

    runtime_policies: dict[str, Any] = {}
    ordered_member_contracts = derive_ordered_member_contracts(config.primary_tbox)
    if ordered_member_contracts:
        runtime_policies["ordered_member_contracts"] = [
            dict(item)
            for item in ordered_member_contracts
            if isinstance(item, dict)
        ]
    if blueprint_path is not None:
        runtime_policies["iteration_plan"] = {
            "iterations_blueprint_path": str(blueprint_path)
        }
    if role == "main" and top_entity:
        top_local = str(top_entity.get("class_local") or "").strip()
        if not top_local:
            raise ValueError("compiled main top entity requires class_local")
        runtime_policies.update(
            {
                "top_entity_extraction": {"count_lines_starting_with": [top_local]},
                "iter1_top_entity_kg": {
                    "prompt_rules": {"top_level_entity_name": top_local}
                },
            }
        )
    if role == "extension" and config.runtime.get("enrichment_target") is not None:
        from src.agents.scripts_and_prompts_generation.enrichment_target_sparql import (
            generated_enrichment_target_relative,
            validate_enrichment_target_declaration,
        )

        policy = validate_enrichment_target_declaration(
            config.runtime.get("enrichment_target"),
            prefix="runtime.enrichment_target",
        )
        focus_iri = str((extension_focus or {}).get("class_iri") or "").strip()
        if focus_iri:
            policy["target_class_iri"] = focus_iri
            policy["query_file"] = generated_enrichment_target_relative(
                config.ontology_name
            )
        runtime_policies["enrichment_target"] = policy

    ontology_entry = {
        "name": config.ontology_name,
        "description": config.domain_id,
        "ttl_file": _portable_tbox_path(
            config,
            config.primary_tbox,
            declared_path=declared_primary,
        ),
        "complex_pipeline": config.workflow_profile == "complex",
        "runtime_policies": runtime_policies,
        "output": {
            "dir": str(output.get("dir") or "{ontology_name}_output"),
            "top_ttl_name": str(output.get("top_ttl_name") or "top.ttl"),
            "entity_ttl_pattern": str(
                output.get("entity_ttl_pattern") or "{entity_safe}.ttl"
            ),
        },
        "mcp_set_name": str(
            (config.mcp_capabilities.get("kg_building") or {}).get("set_name")
            or "run_created_mcp.json"
        ),
        "mcp_list": list(
            (config.mcp_capabilities.get("kg_building") or {}).get("tools")
            or ["llm_created_mcp"]
        ),
    }
    if role == "main":
        extensions: list[dict[str, Any]] = []
        for index, raw_extension in enumerate(config.runtime.get("extensions") or []):
            if not isinstance(raw_extension, dict):
                raise ValueError(f"runtime.extensions[{index}] must be an object")
            extension_name = str(raw_extension.get("name") or "").strip()
            declared_ttl = str(raw_extension.get("ttl_file") or "").strip()
            if not extension_name or not declared_ttl:
                raise ValueError(
                    f"runtime.extensions[{index}] requires name and ttl_file"
                )
            extension_path = Path(declared_ttl)
            if not extension_path.is_absolute():
                extension_path = (config.path.parents[2] / extension_path).resolve()
            if extension_path not in config.supporting_tboxes:
                raise ValueError(
                    f"runtime.extensions[{index}].ttl_file must be declared "
                    "in tbox.supporting"
                )
            extension_output = raw_extension.get("output") or {}
            extensions.append(
                {
                    "name": extension_name,
                    "description": str(
                        raw_extension.get("description") or extension_name
                    ),
                    "ttl_file": _portable_tbox_path(
                        config,
                        extension_path,
                        declared_path=declared_ttl,
                    ),
                    "complex_pipeline": bool(
                        raw_extension.get("complex_pipeline", False)
                    ),
                    "output": {
                        "dir": str(
                            extension_output.get("dir") or "{ontology_name}_output"
                        ),
                        "entity_ttl_pattern": str(
                            extension_output.get("entity_ttl_pattern")
                            or "{entity_slugified}.ttl"
                        ),
                    },
                    "mcp_set_name": str(
                        raw_extension.get("mcp_set_name") or "extension.json"
                    ),
                    "mcp_list": [
                        str(value)
                        for value in raw_extension.get("mcp_list") or []
                        if str(value)
                    ],
                    "agent_model": str(raw_extension.get("agent_model") or "gpt-4o"),
                }
            )
        return {
            "ontologies": {
                "main": ontology_entry,
                "extensions": extensions,
            }
        }

    upstream_name = str(binding.get("upstream_ontology") or "").strip()
    upstream_tbox = str(binding.get("upstream_tbox") or "").strip()
    if not upstream_name or not upstream_tbox:
        raise ValueError(
            "extension domains require runtime.binding.upstream_ontology "
            "and runtime.binding.upstream_tbox"
        )
    upstream_path = Path(upstream_tbox)
    if not upstream_path.is_absolute():
        upstream_path = (config.path.parents[2] / upstream_path).resolve()
    if not upstream_path.is_file():
        raise FileNotFoundError(f"upstream T-Box not found: {upstream_path}")
    if upstream_path not in config.supporting_tboxes:
        raise ValueError(
            "runtime.binding.upstream_tbox must also be declared in tbox.supporting"
        )
    return {
        "ontologies": {
            "main": {
                "name": upstream_name,
                "description": f"Runtime parent for {config.domain_id}",
                "ttl_file": _portable_tbox_path(
                    config,
                    upstream_path,
                    declared_path=upstream_tbox,
                ),
                "complex_pipeline": True,
            },
            "extensions": [ontology_entry],
        }
    }


def build_domain_generation_context(
    *,
    domain_config_path: str | Path,
    output_root: str | Path,
    repository_root: str | Path,
    write_files: bool = True,
    planner: JsonPlanner | None = None,
    operation_mode: str = "legacy",
    operation_planner: JsonPlanner | None = None,
    derived_reuse_policy_path: str | Path | None = None,
    selected_top_entity: dict[str, Any] | None = None,
) -> AgenticGenerationContext:
    """Build generation context from exactly T-Box bundle plus domain config."""
    config = load_domain_generation_config(
        domain_config_path, repository_root=repository_root
    )
    if operation_mode not in {"legacy", "inferred_atomic", "occurrence_surface"}:
        raise ValueError(
            "operation_mode must be 'legacy', 'inferred_atomic', or 'occurrence_surface'"
        )
    effective_reuse_policy_path = (
        Path(derived_reuse_policy_path).resolve()
        if derived_reuse_policy_path is not None
        else config.reuse_policy_path
    )
    root = Path(output_root)
    derived = root / "derived_inputs" / config.ontology_name
    derived.mkdir(parents=True, exist_ok=True)
    adapter_path = derived / "meta_task_adapter.json"
    adapter_path.write_text(
        json.dumps(
            _legacy_adapter(config, blueprint_path=None),
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    tbox_bundle = _tbox_bundle_contract(config)
    base = build_agentic_generation_context(
        ontology_name=config.ontology_name,
        meta_task_config_path=adapter_path,
        output_root=root,
        write_files=False,
    )
    _attach_tbox_bundle(base, tbox_bundle)
    planning_reuse_policy = (
        attach_reuse_policy(base.contract, effective_reuse_policy_path)
        if effective_reuse_policy_path is not None
        else None
    )
    role = _domain_role(config)
    decisions = plan_domain_semantics(
        config=config,
        parsed=base.parsed,
        contract=base.contract,
        planner=planner,
        planning_dir=root / "semantic_planning" / config.ontology_name,
        top_entity_owner="downstream" if role == "extension" else "iteration1",
        selected_root=selected_top_entity,
    )
    extension_focus = dict(decisions["top_entity"]) if role == "extension" else {}
    if role == "extension":
        binding = config.runtime.get("binding") or {}
        upstream_tbox = Path(str(binding.get("upstream_tbox") or ""))
        if not upstream_tbox.is_absolute():
            upstream_tbox = (Path(repository_root) / upstream_tbox).resolve()
        upstream_scope = binding.get("upstream_scope") or {}
        upstream_parsed = parse_ontology_ttl(str(upstream_tbox))
        upstream_local = str(upstream_scope.get("class_local") or "").strip()
        upstream_class = (upstream_parsed.get("classes") or {}).get(
            upstream_local
        ) or {}
        if not upstream_class.get("iri"):
            raise ValueError(
                "simple_extension upstream_scope.class_local is absent from upstream_tbox: "
                + upstream_local
            )
        inherited_top = {
            "status": "known",
            "class_local": upstream_local,
            "class_iri": str(upstream_class["iri"]),
            "rationale": "Declared upstream scope for extension runtime binding.",
            "evidence": [upstream_local],
        }
        inherited_top = {
            **inherited_top,
            "source": "domain_config_upstream_scope",
            "inherited_from_ontology": str(
                upstream_scope.get("ontology") or binding.get("upstream_ontology") or ""
            ),
            "entity_source": str(upstream_scope.get("entity_source") or ""),
            "owned_by_extension": False,
            "main_pass_reuses_scoped_root": True,
        }
        decisions["extension_focus"] = extension_focus
        decisions["inherited_top_entity"] = inherited_top
        decisions["top_entity"] = inherited_top
        _apply_top_entity_decision(base.contract, inherited_top)
        base.contract["extension_focus"] = extension_focus
        base.contract["ontology_publish_contract"]["extension_focus"] = dict(
            extension_focus
        )
    else:
        _apply_top_entity_decision(base.contract, decisions["top_entity"])
    runtime_blueprint = _runtime_blueprint(config, decisions)
    plan = compile_iteration_plan(
        blueprint=runtime_blueprint,
        parsed=base.parsed,
        contract=base.contract,
        ontology_name=config.ontology_name,
        blueprint_provenance={
            "source": "deterministic_tbox_iteration_ownership",
            "model": "deterministic",
            "sha256": _json_digest(decisions["iteration_decomposition"]),
            "ownership_sha256": str(
                (decisions.get("assignments") or {}).get(
                    "ownership_sha256"
                )
                or ""
            ),
        },
    )
    blueprint_path = derived / "iteration_blueprint.json"
    blueprint_path.write_text(
        json.dumps(runtime_blueprint, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    adapter_path.write_text(
        json.dumps(
            _legacy_adapter(
                config,
                blueprint_path=Path("iteration_blueprint.json"),
                top_entity=decisions["top_entity"],
                extension_focus=extension_focus if role == "extension" else None,
            ),
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    context = build_agentic_generation_context(
        ontology_name=config.ontology_name,
        meta_task_config_path=adapter_path,
        output_root=root,
        write_files=write_files,
    )
    _attach_tbox_bundle(context, tbox_bundle)
    reuse_policy = (
        attach_reuse_policy(context.contract, effective_reuse_policy_path)
        if effective_reuse_policy_path is not None
        else planning_reuse_policy
    )
    if role == "extension":
        _apply_top_entity_decision(context.contract, decisions["inherited_top_entity"])
        context.contract["extension_focus"] = extension_focus
        context.contract["ontology_publish_contract"]["extension_focus"] = dict(
            extension_focus
        )
    else:
        _apply_top_entity_decision(context.contract, decisions["top_entity"])
    context.contract["runtime_required_link_bindings"] = derive_required_link_bindings(
        tbox_path=config.primary_tbox,
        top_entity=decisions.get("top_entity"),
        external_identity_bindings=config.runtime.get("external_identity_bindings"),
    )
    context.contract["extension_bridge_class_iris"] = (
        collect_extension_bridge_class_iris(runtime=config.runtime)
    )
    context = replace(
        context,
        contract=context.contract,
        iteration_blueprint=plan,
        config_provenance={
            **context.config_provenance,
            "domain_config": {
                "source": "manual_domain_runtime_input",
                "path": str(config.path),
                "sha256": hashlib.sha256(config.path.read_bytes()).hexdigest(),
                "execution_profile": config.execution_profile,
                "execution_channel": config.execution_channel,
                "pipeline_iteration_number": (
                    (config.runtime.get("workflow") or {}).get(
                        "pipeline_iteration_number"
                    )
                ),
            },
            "semantic_decisions": {
                "source": "gpt_top_entity_plus_deterministic_ownership",
                "sha256": _json_digest(decisions),
                "top_entity_source": str(
                    (decisions.get("top_entity") or {}).get("source") or ""
                ),
                "iteration_ownership_source": (
                    "deterministic_tbox_iteration_ownership"
                ),
                "iteration_ownership_sha256": str(
                    (decisions.get("assignments") or {}).get(
                        "ownership_sha256"
                    )
                    or ""
                ),
            },
            "reuse_policy": (
                {
                    "source": "reviewed_llm_binary_reuse_decisions",
                    "path": str(effective_reuse_policy_path),
                    "sha256": reuse_policy["source_sha256"],
                }
                if reuse_policy is not None
                else None
            ),
            "tbox_bundle": tbox_bundle,
            "boundary": {
                "manual_inputs": ["tbox_bundle", "domain_config"],
                "top_entity": (
                    "gpt-5_supporting_tbox_inherited_scope"
                    if role == "extension"
                    else "gpt-5_active_tbox"
                ),
                "extension_focus": (
                    "gpt-5_active_primary_tbox"
                    if role == "extension"
                    else "not_applicable"
                ),
                "iteration_decomposition": (
                    "deterministic_tbox_iteration_ownership"
                ),
                "runtime_wiring": "domain_config",
            },
        },
    )
    if operation_mode == "inferred_atomic":
        from src.agents.scripts_and_prompts_generation.materialization_operation_inference import (
            infer_materialization_operation_decisions,
            invoke_operation_judge,
        )

        infer_materialization_operation_decisions(
            context,
            planner=operation_planner or invoke_operation_judge,
            model=str(
                config.models.get("operation_planning")
                or config.models.get("script_generation")
                or "gpt-5"
            ),
            checkpoint_path=(
                root
                / "semantic_planning"
                / config.ontology_name
                / "materialization_operation_decisions.json"
            ),
        )
        context.config_provenance["materialization_operation_planning"] = {
            "mode": "inferred_atomic",
            "candidate_source": "deterministic_tbox_filter",
            "decision_source": "llm_tbox_adjudication",
        }
    elif operation_mode == "occurrence_surface":
        from src.agents.scripts_and_prompts_generation.occurrence_surface_inference import (
            infer_occurrence_surface,
            invoke_occurrence_judge,
        )

        infer_occurrence_surface(
            context,
            planner=operation_planner or invoke_occurrence_judge,
            model=str(
                config.models.get("operation_planning")
                or config.models.get("script_generation")
                or "gpt-5"
            ),
            checkpoint_path=(
                root
                / "semantic_planning"
                / config.ontology_name
                / "occurrence_surface_decisions.json"
            ),
        )
        context.config_provenance["materialization_operation_planning"] = {
            "mode": "occurrence_surface",
            "candidate_source": "deterministic_tbox_occurrence_facets",
            "decision_source": "deterministic_tbox_structure",
            "primitive_membership": "deterministic_unique_ordered_membership",
            "instruction_source": "compiled_operational_instruction",
            "deterministic_parent_link": "unique_incoming_parent",
        }
    else:
        context.config_provenance["materialization_operation_planning"] = {
            "mode": "legacy",
            "candidate_source": "disabled",
            "decision_source": "none",
        }
    manifest = {
        "schema_version": "domain-artifact-manifest.v1",
        "domain_config_sha256": hashlib.sha256(config.path.read_bytes()).hexdigest(),
        "tbox_sha256": {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (config.primary_tbox, *config.supporting_tboxes)
        },
        "tbox_bundle": tbox_bundle,
        "semantic_decisions": decisions,
        "compiled_iteration_plan_sha256": _json_digest(plan),
        "planning_model": "gpt-5",
        "iteration_ownership_generator": (
            "deterministic_tbox_iteration_ownership"
        ),
        "iteration_ownership_sha256": str(
            (decisions.get("assignments") or {}).get("ownership_sha256")
            or ""
        ),
    }
    if write_files:
        structure_dir = Path(context.ontology_structure_dir)
        structure_dir.mkdir(parents=True, exist_ok=True)
        Path(context.contract_path).write_text(
            json.dumps(context.contract, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        Path(context.config_provenance_path).write_text(
            json.dumps(context.config_provenance, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        (Path(context.scripts_dir) / "_relationship_contract.json").write_text(
            json.dumps(
                runtime_publish_contract(context.contract),
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (structure_dir / "domain_artifact_manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    return context


def build_domain_generation_context_from_bundle(
    *,
    manifest_path: str | Path,
    output_root: str | Path,
    repository_root: str | Path,
    write_files: bool = True,
) -> AgenticGenerationContext:
    """Load a hash-verified generated bundle without rerunning semantic judges."""
    manifest_file = Path(manifest_path).resolve()
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "generated-config-bundle.v1":
        raise ValueError("unsupported generated config bundle schema")
    if manifest.get("semantic_authority") != "tbox_bundle_only":
        raise ValueError("generated bundle is not certified T-Box-only")
    gates = manifest.get("gates") or {}
    required_gates = (
        "top_entity_10_of_10",
        "iteration_blueprint_byte_stable_10_of_10",
        "operation_boundary_10_of_10",
    )
    if not all(gates.get(key) is True for key in required_gates):
        raise ValueError("generated bundle has an unpassed required stability gate")
    if not gates.get("reuse_10_of_10") and gates.get(
        "reuse_runtime_mode"
    ) != "fail_closed_until_match_basis_review":
        raise ValueError("unstable reuse decisions are not fail-closed")

    artifacts: dict[str, Path] = {}
    for name, spec in (manifest.get("artifacts") or {}).items():
        path = (manifest_file.parent / str((spec or {}).get("path") or "")).resolve()
        expected = str((spec or {}).get("sha256") or "")
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"generated bundle artifact hash mismatch: {name}")
        artifacts[str(name)] = path
    required = {
        "orchestration_config",
        "top_entity",
        "iteration_blueprint",
        "reuse_policy",
        "operation_candidates",
        "operation_decisions",
        "operation_units",
        "generation_contract",
    }
    missing = sorted(required - set(artifacts))
    if missing:
        raise ValueError("generated bundle is missing artifacts: " + ", ".join(missing))

    selected_top = json.loads(artifacts["top_entity"].read_text(encoding="utf-8"))
    context = build_domain_generation_context(
        domain_config_path=artifacts["orchestration_config"],
        output_root=output_root,
        repository_root=repository_root,
        write_files=write_files,
        operation_mode="legacy",
        derived_reuse_policy_path=artifacts["reuse_policy"],
        selected_top_entity=selected_top,
    )
    expected_blueprint = json.loads(
        artifacts["iteration_blueprint"].read_text(encoding="utf-8")
    )
    if context.iteration_blueprint != expected_blueprint:
        raise ValueError("recompiled iteration blueprint differs from generated bundle")

    from src.agents.scripts_and_prompts_generation.materialization_operation_units import (
        compile_materialization_operation_units,
    )

    context.contract["materialization_operation_candidates"] = json.loads(
        artifacts["operation_candidates"].read_text(encoding="utf-8")
    )
    context.contract["materialization_operation_decisions"] = json.loads(
        artifacts["operation_decisions"].read_text(encoding="utf-8")
    )
    compiled_units = compile_materialization_operation_units(
        parsed=context.parsed,
        contract=context.contract,
        iteration_plan=context.iteration_blueprint,
    )
    expected_units = json.loads(
        artifacts["operation_units"].read_text(encoding="utf-8")
    )
    if compiled_units != expected_units:
        raise ValueError("recompiled operation units differ from generated bundle")
    context.contract["materialization_operation_units"] = compiled_units
    expected_contract = json.loads(
        artifacts["generation_contract"].read_text(encoding="utf-8")
    )
    if context.contract != expected_contract:
        raise ValueError("recompiled generation contract differs from generated bundle")
    return context
