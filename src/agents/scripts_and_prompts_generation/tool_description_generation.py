"""Generate MCP-native tool descriptions from compiled ontology contracts."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
from pathlib import Path
from typing import Any

from src.agents.scripts_and_prompts_generation.level1_code_repair import invoke_json


def _tool_local(name: str) -> str:
    for prefix in ("check_existing_", "create_", "add_"):
        if name.startswith(prefix):
            return name[len(prefix) :]
    return name


def _tool_group(name: str) -> str:
    if name.startswith("create_"):
        return "creators"
    if name.startswith("add_"):
        return "relationships"
    return "checks_and_lifecycle"


def _creator_companion_recipes(
    *,
    names: list[str],
    all_tool_names: set[str],
    units: list[dict[str, Any]],
    relationship_contracts: dict[str, Any],
    external_creator_specs: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Derive exact non-atomic create-target then add-relationship recipes."""
    creator_classes = {
        str(spec.get("tool_name") or ""): str(spec.get("class_iri") or "")
        for spec in external_creator_specs
        if str(spec.get("tool_name") or "") in names
    }
    for unit in units:
        creator = unit.get("creator_contract") or {}
        creator_tool = str(
            creator.get("public_tool") or unit.get("public_tool") or ""
        ).strip()
        owner_class_iri = str(
            creator.get("class_iri") or unit.get("owner_class_iri") or ""
        ).strip()
        if creator_tool in names:
            creator_classes[creator_tool] = owner_class_iri
    recipes: dict[str, list[dict[str, Any]]] = {
        name: [] for name in names if name.startswith("create_")
    }
    units_by_tool = {
        str(
            (unit.get("creator_contract") or {}).get("public_tool")
            or unit.get("public_tool")
            or ""
        ): unit
        for unit in units
    }
    for creator_tool, owner_class_iri in creator_classes.items():
        if not owner_class_iri:
            continue
        creator = (units_by_tool.get(creator_tool) or {}).get(
            "creator_contract"
        ) or {}
        atomic_predicates = {
            str(edge.get("predicate_local") or "").strip()
            for edge in (creator.get("required_edges") or [])
            if str(edge.get("predicate_local") or "").strip()
        }
        creator_recipes: list[dict[str, Any]] = []
        for predicate_local, spec in relationship_contracts.items():
            relationship_tool = f"add_{predicate_local}"
            if (
                relationship_tool not in all_tool_names
                or predicate_local in atomic_predicates
                or owner_class_iri
                not in {str(value) for value in (spec.get("domain_iris") or [])}
            ):
                continue
            for target_creator in spec.get("creator_tools") or []:
                target_creator = str(target_creator).strip()
                if target_creator not in all_tool_names:
                    continue
                target_class_iris = [
                    str(value)
                    for value in (spec.get("range_iris") or [])
                    if str(value)
                ]
                target_creator_arguments: dict[str, str] = {}
                if (
                    target_creator == "create_om2_quantity"
                    and len(target_class_iris) == 1
                ):
                    target_creator_arguments["quantity_class_iri"] = (
                        target_class_iris[0]
                    )
                creator_recipes.append(
                    {
                        "sequence": f"{target_creator} → {relationship_tool}",
                        "target_creator_tool": target_creator,
                        "relationship_tool": relationship_tool,
                        "relationship": str(predicate_local),
                        "target_class_iris": target_class_iris,
                        "target_creator_arguments": target_creator_arguments,
                        "subject": (
                            f"IRI returned by {creator_tool} (relationship subject)"
                        ),
                        "object": (
                            f"IRI returned by {target_creator} (relationship object)"
                        ),
                        "condition": "only when this relation is supported by source evidence",
                    }
                )
        recipes[creator_tool] = creator_recipes
    return recipes


