"""Opt-in whole-graph KG build from all hint layers (Pipeline analogue of OX --full-hints).

Official campaigns leave ``kg_full_hints_onepass`` unset and keep the iter2→3→4 loop.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

_TOOL_LINE = re.compile(
    r"^  - ((?:init_memory|export_memory|create_|add_|check_existing_)\S+.*)$",
    re.M,
)

ONEPASS_CONTRACT = """
# Whole-graph construction from all iteration hints
The iteration_hints slot contains every available SEMANTIC_HINTS ledger for one
ChemicalSynthesis, grouped as ITER2, ITER3, and ITER4. These are complementary
views of one graph, not separate graphs and not sequential patch requests.

- ITER2 owns the synthesis/output/input/document skeleton.
- ITER3 owns synthesis steps, step chemicals, amounts, temperatures, and durations.
- ITER4 owns vessels, yields, equipment, and remaining conditions.
- Reconcile repeated mentions across sections into the same node. Do not create
  one copy per iteration.
- Materialize the COMPLETE graph for this ChemicalSynthesis in this single session.
- Call init_memory first and export_memory last.
- Ignore any per-iteration "do not broaden" wording from source prompts; this
  session owns the union of iteration 2, 3, and 4 scopes.
""".strip()

GENERIC_ONEPASS_KG_PROMPT = """
You are building one complete A-Box for a single bound root entity in one shared
session. The supplied semantic hints are complementary evidence about that graph,
not sequential patches and not independent tasks.

Runtime inputs:
- Document identifier: {doi}
- Bound root label: {entity_label}
- Bound root IRI: {entity_uri}
- All available semantic hints: {iteration_hints}

Generic A-Box construction rules:
- Treat the actual MCP tool catalog attached to this request as the complete
  executable construction interface. Derive each operation from its tool
  description and input schema; do not assume an unlisted operation or argument.
- Read all hint sections before acting. Materialize every source-supported entity,
  attribute, relationship, and ordered occurrence that the available tools can
  represent.
- Preserve occurrence identity. Repeated labels do not by themselves identify one
  individual; reuse an IRI only when the evidence and an exposed lookup operation
  establish that it is the same reusable or previously materialized individual.
- Keep the supplied root IRI as the graph root. Relationship endpoints must be
  absolute IRIs returned by successful operations or established by persisted
  graph evidence, never labels or invented identifiers.
- Interpret a creator's required parent, dependent, ordering, and attribute
  arguments as one operation contract. When one creator produces an owned
  dependent or relationship atomically, provide all grounded arguments in that
  call and do not repeat the same effect separately.
- For a relationship not owned by a creator, resolve or create its target as
  directed by the exposed tool descriptions, then call the relationship operation
  in the documented subject-to-object direction.
- When lookup results distinguish reference resolution from reusable identity,
  follow that distinction. Propagate any returned authorization value exactly as
  required by subsequent mutation schemas.
- Preserve source lexical values unless a tool schema explicitly requires another
  representation. Do not invent missing values, entities, links, order positions,
  or scientific facts.
- Treat every structured error or rejected mutation as an unsatisfied obligation.
  Correct it from the hints and tool contract when possible; do not report a
  rejected write as completed.
- Before finishing, check that every representable, source-supported obligation
  from every hint section has been attempted successfully and that the scoped
  graph contains substantive A-Box facts.

The outer runtime owns session initialization and final export. Perform no
independent subtask completion or per-section lifecycle.
""".strip()

MCP_NATIVE_ONEPASS_TASK_PROMPT = """
Execute one complete KG-building task using the authoritative ONEPASS contract
exposed by the attached MCP instruction and its actual tool catalog.

Runtime bindings:
- DOI: {doi}
- Bound root label: {entity_label}
- Bound root IRI: {entity_uri}

All complementary semantic ledgers for this bound root:
{iteration_hints}

