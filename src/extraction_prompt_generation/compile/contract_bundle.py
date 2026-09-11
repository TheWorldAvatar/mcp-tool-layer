"""Generation contract bundles compiled from T-Box and runtime policies.

Public compile entry is `build_generation_contract_bundle`. Validation of
already-generated artifacts is `validate_generated_artifacts`. See README.md.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from rdflib import Graph, OWL, RDF, RDFS, URIRef  # type: ignore[import-not-found]

from src.extraction_prompt_generation.compile.contract_observations import (
    build_validation_observation,
)
from src.extraction_prompt_generation.compile.contract_publish import (
    _ontology_config,
    _resolve_tbox_path,
    build_ontology_publish_contract,
    load_meta_task_config,
)
from src.extraction_prompt_generation.compile.contract_rdf import (
    _choose_union_superclass,
    _domain_members,
    _local_name,
    _namespace_iri,
    _subclass_closure,
)
from src.extraction_prompt_generation.tbox.parser import (
    extract_ontology_integrity_profile,
)


def _external_creator_specs(
    external_class_iris: set[str],
    *,
    internal_class_locals: set[str],
) -> list[dict[str, str]]:
    """Assign stable, collision-safe tools to contract-referenced external classes."""
    grouped: dict[str, list[str]] = {}
    for class_iri in sorted(external_class_iris):
        local = _local_name(class_iri)
        if local:
            grouped.setdefault(local, []).append(class_iri)
    specs: list[dict[str, str]] = []
    for local, class_iris in sorted(grouped.items()):
        needs_suffix = local in internal_class_locals or len(class_iris) > 1
        for class_iri in class_iris:
            suffix = hashlib.sha256(class_iri.encode("utf-8")).hexdigest()[:8]
            tool_local = f"{local}_{suffix}" if needs_suffix else local
            specs.append(
                {
                    "class_iri": class_iri,
                    "class_local": local,
                    "tool_name": f"create_{tool_local}",
                    "check_tool_name": f"check_existing_{tool_local}",
                    "source": "object_property_external_range",
                }
            )
    return specs


def build_generation_contract_bundle(
    *,
    meta_task_config_path: str | Path,
    ontology_name: str | None = None,
) -> dict[str, Any]:
    """Build a machine-readable contract bundle from T-Box and runtime policies."""
    meta_cfg = load_meta_task_config(meta_task_config_path)
    main = _ontology_config(meta_cfg, ontology_name)

    onto_name = str(main.get("name") or ontology_name or "").strip()
    ttl_file = str(main.get("ttl_file") or "").strip()
    graph = Graph()
    resolved_ttl_file = ""
    if ttl_file:
        try:
            resolved_ttl_file = str(
                _resolve_tbox_path(ttl_file, meta_task_config_path)
            )
            graph.parse(resolved_ttl_file, format="turtle")
        except FileNotFoundError:
            resolved_ttl_file = ""

    closure = _subclass_closure(graph)
    relationship_domain_contracts: dict[str, dict[str, Any]] = {}
    relationship_tool_contracts: dict[str, dict[str, Any]] = {}
    quantity_properties: list[dict[str, str]] = []
    ordered_profile = (
        extract_ontology_integrity_profile(resolved_ttl_file)
        if resolved_ttl_file
        else {}
    )
    runtime_policies = main.get("runtime_policies") or {}
    main_entity_policy = runtime_policies.get("main_entity_kg") or {}
    publish_policy = main_entity_policy.get("publish") or {}
    reconciliation_policy = publish_policy.get("hint_reconciliation") or {}
    shell_policy = main_entity_policy.get("shell_validation") or {}

    configured_order_properties = {
        _local_name(str(item.get("order_predicate_iri") or ""))
        for item in reconciliation_policy.get("ordered_member_hint_contracts") or []
        if isinstance(item, dict)
        and str(item.get("order_predicate_iri") or "").strip()
    }
    configured_member_classes: set[str] = set()
    configured_collection_properties: set[str] = set()
    for item in runtime_policies.get("ordered_member_contracts") or []:
        if not isinstance(item, dict):
            continue
        target_iri = str(item.get("member_class_iri") or "").strip()
        collection_iri = str(item.get("collection_property_iri") or "").strip()
        order_iri = str(item.get("order_property_iri") or "").strip()
        if collection_iri:
            configured_collection_properties.add(_local_name(collection_iri))
        if order_iri:
            configured_order_properties.add(_local_name(order_iri))
        if target_iri:
            configured_member_classes.update(
                _local_name(class_iri)
                for class_iri, superclass_iris in closure.items()
                if target_iri in superclass_iris
            )
    for item in shell_policy.get("required_links") or []:
        if not isinstance(item, dict) or item.get("ordered_member") is not True:
            continue
        target_iri = str(item.get("target_class_iri") or "").strip()
        predicate_iri = str(item.get("predicate_iri") or "").strip()
        if predicate_iri:
            configured_collection_properties.add(_local_name(predicate_iri))
        if target_iri:
            configured_member_classes.update(
                _local_name(class_iri)
                for class_iri, superclass_iris in closure.items()
                if target_iri in superclass_iris
            )

    ordered_profile["ordered_member_classes"] = sorted(
        set(ordered_profile.get("ordered_member_classes") or [])
        | configured_member_classes
    )
    ordered_profile["individually_linked_object_properties"] = sorted(
        set(ordered_profile.get("individually_linked_object_properties") or [])
        | configured_collection_properties
    )
    ordered_profile["single_valued_ordering_properties"] = sorted(
        set(ordered_profile.get("single_valued_ordering_properties") or [])
        | configured_order_properties
    )
    ordered_member_locals = {
        str(x).strip()
        for x in (ordered_profile.get("ordered_member_classes") or [])
        if str(x).strip()
    }
    step_scoped_object_properties: list[dict[str, str]] = []
    required_step_scoped_object_properties: list[dict[str, str]] = []
    namespace_candidates = sorted({_namespace_iri(str(s)) for s in graph.subjects() if isinstance(s, URIRef)})
    namespace = next((x for x in namespace_candidates if onto_name.lower() in x.lower()), "")
    ontology_symbol_locals = sorted(
        {
            _local_name(node)
            for node in set(graph.subjects()) | set(graph.predicates()) | set(graph.objects())
            if isinstance(node, URIRef)
        }
    )
    declared_class_iris = {
        str(node)
        for class_type in (OWL.Class, RDFS.Class)
        for node in graph.subjects(RDF.type, class_type)
        if isinstance(node, URIRef)
    }
    internal_class_locals = {
        _local_name(class_iri)
        for class_iri in declared_class_iris
        if namespace and class_iri.startswith(namespace)
    }
    external_entity_range_iris: set[str] = set()
    for prop in graph.subjects(RDF.type, OWL.ObjectProperty):
        if not isinstance(prop, URIRef):
            continue
        prop_iri = str(prop)
        ranges = [str(r) for r in graph.objects(prop, RDFS.range) if isinstance(r, URIRef)]
        domains: list[str] = []
        union_domains: list[list[str]] = []
        for domain in graph.objects(prop, RDFS.domain):
            members = _domain_members(graph, domain)
            if members:
                domains.extend(members)
            if len(members) > 1:
                union_domains.append(members)
                preferred = _choose_union_superclass(members, closure)
                if preferred:
                    relationship_domain_contracts[_local_name(prop_iri)] = {
                        "predicate_iri": prop_iri,
                        "union_members": members,
                        "preferred_domain_iri": preferred,
                        "preferred_domain_local": _local_name(preferred),
                    }
        internal_range_iris = sorted(set(ranges) & declared_class_iris)
        external_range_iris = sorted(set(ranges) - declared_class_iris)
        internal_targets = sorted({_local_name(iri) for iri in internal_range_iris})
        most_specific_targets = ordered_profile.get(
            "most_specific_subclass_targets"
        ) or {}
        materialization_target_locals = sorted(
            {
                concrete
                for target in internal_targets
                for concrete in (
                    most_specific_targets.get(target) or [target]
                )
                if str(concrete).strip()
            }
        )
        external_targets = sorted({_local_name(iri) for iri in external_range_iris})
        om2_range_iris = sorted(
            iri
            for iri in external_range_iris
            if "ontology-of-units-of-measure.org/resource/om-2/" in iri
        )
        creatable_external_range_iris = sorted(
            set(external_range_iris) - set(om2_range_iris)
        )
        external_entity_range_iris.update(creatable_external_range_iris)
        external_creator_specs = _external_creator_specs(
            set(creatable_external_range_iris),
            internal_class_locals=internal_class_locals,
        )
        creator_tools = [
            f"create_{local}" for local in materialization_target_locals
        ]
        if om2_range_iris:
            creator_tools.append("create_om2_quantity")
        creator_tools.extend(
            spec["tool_name"] for spec in external_creator_specs
        )
        if internal_targets and external_targets:
            target_handling = "mixed"
        elif internal_targets:
            target_handling = "generated_creator"
        elif om2_range_iris:
            target_handling = "fixed_runtime_creator"
        elif creatable_external_range_iris:
            target_handling = "generated_external_creator"
        else:
            target_handling = "untyped_existing_iri"
        relationship_tool_contracts[_local_name(prop_iri)] = {
            "predicate_iri": prop_iri,
            "predicate_local": _local_name(prop_iri),
            "domain_iris": sorted(set(domains)),
            "range_iris": sorted(set(ranges)),
            "range_locals": sorted({_local_name(iri) for iri in ranges}),
            "internal_range_iris": internal_range_iris,
            "external_range_iris": external_range_iris,
            "internal_targets": internal_targets,
            "materialization_target_locals": materialization_target_locals,
            "external_targets": external_targets,
            "external_creator_specs": external_creator_specs,
            "fixed_runtime_range_iris": om2_range_iris,
            "creator_tools": creator_tools,
            "creator_available": bool(creator_tools),
            "target_handling": target_handling,
        }
        if any("ontology-of-units-of-measure.org" in r for r in ranges):
            quantity_properties.append(
                {
                    "predicate_iri": prop_iri,
                    "predicate_local": _local_name(prop_iri),
                    "domain_locals": ", ".join(sorted({_local_name(x) for x in domains if x})),
                    "range_iris": ", ".join(ranges),
                }
            )
        for domain_iri in domains:
            domain_local = _local_name(domain_iri)
            if domain_local not in ordered_member_locals:
                continue
            for range_iri in ranges:
                if "ontology-of-units-of-measure.org" in range_iri:
                    continue
                step_scoped_object_properties.append(
                    {
                        "predicate_iri": prop_iri,
                        "predicate_local": _local_name(prop_iri),
                        "domain_iri": domain_iri,
                        "domain_local": domain_local,
                        "range_iri": range_iri,
                        "range_local": _local_name(range_iri),
                    }
                )
    for restriction in graph.subjects(RDF.type, OWL.Restriction):
        for target_predicate in (
            OWL.onClass,
            OWL.someValuesFrom,
            OWL.allValuesFrom,
        ):
            target = graph.value(restriction, target_predicate)
            target_iri = str(target) if isinstance(target, URIRef) else ""
            if (
                target_iri
                and target_iri not in declared_class_iris
                and "ontology-of-units-of-measure.org/resource/om-2/"
                not in target_iri
            ):
                external_entity_range_iris.add(target_iri)
    external_class_creators = _external_creator_specs(
        external_entity_range_iris,
        internal_class_locals=internal_class_locals,
    )
    publish_contract = (
        build_ontology_publish_contract(
            meta_task_config_path=meta_task_config_path,
            ontology_name=ontology_name,
        )
        if resolved_ttl_file
        else {
            "classes": [],
            "subclass_closure": [],
            "object_properties": [],
            "constraints": [],
            "required_links": [],
            "top_role": {
                "status": "unknown",
                "class_iri": "",
                "class_local": "",
                "source": "tbox",
                "evidence": [],
            },
        }
    )
    top_role = dict(publish_contract["top_role"])

    return {
        "ontology_name": onto_name,
        "ttl_file": ttl_file,
        "namespace_uri": namespace,
        "contract_layers": {
            "tbox_derived": {
                "source": "active_tbox",
                "keys": [
                    "top_entity",
                    "required_links",
                    "ontology_publish_contract",
                    "ordered_member_profile",
                    "relationship_domain_contracts",
                    "relationship_tool_contracts",
                    "external_class_creators",
                    "step_scoped_object_properties",
                    "required_step_scoped_object_properties",
                    "om2_quantity_properties",
                    "ontology_symbol_locals",
                ],
            },
            "generation_runtime": {
                "source": "generic_infrastructure_policy",
                "lifecycle": "idempotent_open_or_resume",
                "default_export": "abox_only",
                "closed_world_surface": True,
            },
            "pipeline_only": {
                "source": "meta_task_config",
                "key": "runtime_policies",
                "may_enter_generation_prompt": False,
                "storage": "AgenticGenerationContext.pipeline_runtime_policies",
            },
        },
        "top_entity": {
            **top_role,
            "iter1_allows_multiple": True,
            "main_pass_reuses_scoped_root": False,
        },
        "required_links": publish_contract["required_links"],
        "ontology_publish_contract": publish_contract,
        "ordered_member_profile": ordered_profile,
        "relationship_domain_contracts": relationship_domain_contracts,
        "relationship_tool_contracts": relationship_tool_contracts,
        "external_class_creators": external_class_creators,
        "step_scoped_object_properties": sorted(
            step_scoped_object_properties,
            key=lambda x: (x["domain_local"], x["predicate_local"], x["range_local"]),
        ),
        "required_step_scoped_object_properties": sorted(
            required_step_scoped_object_properties,
            key=lambda x: (x["domain_local"], x["predicate_local"], x["range_local"]),
        ),
        "om2_quantity_properties": quantity_properties,
        "ontology_symbol_locals": ontology_symbol_locals,
    }


def build_relationship_tool_contracts_from_tbox(
    tbox_path: str | Path,
) -> dict[str, dict[str, Any]]:
    """Compile per-property tool metadata solely from a machine-readable T-Box."""
    graph = Graph()
    graph.parse(str(tbox_path), format="turtle")
    integrity_profile = extract_ontology_integrity_profile(str(tbox_path))
    most_specific_targets = (
        integrity_profile.get("most_specific_subclass_targets") or {}
    )
    declared_class_iris = {
        str(node)
        for class_type in (OWL.Class, RDFS.Class)
        for node in graph.subjects(RDF.type, class_type)
        if isinstance(node, URIRef)
    }
    property_namespaces = sorted(
        {
            _namespace_iri(str(node))
            for node in graph.subjects(RDF.type, OWL.ObjectProperty)
            if isinstance(node, URIRef)
        }
    )
    primary_namespace = property_namespaces[0] if len(property_namespaces) == 1 else ""
    internal_class_locals = {
        _local_name(class_iri)
        for class_iri in declared_class_iris
        if primary_namespace and class_iri.startswith(primary_namespace)
    }
    contracts: dict[str, dict[str, Any]] = {}
    for prop in graph.subjects(RDF.type, OWL.ObjectProperty):
        if not isinstance(prop, URIRef):
            continue
        prop_iri = str(prop)
        ranges = sorted(
            {
                str(value)
                for value in graph.objects(prop, RDFS.range)
                if isinstance(value, URIRef)
            }
        )
        domains: set[str] = set()
        for domain in graph.objects(prop, RDFS.domain):
            domains.update(_domain_members(graph, domain))
        internal_range_iris = sorted(set(ranges) & declared_class_iris)
        external_range_iris = sorted(set(ranges) - declared_class_iris)
        internal_targets = sorted({_local_name(iri) for iri in internal_range_iris})
        materialization_target_locals = sorted(
            {
                concrete
                for target in internal_targets
                for concrete in (most_specific_targets.get(target) or [target])
                if str(concrete).strip()
            }
        )
        external_targets = sorted({_local_name(iri) for iri in external_range_iris})
        om2_range_iris = sorted(
            iri
            for iri in external_range_iris
            if "ontology-of-units-of-measure.org/resource/om-2/" in iri
        )
        creatable_external_range_iris = sorted(
            set(external_range_iris) - set(om2_range_iris)
        )
        external_creator_specs = _external_creator_specs(
            set(creatable_external_range_iris),
            internal_class_locals=internal_class_locals,
        )
        creator_tools = [
            f"create_{local}" for local in materialization_target_locals
        ]
        if om2_range_iris:
            creator_tools.append("create_om2_quantity")
        creator_tools.extend(spec["tool_name"] for spec in external_creator_specs)
        if internal_targets and external_targets:
            target_handling = "mixed"
        elif internal_targets:
            target_handling = "generated_creator"
        elif om2_range_iris:
            target_handling = "fixed_runtime_creator"
        elif creatable_external_range_iris:
            target_handling = "generated_external_creator"
        else:
            target_handling = "untyped_existing_iri"
        contracts[_local_name(prop_iri)] = {
            "predicate_iri": prop_iri,
            "predicate_local": _local_name(prop_iri),
            "domain_iris": sorted(domains),
            "range_iris": ranges,
            "range_locals": sorted({_local_name(iri) for iri in ranges}),
            "internal_range_iris": internal_range_iris,
            "external_range_iris": external_range_iris,
            "internal_targets": internal_targets,
            "materialization_target_locals": materialization_target_locals,
            "external_targets": external_targets,
            "external_creator_specs": external_creator_specs,
            "fixed_runtime_range_iris": om2_range_iris,
            "creator_tools": creator_tools,
            "creator_available": bool(creator_tools),
            "target_handling": target_handling,
        }
    return contracts


def write_generation_contract_bundle(bundle: dict[str, Any], output_path: str | Path) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(bundle, indent=2, ensure_ascii=False), encoding="utf-8")


def _function_source(code: str, name: str) -> str:
    try:
        mod = ast.parse(code)
    except SyntaxError:
        return ""
    for node in mod.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(code, node) or ""
    return ""


def _lower_initial(name: str) -> str:
    return name[:1].lower() + name[1:] if name else ""


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", str(name or "")).lower()


def _effective_step_scoped_props(contract_bundle: dict[str, Any]) -> list[dict[str, str]]:
    """Expand union-domain step properties into concrete T-Box class contracts."""
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()

    def add_spec(prop_local: str, domain_local: str, range_local: str) -> None:
        if not (prop_local and domain_local and range_local):
            return
        key = (prop_local, domain_local, range_local)
        if key in seen:
            return
        seen.add(key)
        out.append(
            {
                "predicate_local": prop_local,
                "domain_local": domain_local,
                "range_local": range_local,
            }
        )

    for spec in contract_bundle.get("step_scoped_object_properties") or []:
        add_spec(
            str((spec or {}).get("predicate_local") or "").strip(),
            str((spec or {}).get("domain_local") or "").strip(),
            str((spec or {}).get("range_local") or "").strip(),
        )

    property_constraints = ((contract_bundle.get("ordered_member_profile") or {}).get("property_constraints") or {})
    for prop_local, spec in (contract_bundle.get("relationship_domain_contracts") or {}).items():
        range_local = str(((property_constraints.get(prop_local) or {}).get("range") or "")).strip()
        for member in (spec or {}).get("union_members") or []:
            add_spec(str(prop_local or "").strip(), _local_name(member), range_local)
    return out


def validate_generated_artifacts(
    *,
    scripts_dir: str | Path,
    prompts_dir: str | Path | None = None,
    contract_bundle: dict[str, Any],
) -> dict[str, Any]:
    """Validate generated artifacts against the contract bundle."""
    scripts = Path(scripts_dir)
    prompts = Path(prompts_dir) if prompts_dir else None
    failures: list[str] = []
    warnings: list[str] = []

    for path in sorted(scripts.glob("*.py")):
        if path.name.startswith("main_part_") or "_attempt_" in path.name:
            continue
        text = path.read_text(encoding="utf-8")
        try:
            mod = ast.parse(text)
        except SyntaxError as e:
            failures.append(f"{path.name}: syntax error line {e.lineno}: {e.msg}")
            mod = None
        if mod is not None:
            available: set[str] = set()
            for node in mod.body:
                if isinstance(node, ast.FunctionDef):
                    available.add(node.name)
                elif isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        available.add(alias.asname or alias.name)
            # Private helper names and internal call signatures are intentionally
            # unconstrained. Import and runtime probes validate executable behavior.
        allowed_symbol_locals = {
            str(x).strip()
            for x in (contract_bundle.get("ontology_symbol_locals") or [])
            if str(x).strip()
        }
        has_allowed_1_2_symbol = any("1_2" in local and local in text for local in allowed_symbol_locals)
        if "chemica1" in text or ("1_2" in text and not has_allowed_1_2_symbol):
            failures.append(f"{path.name}: contains OCR/LLM-mangled public symbol text")
    if contract_bundle.get("om2_quantity_properties"):
        runtime_path = scripts / "_fixed_om2_runtime.py"
        if not runtime_path.is_file():
            failures.append("Missing fixed OM-2 runtime `_fixed_om2_runtime.py`")

    # Prompt wording is not a generated-code contract. End-to-end extraction and
    # materialization scoring owns whether prompts preserve required semantics.

    subject_key = str(contract_bundle.get("ontology_name") or scripts.name)
    observed_artifacts = [
        str(scripts),
        *([str(prompts)] if prompts is not None else []),
    ]
    observations = [
        build_validation_observation(
            check_id="generation.contract_bundle",
            subject_key=subject_key,
            stage="contract",
            failures=failures,
            warnings=warnings,
            observed_artifacts=observed_artifacts,
            evidence={"contract_ontology": contract_bundle.get("ontology_name")},
        )
    ]
    return {
        "ok": not failures,
        "failures": failures,
        "warnings": warnings,
        "observations": observations,
    }