def _contract_slice(
    *,
    names: list[str],
    all_tool_names: set[str],
    parsed: dict[str, Any],
    contract: dict[str, Any],
) -> dict[str, Any]:
    locals_ = {_tool_local(name) for name in names}
    units = [
        unit
        for unit in (
            (contract.get("materialization_operation_units") or {}).get("units") or []
        )
        if str(unit.get("public_tool") or "") in names
        or str((unit.get("creator_contract") or {}).get("public_tool") or "") in names
    ]
    owner_class_iris = {
        str((unit.get("creator_contract") or {}).get("class_iri") or "")
        for unit in units
        if str((unit.get("creator_contract") or {}).get("class_iri") or "")
    }
    companion_relationships = {
        local: spec
        for local, spec in (
            contract.get("relationship_tool_contracts") or {}
        ).items()
        if f"add_{local}" in all_tool_names
        and owner_class_iris.intersection(
            str(domain_iri)
            for domain_iri in (spec.get("domain_iris") or [])
        )
    }
    companion_recipes = _creator_companion_recipes(
        names=names,
        all_tool_names=all_tool_names,
        units=units,
        relationship_contracts=contract.get("relationship_tool_contracts") or {},
        external_creator_specs=contract.get("external_class_creators") or [],
    )
    return {
        "tbox_classes": {
            local: spec
            for local, spec in (parsed.get("classes") or {}).items()
            if local in locals_
        },
        "tbox_properties": {
            local: spec
            for local, spec in (parsed.get("properties") or {}).items()
            if local in locals_
        },
        "relationship_tool_contracts": {
            local: spec
            for local, spec in (
                contract.get("relationship_tool_contracts") or {}
            ).items()
            if f"add_{local}" in names
        },
        "creator_companion_relationships": companion_relationships,
        "creator_companion_recipes": companion_recipes,
        "materialization_operation_units": units,
        "external_class_creators": [
            spec
            for spec in contract.get("external_class_creators") or []
            if str(spec.get("public_tool") or "") in names
        ],
        "reuse_policy": contract.get("reuse_policy") or {},
        "ordered_member_profile": (
            contract.get("ordered_member_profile") or {}
            if "check_ordered_members" in names
            else {}
        ),
        "om2_quantity_properties": (
            contract.get("om2_quantity_properties") or []
            if "create_om2_quantity" in names
            else []
        ),
        "required_links": contract.get("required_links") or [],
    }