Materialize the complete source-supported graph. The MCP instruction owns all
global interpretation, ordering, identity, reuse, tool-use, and lifecycle rules.
""".strip()

MCP_NATIVE_ONEPASS_USER_ALIGNED_TASK_PROMPT = """
You are the runtime KG-building agent for a single whole-graph pass.
This session sees every iteration ledger at once and materializes the complete ChemicalSynthesis graph. Do not treat the ledgers as sequential patches.

Bound runtime inputs (single authoritative bindings):
- DOI: {doi}
- Top-level ChemicalSynthesis label: {entity_label}
- Top-level ChemicalSynthesis IRI: {entity_uri}
- All-iteration hints: {iteration_hints}

""".lstrip()

MCP_SEMANTIC_SURFACE_TASK_PROMPT = """
Use the attached MCP for the bound graph-building task.

Runtime bindings:
- DOI: {doi}
- Bound root label: {entity_label}
- Bound root IRI: {entity_uri}

Semantic ledger supplied to the agent:
{iteration_hints}
""".strip()

OFFICIAL_CONSTRUCTION_PREFACE = """
# Official per-iteration construction rules
The following blocks are the official iteration 2, 3, and 4 KG-building prompts,
copied verbatim except that their runtime placeholders now refer to the
whole-graph bindings above. Apply every construction, reuse, and linking rule
they contain, including step-local ChemicalInput ownership
(add_hasAddedChemicalInput on every Add, and the matching Filter / Separate /
Evaporate rules). When a block says the iteration is closed or "do not
broaden", ignore that scope lock; this session owns the union.

