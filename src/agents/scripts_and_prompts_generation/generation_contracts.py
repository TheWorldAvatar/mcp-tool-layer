"""Generation contracts and validators for ontology-driven MCP artifacts.

The functions in this module deliberately derive operational constraints from
the ontology T-Box and runtime configuration. They should not contain benchmark
or paper-specific facts.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

from rdflib import Graph, OWL, RDF, RDFS, URIRef  # type: ignore[import-not-found]

from src.agents.scripts_and_prompts_generation.ttl_parser import (
    extract_ontology_integrity_profile,
)


def _local_name(iri: Any) -> str:
    text = str(iri or "").strip()
    if not text:
        return ""
    return text.rstrip("/#").rsplit("/", 1)[-1].rsplit("#", 1)[-1]


def _namespace_iri(iri: str) -> str:
    text = str(iri or "").strip()
    if "#" in text:
        return text.rsplit("#", 1)[0] + "#"
    if "/" in text:
        return text.rsplit("/", 1)[0] + "/"
    return ""


def _iter_rdf_list(graph: Graph, node: Any) -> list[Any]:
    out: list[Any] = []
    current = node
    while current and current != RDF.nil:
        first = graph.value(current, RDF.first)
        if first is not None:
            out.append(first)
        current = graph.value(current, RDF.rest)
    return out


def _domain_members(graph: Graph, domain: Any) -> list[str]:
    if isinstance(domain, URIRef):
        return [str(domain)]
    members: list[str] = []
    for union_list in graph.objects(domain, OWL.unionOf):
        members.extend(str(x) for x in _iter_rdf_list(graph, union_list) if isinstance(x, URIRef))
    return members


def _subclass_closure(graph: Graph) -> dict[str, set[str]]:
    classes = {str(c) for c in graph.subjects(RDF.type, OWL.Class) if isinstance(c, URIRef)}
    closure: dict[str, set[str]] = {c: {c} for c in classes}
    changed = True
    while changed:
        changed = False
        for child in list(closure):
            node = URIRef(child)
            for parent in graph.objects(node, RDFS.subClassOf):
                if not isinstance(parent, URIRef):
                    continue
                before = len(closure[child])
                closure[child].add(str(parent))
                closure[child].update(closure.get(str(parent), {str(parent)}))
                changed = changed or len(closure[child]) != before
    return closure


def _choose_union_superclass(members: list[str], closure: dict[str, set[str]]) -> str:
    for candidate in members:
        if all(candidate in closure.get(member, {member}) for member in members):
            return candidate
    return ""


def load_meta_task_config(path: str | Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def build_generation_contract_bundle(
    *,
    meta_task_config_path: str | Path = "configs/meta_task/meta_task_config.json",
    ontology_name: str | None = None,
) -> dict[str, Any]:
    """Build a machine-readable contract bundle from T-Box and runtime policies."""
    meta_cfg = load_meta_task_config(meta_task_config_path)
    main = (meta_cfg.get("ontologies", {}).get("main", {}) or {})
    if ontology_name and main.get("name") != ontology_name:
        candidates = [main] + list(meta_cfg.get("ontologies", {}).get("extensions", []) or [])
        main = next((x for x in candidates if (x or {}).get("name") == ontology_name), main)

    onto_name = str(main.get("name") or ontology_name or "").strip()
    ttl_file = str(main.get("ttl_file") or "").strip()
    graph = Graph()
    if ttl_file and Path(ttl_file).exists():
        graph.parse(ttl_file, format="turtle")

    closure = _subclass_closure(graph)
    relationship_domain_contracts: dict[str, dict[str, Any]] = {}
    quantity_properties: list[dict[str, str]] = []
    ordered_profile = extract_ontology_integrity_profile(ttl_file) if ttl_file else {}
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
    class_comments: dict[str, str] = {}
    for cls in graph.subjects(RDF.type, OWL.Class):
        if isinstance(cls, URIRef):
            local = _local_name(cls)
            class_comments[local] = "\n".join(str(c or "") for c in graph.objects(cls, RDFS.comment))

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
                cls_comment = class_comments.get(domain_local, "")
                prop_local = _local_name(prop_iri)
                if _comment_makes_property_required(cls_comment, prop_local):
                    required_step_scoped_object_properties.append(
                        {
                            "predicate_iri": prop_iri,
                            "predicate_local": prop_local,
                            "domain_iri": domain_iri,
                            "domain_local": domain_local,
                            "range_iri": range_iri,
                            "range_local": _local_name(range_iri),
                        }
                    )

    runtime_policies = (main.get("runtime_policies") or {}) if isinstance(main, dict) else {}
    main_entity_kg = (runtime_policies.get("main_entity_kg") or {}) if isinstance(runtime_policies, dict) else {}
    shell_validation = (main_entity_kg.get("shell_validation") or {}) if isinstance(main_entity_kg, dict) else {}
    top_class_iri = str(shell_validation.get("top_entity_class_iri") or "").strip()

    return {
        "ontology_name": onto_name,
        "ttl_file": ttl_file,
        "namespace_uri": namespace,
        "top_entity": {
            "class_iri": top_class_iri,
            "class_local": _local_name(top_class_iri),
            "iter1_allows_multiple": True,
            "main_pass_reuses_scoped_root": bool(
                (main_entity_kg.get("prompt_rules") or {}).get("require_top_entity_reuse")
            ),
        },
        "required_links": shell_validation.get("required_links") or [],
        "ordered_member_profile": ordered_profile,
        "relationship_domain_contracts": relationship_domain_contracts,
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
        "runtime_policies": runtime_policies,
    }


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


def _comment_makes_property_required(comment: str, prop_local: str) -> bool:
    text = str(comment or "").lower()
    prop = str(prop_local or "").lower()
    if not text or not prop:
        return False
    markers = ("must link", "must attach", "must have", "required", "exactly one")
    for segment in re.split(r"(?<=[.;])\s+|\n+", text):
        if prop in segment and any(marker in segment for marker in markers):
            return True
    return False


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
            missing_helpers = sorted(
                {
                    node.func.id
                    for node in ast.walk(mod)
                    if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id.startswith("_find_or_create_")
                    and node.func.id not in available
                }
            )
            if missing_helpers:
                failures.append(
                    f"{path.name}: undefined private helper call(s): "
                    + ", ".join(missing_helpers[:10])
                )
            bad_memory_path_calls = [
                node.lineno
                for node in ast.walk(mod)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "get_memory_paths"
                and len(node.args) < 2
            ]
            if bad_memory_path_calls:
                failures.append(
                    f"{path.name}: get_memory_paths called without doi and top-level entity "
                    f"at line(s): {', '.join(str(x) for x in bad_memory_path_calls[:10])}"
                )
            bad_guard_path_uses = [
                node.lineno
                for node in ast.walk(mod)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "open"
                and node.args
                and isinstance(node.args[0], ast.Call)
                and isinstance(node.args[0].func, ast.Name)
                and node.args[0].func.id == "_guard_paths"
            ]
            if bad_guard_path_uses:
                failures.append(
                    f"{path.name}: open(_guard_paths()) used instead of open(_guard_paths()[\"state\"]) "
                    f"at line(s): {', '.join(str(x) for x in bad_guard_path_uses[:10])}"
                )
            bad_success_calls = []
            for node in ast.walk(mod):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "_format_success_json"
                ):
                    has_created = any(kw.arg == "created" for kw in node.keywords)
                    if len(node.args) != 2 or not has_created:
                        bad_success_calls.append(node.lineno)
            if bad_success_calls:
                failures.append(
                    f"{path.name}: _format_success_json must be called as "
                    f"_format_success_json(iri, message, created=...) at line(s): "
                    f"{', '.join(str(x) for x in bad_success_calls[:10])}"
                )
        allowed_symbol_locals = {
            str(x).strip()
            for x in (contract_bundle.get("ontology_symbol_locals") or [])
            if str(x).strip()
        }
        has_allowed_1_2_symbol = any("1_2" in local and local in text for local in allowed_symbol_locals)
        if "chemica1" in text or ("1_2" in text and not has_allowed_1_2_symbol):
            failures.append(f"{path.name}: contains OCR/LLM-mangled public symbol text")
        orphan_identifier_lines = [
            line.strip()
            for line in text.splitlines()
            if line and not line[:1].isspace() and line.strip().isidentifier()
        ]
        if orphan_identifier_lines:
            failures.append(
                f"{path.name}: contains orphan top-level identifier line(s): "
                + ", ".join(orphan_identifier_lines[:5])
            )

    base_files = list(scripts.glob("*_creation_base.py"))
    if base_files:
        base_text = base_files[0].read_text(encoding="utf-8")
        if contract_bundle.get("required_links"):
            for marker in ("_read_global_state", "_ensure_required_top_links_before_export"):
                if marker not in base_text:
                    failures.append(f"{base_files[0].name}: missing required top-link helper/import `{marker}`")
        if contract_bundle.get("om2_quantity_properties") and "_normalize_om2_unit_alias" not in base_text:
            failures.append(f"{base_files[0].name}: missing OM-2 unit alias normalizer")
    else:
        warnings.append("No generated creation base module found")

    top_local = str((contract_bundle.get("top_entity") or {}).get("class_local") or "").strip()
    if top_local:
        fn_name = f"create_{top_local}"
        entity_text = "\n".join(p.read_text(encoding="utf-8") for p in scripts.glob("*_creation_entities*.py"))
        fn_src = _function_source(entity_text, fn_name)
        if fn_src:
            for marker in ("get_top_entity_iri", "_find_by_type_and_label", "_mint_hash_iri"):
                if marker not in fn_src:
                    failures.append(f"{fn_name}: missing stable multi-top contract marker `{marker}`")
        else:
            warnings.append(f"{fn_name} not found in generated entity modules")

    rel_text = "\n".join(p.read_text(encoding="utf-8") for p in scripts.glob("*_creation_relationships.py"))
    for prop_local, spec in (contract_bundle.get("relationship_domain_contracts") or {}).items():
        preferred = str((spec or {}).get("preferred_domain_local") or "").strip()
        namespace_attr = re.search(rf"\b[A-Z][A-Z0-9_]*\.{re.escape(preferred)}\b", rel_text)
        namespace_item = f"NAMESPACE.{preferred}" in rel_text or f"NAMESPACE['{preferred}']" in rel_text or f'NAMESPACE["{preferred}"]' in rel_text
        if preferred and not namespace_attr and not namespace_item:
            warnings.append(f"Relationship `{prop_local}` may not use preferred union-domain `{preferred}`")

    step_scoped_props = contract_bundle.get("step_scoped_object_properties") or []
    entity_text_all = (
        "\n".join(p.read_text(encoding="utf-8") for p in scripts.glob("*_creation_entities*.py"))
        if step_scoped_props
        else ""
    )
    for spec in step_scoped_props:
        prop_local = str((spec or {}).get("predicate_local") or "").strip()
        domain_local = str((spec or {}).get("domain_local") or "").strip()
        range_local = str((spec or {}).get("range_local") or "").strip()
        if not prop_local:
            continue
        if f"def add_{prop_local}" not in rel_text:
            failures.append(
                f"Missing relationship tool add_{prop_local} for step-scoped "
                f"{domain_local}->{range_local} contract"
            )
        if domain_local and range_local:
            ctor_src = _function_source(entity_text_all, f"create_{domain_local}")
            label_markers = {
                f"{_lower_initial(range_local)}_label",
                f"{_snake(range_local)}_label",
                f"{_lower_initial(prop_local)}_label",
                f"{_snake(prop_local)}_label",
            }
            if ctor_src and any(marker in ctor_src for marker in label_markers) and prop_local not in ctor_src:
                failures.append(
                    f"create_{domain_local}: label parameter for `{range_local}` must add "
                    f"step-scoped predicate `{prop_local}`"
                )

    required_step_scoped_props = contract_bundle.get("required_step_scoped_object_properties") or []
    if required_step_scoped_props:
        entity_text = "\n".join(p.read_text(encoding="utf-8") for p in scripts.glob("*_creation_entities*.py"))
    else:
        entity_text = ""
    for spec in required_step_scoped_props:
        prop_local = str((spec or {}).get("predicate_local") or "").strip()
        domain_local = str((spec or {}).get("domain_local") or "").strip()
        range_local = str((spec or {}).get("range_local") or "").strip()
        if not (prop_local and domain_local):
            continue
        ctor_src = _function_source(entity_text, f"create_{domain_local}")
        if not ctor_src:
            failures.append(
                f"Missing constructor create_{domain_local} for required step-scoped "
                f"{domain_local}->{prop_local}->{range_local} contract"
            )
            continue
        if prop_local not in ctor_src:
            failures.append(
                f"create_{domain_local}: missing required step-scoped object-property "
                f"`{prop_local}` to {range_local}"
            )
            continue
        if range_local:
            reuses_target = re.search(
                rf"_find_by_type_and_label\([^)]*{re.escape(range_local)}",
                ctor_src,
            ) is not None
            mints_target = re.search(
                rf"_mint_hash_iri\(\s*['\"]{re.escape(range_local)}['\"]\s*\)",
                ctor_src,
            ) is not None
            types_target = bool(
                re.search(rf"RDF\.type\s*,\s*[A-Z][A-Z0-9_]*\.{re.escape(range_local)}", ctor_src)
                or re.search(rf"RDF\.type\s*,\s*NAMESPACE\[['\"]{re.escape(range_local)}['\"]\]", ctor_src)
            )
            if not reuses_target:
                failures.append(
                    f"create_{domain_local}: required `{prop_local}` target must be "
                    f"resolved/reused by `{range_local}` label before minting"
                )
            if mints_target and not types_target:
                failures.append(
                    f"create_{domain_local}: fallback minted `{prop_local}` target must be "
                    f"typed as `{range_local}`"
                )

    quantity_props = contract_bundle.get("om2_quantity_properties") or []
    if quantity_props:
        entity_text = "\n".join(p.read_text(encoding="utf-8") for p in scripts.glob("*_creation_entities*.py"))
        try:
            entity_mod = ast.parse(entity_text)
        except SyntaxError:
            entity_mod = None
        if entity_mod is not None:
            create_sources = {
                node.name: ast.get_source_segment(entity_text, node) or ""
                for node in entity_mod.body
                if isinstance(node, ast.FunctionDef) and node.name.startswith("create_")
            }
            for fn_name, ctor_src in create_sources.items():
                if "_find_or_create_om2_quantity" not in ctor_src:
                    continue
                for spec in quantity_props:
                    prop_local = str((spec or {}).get("predicate_local") or "").strip()
                    if not prop_local:
                        continue
                    prop_markers = {
                        prop_local,
                        _lower_initial(prop_local),
                        _snake(prop_local),
                    }
                    if not any(marker in ctor_src for marker in prop_markers):
                        continue
                    link_pattern = rf"g\.add\(\s*\(\s*iri\s*,\s*[A-Z][A-Z0-9_]*\.{re.escape(prop_local)}\s*,"
                    if re.search(link_pattern, ctor_src) is None:
                        failures.append(
                            f"{fn_name}: OM-2 quantity for `{prop_local}` must be linked "
                            f"from the created entity"
                        )

    if prompts and prompts.exists():
        prompt_text = "\n".join(p.read_text(encoding="utf-8") for p in prompts.glob("*.md"))
        for spec in contract_bundle.get("required_links") or []:
            local = _local_name((spec or {}).get("predicate_iri"))
            if local and local not in prompt_text:
                warnings.append(f"Prompts do not mention required link `{local}`")
        for spec in step_scoped_props:
            prop_local = str((spec or {}).get("predicate_local") or "").strip()
            domain_local = str((spec or {}).get("domain_local") or "").strip()
            range_local = str((spec or {}).get("range_local") or "").strip()
            if prop_local and prop_local not in prompt_text:
                failures.append(
                    f"Prompts do not mention step-scoped object-property `{prop_local}` "
                    f"({domain_local}->{range_local})"
                )

    return {"ok": not failures, "failures": failures, "warnings": warnings}