def _build_mcp_instruction(contract: dict[str, Any]) -> str:
    """Build global, ontology-neutral guidance plus contract-derived invariants."""
    lines = [
        "Use the available MCP tools to mutate and export the RDF graph.",
        (
            "Invoke actual MCP tools for every mutation. Never simulate tool calls "
            "in prose, Markdown, a code block, JSON, or a proposed call list; a plan "
            "or serialized call description is not an executed graph mutation."
        ),
        (
            "Read all supplied evidence, then emit every independent mutation whose "
            "inputs are already known in the same turn. Do not first write an "
            "occurrence ledger, classify obligations, or walk a dependency checklist "
            "in conversation. Open another turn only when a required IRI is still "
            "missing or a tool just rejected a call."
        ),
        (
            "Evidence sections may repeat the same occurrences. For any repeated "
            "occurrence or repeated ordered sequence, keep only the first "
            "presentation and ignore later copies. Later complementary text may "
            "add attributes to that first individual, but must not create another "
            "individual or another 1..N sequence. Distinct source spans, headings, "
            "owners, roles, or order positions that appear only once remain "
            "distinct even when labels match."
        ),
        (
            "Each ordered collection is one canonical 1..N sequence taken from "
            "the first presentation of that sequence: no gaps, duplicates, or "
            "extra positions from later repeated views."
        ),
        (
            "A creator-owned atomic effect must go through that creator. A "
            "non-atomic edge uses its relationship tool only after both endpoint "
            "IRIs exist. Reuse an IRI only when the declared reuse policy and "
            "evidence authorize it."
        ),
        (
            "Do not repeat successful mutations. After a structured rejection, "
            "repair only that failed call and keep successful IRI and relationship "
            "state. Never report that graph content was created, linked, exported, "
            "or validated unless the corresponding tool returned successfully. "
            "Export only after every source-supported obligation that the tools "
            "can represent has succeeded."
        ),
    ]
    units = (
        (contract.get("materialization_operation_units") or {}).get("units") or []
    )
    ordered_collections: dict[
        tuple[str, str, tuple[str, ...]], set[str]
    ] = {}
    for unit in units:
        creator = unit.get("creator_contract") or {}
        if not creator.get("ordered_member"):
            continue
        creator_tool = str(
            creator.get("public_tool") or unit.get("public_tool") or ""
        ).strip()
        ordering_property = str(
            creator.get("ordering_property_local") or ""
        ).strip()
        for edge in creator.get("required_edges") or []:
            if str(edge.get("role") or "") != "container_membership":
                continue
            membership = str(
                edge.get("predicate_local") or edge.get("predicate_iri") or ""
            ).strip()
            containers = tuple(
                sorted(
                    str(value)
                    for value in (edge.get("container_class_iris") or [])
                    if str(value)
                )
            )
            key = (ordering_property, membership, containers)
            if creator_tool:
                ordered_collections.setdefault(key, set()).add(creator_tool)
    for (
        ordering_property,
        membership,
        containers,
    ), creator_tools in sorted(ordered_collections.items()):
        tools = ", ".join(f"`{tool}`" for tool in sorted(creator_tools))
        rule = (
            f"The creator tools {tools} participate in one shared ordered "
            "collection per common parent/container"
        )
        if membership:
            rule += f" through `{membership}`"
        if ordering_property:
            rule += f" and use the same `{ordering_property}` position"
        rule += (
            ". Merge supported occurrences from all of these creator types into "
            "one source-order sequence for that parent; creator type must never "
            "start a separate counter or reset the position to 1."
        )
        if containers:
            rule += (
                " The contract-declared container classes are "
                + ", ".join(f"`{value}`" for value in containers)
                + "."
            )
        rule += (
            " If evidence presents type-local ordinal counters, preserve their "
            "within-type relative order but translate them into the shared global "
            "1..N sequence; never submit duplicate positions to the shared "
            "collection. After an ordering rejection, reconstruct the unresolved "
            "global sequence instead of retrying the same conflicting position."
        )
        lines.append(rule)
    lines.append(
        "A creator's returned owner IRI denotes the owner class only; it never "
        "substitutes for the IRI of an atomic dependent created inside that owner "
        "operation. Relationship objects must be the target creator's returned IRI "
        "and must satisfy that relationship's domain and range."
    )
    reuse_by_iri = {
        str(spec.get("class_iri") or ""): spec
        for spec in ((contract.get("reuse_policy") or {}).get("classes") or [])
        if str(spec.get("class_iri") or "")
    }
    relationship_contracts = contract.get("relationship_tool_contracts") or {}
    for predicate_local, spec in sorted(relationship_contracts.items()):
        range_iris = [
            str(value) for value in (spec.get("range_iris") or []) if str(value)
        ]
        target_creators = [
            str(value) for value in (spec.get("creator_tools") or []) if str(value)
        ]
        fixed_runtime_target = (
            str(spec.get("target_handling") or "") == "fixed_runtime_creator"
        )
        non_reusable_target = bool(range_iris) and all(
            fixed_runtime_target
            or (
                range_iri in reuse_by_iri
                and not bool(reuse_by_iri[range_iri].get("reusable"))
            )
            for range_iri in range_iris
        )
        if not non_reusable_target or not target_creators:
            continue
        relationship_tool = f"add_{predicate_local}"
        rule = (
            f"`{relationship_tool}` has a non-reusable occurrence-local object "
            "slot. For every distinct subject-and-predicate obligation, create a "
            "fresh object and use it only for that owner slot. The exact allowed "
            "object class IRI"
        )
        rule += " is " if len(range_iris) == 1 else "s are "
        rule += ", ".join(f"`{value}`" for value in range_iris)
        rule += (
            "; the target creator "
            + ", ".join(f"`{value}`" for value in target_creators)
            + " must supply the object IRI."
        )
        if target_creators == ["create_om2_quantity"] and len(range_iris) == 1:
            rule += (
                " Call `create_om2_quantity` with "
                f"`quantity_class_iri=\"{range_iris[0]}\"` for each owner slot."
            )
        rule += (
            " Equal labels or values never authorize reuse across owners, and an "
            "IRI already used as another occurrence-local object must not be reused."
        )
        lines.append(rule)
    for unit in units:
        creator = unit.get("creator_contract") or {}
        creator_tool = str(
            creator.get("public_tool") or unit.get("public_tool") or ""
        ).strip()
        if creator.get("ordered_member"):
            ordering_property = str(
                creator.get("ordering_property_local") or ""
            ).strip()
            rule = (
                f"`{creator_tool}` is a contract-declared ordered-occurrence "
                "creator. Invoke it exactly once for each ledger occurrence in its "
                "canonical collection"
            )
            if ordering_property:
                rule += f", using `{ordering_property}` for the contiguous position"
            rule += (
                "; repeated evidence views must reuse the same ledger occurrence "
                "rather than create another ordered member."
            )
            lines.append(rule)
        for edge in creator.get("required_edges") or []:
            if (
                str(edge.get("role") or "") != "owned_dependent"
                or str(edge.get("lifecycle") or "") != "fresh_per_owner"
            ):
                continue
            dependent = str(edge.get("dependent_class_local") or "").strip()
            predicate = str(edge.get("predicate_local") or "").strip()
            cardinality = str(edge.get("cardinality") or "").strip()
            exclusivity = [
                str(value)
                for value in (edge.get("exclusive_predicate_iris") or [])
                if str(value)
            ]
            rule = (
                f"For every distinct `{creator_tool}` owner occurrence, use that "
                f"atomic creator to materialize a fresh occurrence-local "
                f"`{dependent}` dependent through `{predicate}`"
            )
            if cardinality:
                rule += f" with cardinality `{cardinality}`"
            rule += (
                "; never reuse that dependent for another owner or independently "
                "attach it through a relationship tool. Repeated evidence about the "
                "same owner does not require another dependent, but every genuinely "
                "distinct owner occurrence requires its own dependent."
            )
            if exclusivity:
                rule += (
                    " Its ownership is exclusive across these contract predicates: "
                    + ", ".join(f"`{value}`" for value in exclusivity)
                    + "."
                )
            lines.append(rule)
    lines.append(
        "Use creator-specific and relationship-specific tools explicitly, then call "
        "the required export tool."
    )
    return "\n".join(f"- {line}" for line in lines)