Creators and relationship writers stay split: create the entity first, then
call the listed standalone add_* tool. Do not invent a composite/atomic creator
or skip a listed relationship write because a similar label already exists.
""".strip()

_OFFICIAL_PLACEHOLDER_REWRITES = (
    ("{iteration_hints}", "the all-iteration hints bound above"),
    ("{entity_identity_dossier}", "the bound entity identity dossier, if provided"),
    ("{entity_label}", "the bound top-level entity label above"),
    ("{entity_uri}", "the bound top-level entity IRI above"),
    ("{doi}", "the bound DOI above"),
    ("{hash}", "the bound DOI above"),
)


@dataclass(frozen=True)
class CombinedHints:
    text: str
    layers: tuple[int, ...]
    paths: tuple[Path, ...]


def find_layer_hint_file(mcp_run_dir: str | Path, iter_num: int, entity_safe: str) -> Path | None:
    directory = Path(mcp_run_dir)
    expected = directory / f"iter{iter_num}_hints_{entity_safe}.txt"
    if expected.is_file():
        return expected
    prefix = f"iter{iter_num}_hints_"
    if not directory.is_dir():
        return None
    needle = re.sub(r"[^A-Za-z0-9]+", "", entity_safe).lower()
    for path in sorted(directory.glob(f"{prefix}*.txt")):
        if ".pre_size_dedup" in path.name:
            continue
        stem = path.name[len(prefix) : -4]
        if re.sub(r"[^A-Za-z0-9]+", "", stem).lower() == needle:
            return path
    return None


def combine_hint_ledgers(
    mcp_run_dir: str | Path,
    entity_safe: str,
    layers: Iterable[int] = (2, 3, 4),
) -> CombinedHints:
    """Concatenate every available iterN hint file, matching OX HintEntity.full_hints()."""
    sections: list[str] = []
    found_layers: list[int] = []
    found_paths: list[Path] = []
    for layer in layers:
        path = find_layer_hint_file(mcp_run_dir, layer, entity_safe)
        if path is None:
            continue
        found_layers.append(layer)
        found_paths.append(path)
        sections.append(
            f"=== ITER{layer} SEMANTIC_HINTS ===\n"
            f"Source: {path}\n"
            f"{path.read_text(encoding='utf-8').strip()}"
        )
    if not sections:
        raise FileNotFoundError(
            f"No iter2/3/4 hint ledgers for {entity_safe} in {mcp_run_dir}"
        )
    text = (
        "SEMANTIC_HINTS_V1\n"
        "Whole-graph ledger: complementary iteration views of one bound top-level entity.\n\n"
        + "\n\n".join(sections)
        + "\n"
    )
    return CombinedHints(text=text, layers=tuple(found_layers), paths=tuple(found_paths))


def _locals(values: Iterable[Any]) -> list[str]:
    seen: list[str] = []
    for value in values:
        if isinstance(value, dict):
            local = str(value.get("local") or "").strip()
        else:
            local = str(value or "").strip()
        if local and local not in seen:
            seen.append(local)
    return seen


def merge_iteration_specs(iterations: list[dict[str, Any]]) -> dict[str, Any]:
    """Union compiled ownership from KG iterations 2–4 into one spec."""
    kg_iters = [
        dict(item)
        for item in iterations
        if int(item.get("iteration_number") or 0) >= 2
        and item.get("kg_building_prompt")
    ]
    if not kg_iters:
        raise ValueError("no KG-building iterations to collapse")
    classes: list[str] = []
    properties: list[str] = []
    linked: list[str] = []
    semantic_classes: list[dict[str, Any]] = []
    semantic_properties: list[dict[str, Any]] = []
    source_prompt_specs: list[dict[str, Any]] = []
    for item in kg_iters:
        responsibilities = item.get("responsibilities") or {}
        for local in _locals(responsibilities.get("classes") or []):
            if local not in classes:
                classes.append(local)
        for local in _locals(responsibilities.get("object_properties") or []):
            if local not in properties:
                properties.append(local)
        for local in _locals(item.get("linked_materialization_classes") or []):
            if local not in linked:
                linked.append(local)
        scope = item.get("semantic_scope") or {}
        for row in scope.get("classes") or []:
            if isinstance(row, dict) and str(row.get("local") or "").strip() not in {
                str(existing.get("local") or "")
                for existing in semantic_classes
                if isinstance(existing, dict)
            }:
                semantic_classes.append(dict(row))
        for row in scope.get("object_properties") or []:
            if isinstance(row, dict) and str(row.get("local") or "").strip() not in {
                str(existing.get("local") or "")
                for existing in semantic_properties
                if isinstance(existing, dict)
            }:
                semantic_properties.append(dict(row))
        prompt = str(
            item.get("kg_building_onepass_prompt")
            or item.get("kg_building_prompt")
            or ""
        ).strip()
        if prompt:
            source_prompt_specs.append(
                {
                    "iteration_number": int(item.get("iteration_number") or 0),
                    "path": prompt,
                }
            )
    last = dict(kg_iters[-1])
    last.update(
        {
            "iteration_number": int(last.get("iteration_number") or 4),
            "name": "full_hints_onepass",
            "full_hints_onepass": True,
            "source_kg_prompt_specs": source_prompt_specs,
            "source_kg_prompts": [row["path"] for row in source_prompt_specs],
            "responsibilities": {
                "classes": classes,
                "object_properties": properties,
            },
            "linked_materialization_classes": linked,
            "semantic_scope": {
                "source": "union_iter2_iter3_iter4",
                "classes": semantic_classes,
                "object_properties": semantic_properties,
            },
        }
    )
    return last


def extract_mcp_tool_lines(prompt_text: str) -> list[str]:
    seen: list[str] = []
    for match in _TOOL_LINE.finditer(prompt_text or ""):
        line = match.group(1).strip()
        if line and line not in seen:
            seen.append(line)
    return seen


def _tool_name(tool_line: str) -> str:
    return str(tool_line or "").split("(", 1)[0].strip()


def _resolve_generated_mcp_scripts_dir(
    *,
    mcp_set_name: str,
    mcp_tools: Iterable[str],
    project_root: str | Path,
) -> Path:
    """Resolve the one generated MCP package selected for this run."""
    root = Path(project_root).resolve()
    config_path = Path(mcp_set_name)
    if not config_path.is_absolute():
        direct = root / config_path
        config_path = direct if direct.is_file() else root / "configs" / config_path
    config = json.loads(config_path.read_text(encoding="utf-8"))
    selected = {str(value).strip() for value in mcp_tools if str(value).strip()}
    specs = [
        spec
        for name, spec in config.items()
        if isinstance(spec, dict) and (not selected or str(name) in selected)
    ]
    if not specs and selected:
        specs = [spec for spec in config.values() if isinstance(spec, dict)]

    resolved_script_dirs: set[Path] = set()
    for spec in specs:
        args = [str(value) for value in spec.get("args") or []]
        scripts_dir: Path | None = None
        if "-m" in args:
            try:
                module_name = args[args.index("-m") + 1]
            except IndexError:
                module_name = ""
            module_path = root.joinpath(*module_name.split(".")).with_suffix(".py")
            if module_path.is_file() and module_path.name == "main.py":
                scripts_dir = module_path.parent.resolve()
        if scripts_dir is None:
            launcher = next(
                (
                    Path(value)
                    for value in args
                    if re.fullmatch(r"_launch_.+_mcp\.py", Path(value).name)
                ),
                None,
            )
            match = (
                re.fullmatch(r"_launch_(.+)_mcp\.py", launcher.name)
                if launcher is not None
                else None
            )
            env = spec.get("env") or {}
            artifact_root = Path(
                str(env.get("TWA_GENERATED_ARTIFACT_ROOT") or "")
            )
            if (
                match is not None
                and artifact_root.is_dir()
                and (artifact_root / "scripts" / match.group(1) / "main.py").is_file()
            ):
                scripts_dir = (
                    artifact_root / "scripts" / match.group(1)
                ).resolve()
        if scripts_dir is None:
            continue
        if scripts_dir in resolved_script_dirs:
            continue
        resolved_script_dirs.add(scripts_dir)
    if len(resolved_script_dirs) != 1:
        raise ValueError(
            "Full-hints one-pass requires exactly one generated MCP package; "
            f"resolved {len(resolved_script_dirs)} from {config_path}"
        )
    return next(iter(resolved_script_dirs))


def resolve_generated_mcp_tool_surface(
    *,
    mcp_set_name: str,
    mcp_tools: Iterable[str],
    project_root: str | Path,
) -> set[str]:
    """Resolve the selected generated MCP's literal public manifests."""
    from src.agents.scripts_and_prompts_generation.artifact_surface_contract import (
        derive_main_surface_contract,
    )

    scripts_dir = _resolve_generated_mcp_scripts_dir(
        mcp_set_name=mcp_set_name,
        mcp_tools=mcp_tools,
        project_root=project_root,
    )
    surface = derive_main_surface_contract(scripts_dir)
    return set(surface.get("expected_mcp_tools") or [])


def resolve_generated_mcp_relationship_contract(
    *,
    mcp_set_name: str,
    mcp_tools: Iterable[str],
    project_root: str | Path,
) -> dict[str, Any]:
    """Load the same relationship contract enforced by the generated runtime."""
    scripts_dir = _resolve_generated_mcp_scripts_dir(
        mcp_set_name=mcp_set_name,
        mcp_tools=mcp_tools,
        project_root=project_root,
    )
    path = scripts_dir / "_relationship_contract.json"
    if not path.is_file():
        raise ValueError(f"Generated MCP relationship contract is missing: {path}")
    contract = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(contract.get("object_properties"), list):
        raise ValueError(f"Malformed generated MCP relationship contract: {path}")
    return contract


def _iri_local(iri: Any) -> str:
    value = str(iri or "").rstrip("/#")
    return re.split(r"[/#:]", value)[-1] if value else ""


def render_runtime_relationship_guidance(
    contract: dict[str, Any],
    *,
    owned_property_names: Iterable[str],
) -> str:
    """Render a compact endpoint contract from the runtime validator artifact."""
    selected = {str(value).strip() for value in owned_property_names if str(value).strip()}
    rows: list[str] = []
    for item in contract.get("object_properties") or []:
        if not isinstance(item, dict):
            continue
        name = _iri_local(item.get("property_iri"))
        if name not in selected:
            continue
        domains = ", ".join(
            value for value in (_iri_local(iri) for iri in item.get("domain_iris") or []) if value
        )
        ranges = ", ".join(
            value for value in (_iri_local(iri) for iri in item.get("range_iris") or []) if value
        )
        rows.append(f"- {name}: subject type [{domains}]; object type [{ranges}].")

    creator_rows: list[str] = []
    for predicate_iri, bindings in (
        contract.get("creator_owned_relationships") or {}
    ).items():
        name = _iri_local(predicate_iri)
        if name not in selected:
            continue
        tools = sorted(
            {
                str(binding.get("public_tool") or "").strip()
                for binding in bindings or []
                if isinstance(binding, dict) and str(binding.get("public_tool") or "").strip()
            }
        )
        if tools:
            creator_rows.append(
                f"- {name}: creator-owned by {', '.join(tools)}; "
                "never issue a separate relationship write."
            )

    if not rows:
        raise ValueError("Runtime relationship contract covers none of the compiled properties")
    creator_text = "\n".join(creator_rows) if creator_rows else "- (none)"
    return (
        "# Runtime-derived relationship endpoint contract\n"
        "This block is generated from the exact `_relationship_contract.json` enforced "
        "by the selected MCP runtime. It overrides contradictory prose.\n"
        "Before every relationship call, type-check both returned IRIs against this "
        "matrix. The bound root IRI is the only valid subject for a relation whose "
        "subject type is the root class; never substitute a step or dependent IRI. "
        "Keep each creator result's `iri` (owner) distinct from `dependent_iri` "
        "(owned dependent).\n\n"
        "Endpoint matrix:\n"
        + "\n".join(rows)
        + "\n\nCreator-owned effects:\n"
        + creator_text
    )