async def _runtime_tools(module_name: str) -> dict[str, Any]:
    module = importlib.import_module(module_name)
    tools = await module.mcp.get_tools()
    return dict(tools)


def _tool_schema(tool: Any) -> dict[str, Any]:
    return {
        "name": str(tool.name),
        "current_description": tool.description,
        "input_schema": tool.parameters,
        "output_schema": getattr(tool, "output_schema", None),
    }


def _main_registration_slice(main_source: str, names: list[str]) -> str:
    """Keep only main.py registration evidence for the requested tool batch."""
    quoted_names = {
        quoted
        for name in names
        for quoted in (f'"{name}"', f"'{name}'")
    }
    return "\n".join(
        line
        for line in main_source.splitlines()
        if any(quoted in line for quoted in quoted_names)
    )


def _generation_prompt(
    *,
    ontology_name: str,
    main_source: str,
    tools: list[dict[str, Any]],
    source_contract: dict[str, Any],
) -> str:
    return f"""
You are writing authoritative MCP tool descriptions for ontology A-Box construction.

Generate one concise but operationally complete description for every listed tool.
The descriptions will be registered directly in the MCP server and shown to a
tool-calling agent. They are interface documentation, not a task prompt.

Compression contract:
- The MCP instruction already owns global occurrence identity, shared ordering,
  reuse discipline, endpoint provenance, same-turn batching, and completion
  rules. Do not repeat those global rules in individual descriptions.
- The runtime input_schema already exposes parameter names, JSON types,
  requiredness, defaults, and enums. Do not restate that mechanical schema.
- Use dense plain text: one short purpose/boundary sentence, semantic notes only
  for non-obvious arguments, one atomic-effect sentence, compact companion recipe
  lines, and one rejection/correction sentence.
- State a rule only when it distinguishes this tool or supplies an exact class
  IRI, direction, ownership effect, freshness constraint, or creator argument
  needed to call it correctly.
- Never add tutorials, generic RDF explanations, repeated examples, headings with
  no unique content, or prose summaries of the full ontology.
- Target at most 700 characters for a relationship/check/lifecycle description.
  Target at most 1100 characters plus the literal companion recipe payload for a
  creator description. Exact required recipes and IRIs take priority over length.

Source authority:
1. Runtime input_schema is authoritative for callable parameters.
2. Materialization operation units are authoritative for atomic effects, required
   parents, owned dependents, freshness, and ordering.
3. T-Box classes/properties and relationship contracts are authoritative for
   meaning, evidence conditions, direction, domain/range, cardinality, and exclusions.
4. Reuse policy is authoritative for lookup scope and authorization handling.
5. main.py is authoritative for which tools are exposed.

For each description:
- State what the tool does and when an A-Box construction agent should use it.
- Explain only semantically non-obvious inputs; rely on input_schema for mechanics.
- State all atomic side effects and required parent/dependent behavior.
- For every creator tool, include a compact section titled exactly
  "Companion calls when evidenced". Use creator_companion_recipes as a mandatory
  checklist. Copy every listed `sequence` literally (for example,
  `create_Target → add_relationship`), then state that the current creator's
  returned IRI is the relationship subject and the target creator's returned IRI
  is the object. Copy every listed absolute `target_class_iris` value and every
  `target_creator_arguments` name/value literally into that recipe, so callers
  know the exact range class and creator input. Omit no listed recipe. Do not
  present atomic creator effects as companion calls. If the list is empty,
  explicitly say "None".
- State decision boundaries against neighboring creator tools only when those
  boundaries are present in the supplied contracts.
- Where contracts permit multiple compatible roles for one source object, explain
  that one representation does not replace another when both are evidenced.
- For occurrence-local entities, make the evidence boundary explicit: a fresh IRI
  alone is not enough. Do not copy attributes from another occurrence without
  local source evidence and contract authorization.
- Preserve every source-supported designation for an occurrence. Treat external
  lookup results as candidates subordinate to source evidence; they must not
  replace or conflict with source-supported identity.
- When the contracts expose non-atomic properties for a creator, enumerate all
  applicable follow-up properties as an evidence-conditional checklist.
- For relationship tools, identify subject and object roles and IRI requirements.
- For lookup tools, explain whether the result authorizes reuse or only resolves
  an existing scoped occurrence, including authorization-token handling.
- Mention important T-Box evidence gates, cardinality, identity, ordering, or
  non-reuse constraints that directly govern this tool.
- Explain relevant structured rejection conditions and what the caller should correct.
- Do not invent parameters, tools, effects, ontology facts, or workflow stages.
- Do not prescribe session initialization, export, iteration scope, or task completion
  except in the descriptions of lifecycle tools themselves.
- Do not rely on unstated domain knowledge or fixture-specific A-Box examples.

Return exactly one JSON object:
{{
  "descriptions": {{
    "<exact tool name>": "<plain-text description>",
    "...": "..."
  }}
}}
The key set must exactly equal the supplied tool names. Every value must be non-empty.

Ontology identifier: {ontology_name}

Exposed main.py:
```python
{main_source}
```

Actual runtime tool schemas:
{json.dumps(tools, ensure_ascii=False, indent=2)}

Relevant T-Box and compiled operation contracts:
{json.dumps(source_contract, ensure_ascii=False, indent=2)}
""".strip()