def retarget_official_kg_placeholders(prompt_text: str) -> str:
    """Point official template slots at the one-pass bindings without editing source files."""
    text = prompt_text or ""
    for old, new in _OFFICIAL_PLACEHOLDER_REWRITES:
        text = text.replace(old, new)
    return text


def stack_official_kg_construction(
    *,
    specs: list[dict[str, Any]],
    project_root: str | Path,
    load_prompt: Callable[[str, str], str],
) -> str:
    blocks: list[str] = []
    for spec in specs:
        path = str(spec.get("path") or "").strip()
        if not path:
            continue
        body = retarget_official_kg_placeholders(load_prompt(path, str(project_root))).strip()
        if not body:
            raise ValueError(f"could not load one-pass KG construction fragment: {path}")
        layer = int(spec.get("iteration_number") or 0)
        heading = (
            f"=== Official ITER{layer} KG construction (source prompt, unmodified) ==="
            if layer
            else "=== Official KG construction (source prompt, unmodified) ==="
        )
        blocks.append(f"{heading}\nSource: {path}\n{body}")
    if not blocks:
        raise ValueError("could not load official KG construction prompts")
    return "\n\n".join(blocks)


def build_onepass_kg_prompt(
    *,
    iterations: list[dict[str, Any]],
    project_root: str | Path,
    load_prompt: Callable[[str, str], str],
    allowed_tool_names: Iterable[str] | None = None,
    runtime_relationship_contract: dict[str, Any] | None = None,
) -> str:
    merged = merge_iteration_specs(iterations)
    allowed_tools = (
        {str(value).strip() for value in allowed_tool_names if str(value).strip()}
        if allowed_tool_names is not None
        else None
    )
    specs = list(merged.get("source_kg_prompt_specs") or [])
    if not specs:
        specs = [
            {"iteration_number": 0, "path": path}
            for path in (merged.get("source_kg_prompts") or [])
        ]
    loaded: list[tuple[dict[str, Any], str]] = []
    tool_lines: list[str] = []
    for spec in specs:
        raw = load_prompt(str(spec.get("path") or ""), str(project_root))
        loaded.append((spec, raw))
        for line in extract_mcp_tool_lines(raw):
            name = _tool_name(line)
            if allowed_tools is not None and name not in allowed_tools:
                raise ValueError(
                    f"KG source prompt {spec.get('path')} exposes {name!r}, but the "
                    "selected MCP does not publish that tool. Regenerate the prompt and "
                    "MCP from the same operation contract before running one-pass."
                )
            if line not in tool_lines:
                tool_lines.append(line)
    if not tool_lines:
        if allowed_tools is None:
            raise ValueError("could not extract MCP tools from iteration KG prompts")
        # New generated prompts describe atomic contracts without duplicating the
        # legacy signature-list format. The generated MCP manifest is the
        # authoritative closed-world surface; runtime schemas still provide the
        # exact signatures to the agent.
        tool_lines.extend(sorted(allowed_tools))
    construction = stack_official_kg_construction(
        specs=specs,
        project_root=project_root,
        load_prompt=lambda path, root: next(
            (text for spec, text in loaded if spec.get("path") == path),
            load_prompt(path, root),
        ),
    )
    classes = ", ".join(merged["responsibilities"]["classes"])
    properties = ", ".join(merged["responsibilities"]["object_properties"])
    linked = ", ".join(merged.get("linked_materialization_classes") or [])
    tools = "\n".join(f"  - {line}" for line in tool_lines)
    relationship_guidance = (
        render_runtime_relationship_guidance(
            runtime_relationship_contract,
            owned_property_names=merged["responsibilities"]["object_properties"],
        )
        if runtime_relationship_contract is not None
        else ""
    )
    return (
        "You are the runtime KG-building agent for a single whole-graph pass.\n"
        "This session sees every iteration ledger at once and materializes the "
        "complete ChemicalSynthesis graph. Do not treat the ledgers as sequential "
        "patches.\n\n"
        "Bound runtime inputs (single authoritative bindings):\n"
        "- DOI: {doi}\n"
        "- Top-level ChemicalSynthesis label: {entity_label}\n"
        "- Top-level ChemicalSynthesis IRI: {entity_uri}\n"
        "- All-iteration hints: {iteration_hints}\n\n"
        f"{ONEPASS_CONTRACT}\n\n"
        f"{OFFICIAL_CONSTRUCTION_PREFACE}\n\n"
        f"{relationship_guidance}\n\n"
        f"{construction}\n\n"
        "Compiled ownership (union of iterations 2-4):\n"
        f"- Classes: {classes}.\n"
        f"- Object properties: {properties}.\n"
        f"- Linked materialization classes: {linked or '(none)'}.\n\n"
        "Closed-world MCP tool surface (union; exact names and signatures):\n"
        f"{tools}\n\n"
        "Required sequence:\n"
        "1) Call init_memory with the bound DOI and canonical entity scope.\n"
        "2) Materialize every ledger-supported fact that falls under the compiled ownership. "
        "Track the semantic type and role of every returned IRI; before each relationship "
        "call, verify its subject and object against the runtime-derived endpoint matrix.\n"
        "3) Call export_memory as the final action.\n"
    )