def _validate_companion_recipes(
    *,
    descriptions: dict[str, Any],
    source_contract: dict[str, Any],
) -> None:
    """Reject creator descriptions that omit contract-derived exact recipes."""
    errors: list[str] = []
    for creator_tool, recipes in (
        source_contract.get("creator_companion_recipes") or {}
    ).items():
        description = str(descriptions.get(creator_tool) or "")
        if "Companion calls when evidenced" not in description:
            errors.append(f"{creator_tool}: missing companion section")
            continue
        if not recipes and "None" not in description:
            errors.append(f"{creator_tool}: empty recipe list must state None")
        for recipe in recipes:
            sequence = str(recipe.get("sequence") or "")
            if sequence and sequence not in description:
                errors.append(f"{creator_tool}: missing exact recipe `{sequence}`")
            for class_iri in recipe.get("target_class_iris") or []:
                class_iri = str(class_iri)
                if class_iri and class_iri not in description:
                    errors.append(
                        f"{creator_tool}: missing target class IRI `{class_iri}`"
                    )
            for argument, value in (
                recipe.get("target_creator_arguments") or {}
            ).items():
                argument = str(argument)
                value = str(value)
                if argument and argument not in description:
                    errors.append(
                        f"{creator_tool}: missing target creator argument `{argument}`"
                    )
                if value and value not in description:
                    errors.append(
                        f"{creator_tool}: missing target creator value `{value}`"
                    )
    if errors:
        raise ValueError(
            "Creator companion recipe validation failed: " + "; ".join(errors)
        )


def _validate_description_compaction(
    *,
    descriptions: dict[str, Any],
    source_contract: dict[str, Any],
) -> None:
    """Reject descriptions that re-expand generic guidance into every tool."""
    recipes_by_creator = source_contract.get("creator_companion_recipes") or {}
    errors: list[str] = []
    for name, raw_description in descriptions.items():
        description = str(raw_description or "")
        if name.startswith("create_"):
            recipes = recipes_by_creator.get(name) or []
            literal_payload = 0
            for recipe in recipes:
                literal_payload += len(str(recipe.get("sequence") or ""))
                literal_payload += sum(
                    len(str(value))
                    for value in (recipe.get("target_class_iris") or [])
                )
                literal_payload += sum(
                    len(str(key)) + len(str(value))
                    for key, value in (
                        recipe.get("target_creator_arguments") or {}
                    ).items()
                )
            limit = 1400 + literal_payload + 100 * len(recipes)
        elif name.startswith("add_"):
            limit = 1000
        else:
            limit = 1200
        if len(description) > limit:
            errors.append(f"{name}: {len(description)} chars exceeds {limit}")
    if errors:
        raise ValueError(
            "Tool description compaction validation failed: " + "; ".join(errors)
        )


def generate_tool_descriptions(
    *,
    artifact_root: Path,
    ontology_name: str,
    module_name: str,
    model: str = "gpt-5",
) -> Path:
    structure = artifact_root / "ontology_structures" / ontology_name
    scripts = artifact_root / "scripts" / ontology_name
    parsed = json.loads((structure / "parsed.json").read_text(encoding="utf-8"))
    contract = json.loads(
        (structure / "generation_contract.json").read_text(encoding="utf-8")
    )
    main_path = scripts / "main.py"
    main_source = main_path.read_text(encoding="utf-8")
    runtime_tools = asyncio.run(_runtime_tools(module_name))

    descriptions: dict[str, str] = {}
    generation_records: list[dict[str, Any]] = []
    grouped_names: dict[str, list[str]] = {}
    for name in sorted(runtime_tools):
        grouped_names.setdefault(_tool_group(name), []).append(name)

    for group, names in grouped_names.items():
        schemas = [_tool_schema(runtime_tools[name]) for name in names]
        source_contract = _contract_slice(
            names=names,
            all_tool_names=set(runtime_tools),
            parsed=parsed,
            contract=contract,
        )
        prompt = _generation_prompt(
            ontology_name=ontology_name,
            main_source=_main_registration_slice(main_source, names),
            tools=schemas,
            source_contract=source_contract,
        )
        result = invoke_json(
            model,
            prompt,
            timeout_seconds=1200,
            max_attempts=3,
            provider_max_retries=1,
        )
        generated = result.data.get("descriptions")
        if not isinstance(generated, dict):
            raise ValueError(f"{group}: response has no descriptions object")
        observed = set(generated)
        expected = set(names)
        if observed != expected:
            raise ValueError(
                f"{group}: tool key mismatch; missing={sorted(expected - observed)}, "
                f"extra={sorted(observed - expected)}"
            )
        empty = [
            name
            for name in names
            if not isinstance(generated.get(name), str)
            or not str(generated[name]).strip()
        ]
        if empty:
            raise ValueError(f"{group}: empty descriptions for {empty}")
        if group == "creators":
            _validate_companion_recipes(
                descriptions=generated,
                source_contract=source_contract,
            )
        _validate_description_compaction(
            descriptions=generated,
            source_contract=source_contract,
        )
        descriptions.update(
            {name: str(generated[name]).strip() for name in names}
        )
        generation_records.append(
            {
                "group": group,
                "tool_count": len(names),
                "elapsed_seconds": result.elapsed_seconds,
                "token_usage": result.token_usage,
                "actual_cost_usd": result.actual_cost_usd,
                "generation_ids": result.generation_ids or [],
            }
        )

    output = scripts / "tool_descriptions.json"
    output.write_text(
        json.dumps(
            {
                "schema_version": "mcp-tool-descriptions.v1",
                "ontology_name": ontology_name,
                "model": model,
                "sources": {
                    "tbox": contract.get("ttl_file"),
                    "main_py": str(main_path),
                    "runtime_schema_source": f"{module_name}.mcp.get_tools",
                    "generation_contract": str(
                        structure / "generation_contract.json"
                    ),
                },
                "generation_records": generation_records,
                "instruction": _build_mcp_instruction(contract),
                "descriptions": descriptions,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return output


def refresh_mcp_instruction(*, artifact_root: Path, ontology_name: str) -> Path:
    """Refresh only deterministic MCP instruction text without another LLM call."""
    structure = artifact_root / "ontology_structures" / ontology_name
    output = artifact_root / "scripts" / ontology_name / "tool_descriptions.json"
    contract = json.loads(
        (structure / "generation_contract.json").read_text(encoding="utf-8")
    )
    payload = json.loads(output.read_text(encoding="utf-8"))
    payload["instruction"] = _build_mcp_instruction(contract)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--ontology", required=True)
    parser.add_argument("--module")
    parser.add_argument("--model", default="gpt-5")
    parser.add_argument("--instruction-only", action="store_true")
    args = parser.parse_args()
    if args.instruction_only:
        output = refresh_mcp_instruction(
            artifact_root=args.artifact_root,
            ontology_name=args.ontology,
        )
    else:
        if not args.module:
            parser.error("--module is required unless --instruction-only is used")
        output = generate_tool_descriptions(
            artifact_root=args.artifact_root,
            ontology_name=args.ontology,
            module_name=args.module,
            model=args.model,
        )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