def build_generic_onepass_kg_prompt() -> str:
    """Return the domain-neutral experimental whole-graph construction prompt."""
    return GENERIC_ONEPASS_KG_PROMPT


def build_mcp_native_onepass_task_prompt() -> str:
    """Return a thin task carrying only case bindings and all hint ledgers."""
    return MCP_NATIVE_ONEPASS_TASK_PROMPT


def build_mcp_native_onepass_user_aligned_task_prompt() -> str:
    """Return only the Legacy role + bound-input envelope.

    This is not a draft with a contract slot. BaseAgent loads the MCP
    ``instruction`` prompt and inserts it after this envelope.
    The trailing blank line is load-bearing: ``envelope + contract`` must
    equal the unbound official ONEPASS template.
    """
    return MCP_NATIVE_ONEPASS_USER_ALIGNED_TASK_PROMPT


def build_mcp_semantic_surface_task_prompt() -> str:
    """Return bindings and ledger only; the retained KG contract is not injected."""
    return MCP_SEMANTIC_SURFACE_TASK_PROMPT


def collapse_kg_iterations_for_full_hints_onepass(
    iterations: list[dict[str, Any]],
    *,
    enabled: bool,
) -> list[dict[str, Any]]:
    if not enabled:
        return list(iterations)
    return [merge_iteration_specs(iterations)]
