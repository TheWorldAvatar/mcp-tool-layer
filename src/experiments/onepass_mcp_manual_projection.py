"""Manual ONEPASS-to-MCP projection and mock-agent visibility experiment.

This module deliberately performs no LLM generation.  The complete ONEPASS
contract remains one contiguous MCP instruction; tool descriptions are only
manually curated local indexes into that contract.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from fastmcp import FastMCP
from langchain_core.messages import HumanMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from src.agents.scripts_and_prompts_generation.artifact_surface_contract import (
    derive_main_surface_contract,
)
from src.pipelines.utils.kg_full_hints_onepass import build_onepass_kg_prompt


CONTRACT_BEGIN = "<<<ONEPASS_KG_CONTRACT_BEGIN>>>"
CONTRACT_END = "<<<ONEPASS_KG_CONTRACT_END>>>"
CHUNK_PREFIX = "OP-"


# Hand-authored semantic index.  These are not substitutes for the full
# instruction: every description points back to the contiguous canonical text.
RULES: dict[str, str] = {
    "G1": "Read ITER2/3/4 ledgers as complementary evidence for one graph, not patches.",
    "G2": "Use only source-grounded facts and the active T-Box; never invent missing facts.",
    "G3": "Treat the MCP catalog and schemas as the closed executable surface.",
    "L1": "The host opens memory first and exports once, last, after all graph mutations.",
    "C1": "Create an entity first, retain its returned IRI, then use standalone add_* writers.",
    "I1": "Occurrence-local entities are fresh per supported occurrence; equal labels do not authorize reuse.",
    "I2": "Chemical labels contain identity only; amounts, purity, supplier, and role are fields.",
    "R1": "Reusable identities require the matching check tool and its documented match basis.",
    "R2": "Relationship subjects and objects are absolute returned/bound IRIs in the documented direction.",
    "S1": "Create the most specific evidenced synthesis-step subclass; never a generic placeholder.",
    "S2": "Step hasOrder values follow source order, start at 1, and are contiguous without duplicates.",
    "S3": "Each Add introduces exactly one named material and links one fresh step-local ChemicalInput.",
    "S4": "Washing solvents belong to Filter, separation media to Separate, and removed species to Evaporate.",
    "S5": "Do not infer Stir from generic mixing, atmosphere from absence, or vacuum from sealing.",
    "Q1": "Create OM-2 quantities from the exact source lexeme, then attach with the matching add_* tool.",
    "O1": "Create exactly one ChemicalOutput per ChemicalSynthesis and link its MOP representation.",
    "Y1": "Yield is explicit-only, at most one, never calculated/inherited/normalized.",
    "E1": "Equipment means source-explicit process equipment, not analytical instruments.",
    "V1": "Vessel is synthesis-scoped; vessel type/environment use only authorized reuse and explicit evidence.",
    "F1": "A rejected/failed tool call leaves its obligation unsatisfied and must not be reported as success.",
}

STEP_CREATORS = {
    "create_Add",
    "create_Crystallize",
    "create_Dry",
    "create_Evaporate",
    "create_Filter",
    "create_HeatChill",
    "create_Separate",
    "create_Sonicate",
    "create_Stir",
    "create_Transfer",
}


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _escape_for_langchain_system_template(text: str) -> str:
    """Escape literals consumed by BaseAgent's second template-compilation pass."""
    return text.replace("{", "{{").replace("}", "}}")


def render_canonical_onepass_contract(
    *, artifact_root: str | Path, repository_root: str | Path
) -> str:
    """Render the complete, unbound ONEPASS task template used by the Pipeline."""
    artifact = Path(artifact_root).resolve()
    iterations = json.loads(
        (artifact / "iterations/ontosynthesis/iterations.json").read_text(
            encoding="utf-8"
        )
    )["iterations"]
    relationship_contract = json.loads(
        (artifact / "scripts/ontosynthesis/_relationship_contract.json").read_text(
            encoding="utf-8"
        )
    )
    surface = set(
        derive_main_surface_contract(artifact / "scripts/ontosynthesis")[
            "expected_mcp_tools"
        ]
    )

    def load_artifact_prompt(path: str, _root: str) -> str:
        relative = path.replace("ai_generated_contents/", "", 1)
        return (artifact / relative).read_text(encoding="utf-8")

    return build_onepass_kg_prompt(
        iterations=iterations,
        project_root=repository_root,
        load_prompt=load_artifact_prompt,
        allowed_tool_names=surface,
        runtime_relationship_contract=relationship_contract,
    )


_RUNTIME_BINDING_BLOCK = (
    "Bound runtime inputs (single authoritative bindings):\n"
    "- DOI: {doi}\n"
    "- Top-level ChemicalSynthesis label: {entity_label}\n"
    "- Top-level ChemicalSynthesis IRI: {entity_uri}\n"
    "- All-iteration hints: {iteration_hints}\n\n"
)


def render_invariant_onepass_contract(
    *, artifact_root: str | Path, repository_root: str | Path
) -> str:
    """Render only stable ONEPASS guidance for an MCP instruction.

    DOI, entity identity, and ledger evidence belong exclusively to the user
    task. Keeping their placeholders in the MCP instruction creates a second,
    unresolved runtime envelope and is not behaviorally equivalent.
    """
    template = render_canonical_onepass_contract(
        artifact_root=artifact_root,
        repository_root=repository_root,
    )
    if template.count(_RUNTIME_BINDING_BLOCK) != 1:
        raise ValueError("ONEPASS runtime binding block changed or is ambiguous")
    contract = template.replace(_RUNTIME_BINDING_BLOCK, "", 1)
    unresolved = [
        token
        for token in ("{doi}", "{entity_label}", "{entity_uri}", "{iteration_hints}")
        if token in contract
    ]
    if unresolved:
        raise ValueError(
            "Invariant ONEPASS MCP contract retains runtime placeholders: "
            + ", ".join(unresolved)
        )
    return contract


def render_post_binding_onepass_contract(
    *, artifact_root: str | Path, repository_root: str | Path
) -> str:
    """Render the exact Legacy ONEPASS suffix following its runtime bindings."""
    template = render_canonical_onepass_contract(
        artifact_root=artifact_root,
        repository_root=repository_root,
    )
    _prefix, marker, contract = template.partition(_RUNTIME_BINDING_BLOCK)
    if not marker or _RUNTIME_BINDING_BLOCK in contract:
        raise ValueError("ONEPASS runtime binding block changed or is ambiguous")
    return contract


def _rule_ids_for_tool(name: str) -> tuple[str, ...]:
    """Manual routing from each public tool to its local ONEPASS rules."""
    ids: list[str] = ["G1", "G2", "G3", "F1"]
    if name in {"init_memory", "export_memory"}:
        ids.append("L1")
    elif name.startswith("check_existing_"):
        ids.extend(["R1", "I1"])
    elif name.startswith("add_"):
        ids.extend(["C1", "R2"])
    elif name.startswith("create_"):
        ids.extend(["C1", "I1"])

    if name == "create_ChemicalInput":
        ids.extend(["I2"])
    if name == "create_ChemicalOutput":
        ids.extend(["I2", "O1"])
    if name == "create_MetalOrganicPolyhedron" or name == "add_isRepresentedBy":
        ids.append("O1")
    if name in STEP_CREATORS or name == "add_hasSynthesisStep":
        ids.extend(["S1", "S2"])
    if name in {"create_Add", "add_hasAddedChemicalInput"}:
        ids.append("S3")
    if name in {
        "create_Filter",
        "create_Separate",
        "create_Evaporate",
        "add_hasWashingSolvent",
        "add_hasSeparationSolvent",
        "add_removesSpecies",
    }:
        ids.append("S4")
    if name in {
        "create_Stir",
        "create_HeatChill",
        "create_VesselEnvironment",
        "add_hasVesselEnvironment",
    }:
        ids.append("S5")
    if name == "create_om2_quantity" or name in {
        "add_hasStepDuration",
        "add_hasTargetTemperature",
        "add_hasTemperatureRate",
        "add_hasStirringTemperature",
        "add_hasEvaporationTemperature",
        "add_hasEvaporationPressure",
        "add_isEvaporatedToVolume",
        "add_hasTransferedAmount",
        "add_hasDryingTemperature",
        "add_hasDryingPressure",
        "add_hasCrystallizationTargetTemperature",
        "add_hasYield",
    }:
        ids.append("Q1")
    if name == "add_hasYield":
        ids.append("Y1")
    if "Equipment" in name or name == "add_usesEquipment":
        ids.append("E1")
    if "Vessel" in name or name in {"add_hasVesselType", "add_hasVesselEnvironment"}:
        ids.append("V1")
    return tuple(dict.fromkeys(ids))


def manual_tool_descriptions(tool_names: Iterable[str]) -> dict[str, str]:
    """Create deterministic local indexes without generating or paraphrasing by LLM."""
    descriptions: dict[str, str] = {}
    for name in sorted(set(tool_names)):
        rule_ids = _rule_ids_for_tool(name)
        local_rules = " ".join(f"[{rule_id}] {RULES[rule_id]}" for rule_id in rule_ids)
        descriptions[name] = (
            f"{name}. The complete authoritative ONEPASS contract is in the MCP "
            f"instruction; this description is a local index only. {local_rules}"
        )
    return descriptions


def build_mcp_instruction(canonical_contract: str) -> str:
    """Keep the whole source contract contiguous and hash-addressable."""
    digest = _sha256(canonical_contract)
    raw = (
        "MCP-native ONEPASS contract. The current task message supplies the concrete "
        "DOI, root label/IRI, and ITER2/3/4 ledgers. Treat the following contiguous "
        "contract as authoritative system guidance; tool descriptions are redundant "
        "local indexes and never replace it.\n"
        f"canonical_sha256={digest}\n"
        f"{CONTRACT_BEGIN}\n{canonical_contract}\n{CONTRACT_END}"
    )
    return _escape_for_langchain_system_template(raw)


def _encoded_contract_lines(canonical_contract: str) -> list[str]:
    """Encode every source line with a stable global sequence number."""
    return [
        f"[{CHUNK_PREFIX}{index:05d}]{line}"
        for index, line in enumerate(canonical_contract.splitlines(), start=1)
    ]


def distributed_projection(
    canonical_contract: str, tool_names: Iterable[str]
) -> tuple[str, dict[str, str]]:
    """Losslessly split exact tool-naming lines into corresponding descriptions.

    A line mentioning several concrete tools is copied to each relevant
    description.  Sequence IDs are global, so duplicate copies collapse and the
    original contract can be reconstructed in source order.
    """
    names = sorted(set(tool_names), key=lambda value: (-len(value), value))
    encoded = _encoded_contract_lines(canonical_contract)
    global_lines: list[str] = []
    by_tool: dict[str, list[str]] = {name: [] for name in names}
    for raw_line, encoded_line in zip(canonical_contract.splitlines(), encoded):
        mentioned = [
            name
            for name in names
            if re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", raw_line)
        ]
        if not mentioned:
            global_lines.append(encoded_line)
            continue
        for name in mentioned:
            by_tool[name].append(encoded_line)

    digest = _sha256(canonical_contract)
    trailing_newline = int(canonical_contract.endswith("\n"))
    instruction = (
        "MCP-native distributed ONEPASS contract. The current task supplies concrete "
        "bindings and ledgers. Read this instruction together with every tool "
        "description; [OP-nnnnn] is the original global order. Lines containing "
        "specific public tool names are located in those tools' descriptions; all "
        "other lines remain here. Sorting the union by OP number reconstructs the "
        "authoritative contract exactly.\n"
        f"canonical_sha256={digest}\n"
        f"canonical_trailing_newline={trailing_newline}\n"
        + "\n".join(global_lines)
    )
    instruction = _escape_for_langchain_system_template(instruction)
    descriptions = {
        name: (
            "Exact tool-associated excerpts from the ONEPASS contract. Interpret "
            "their OP numbers in the global order declared by the MCP instruction.\n"
            + "\n".join(lines)
        )
        for name, lines in by_tool.items()
    }
    return instruction, descriptions


def _import_runtime_modules(artifact_package: str) -> tuple[Any, Any, Any, Any]:
    base = f"{artifact_package}.scripts.ontosynthesis"
    return (
        importlib.import_module(f"{base}.ontosynthesis_creation_entities"),
        importlib.import_module(f"{base}.ontosynthesis_creation_relationships"),
        importlib.import_module(f"{base}.ontosynthesis_creation_checks"),
        importlib.import_module(f"{base}._fixed_rdf_runtime"),
    )


def build_projection_mcp(
    *,
    canonical_contract: str,
    artifact_package: str = "ai_generated_contents_legacy_split_20260901",
    mode: str = "full_instruction",
) -> FastMCP:
    """Build an isolated split MCP using one of the two projection experiments."""
    entities, relationships, checks, runtime = _import_runtime_modules(artifact_package)
    functions: dict[str, Any] = {}
    for module in (entities, relationships, checks):
        for name in getattr(module, "__all__", []):
            functions[name] = getattr(module, name)
    functions["init_memory"] = runtime.init_memory
    functions["export_memory"] = runtime.export_memory

    if mode == "full_instruction":
        instruction_text = build_mcp_instruction(canonical_contract)
        descriptions: dict[str, str] | None = None
    elif mode == "full_instruction_user_aligned":
        # BaseAgent inserts this exact suffix into the HumanMessage marker.
        # No wrapper/hash text is exposed to the model in this control.
        instruction_text = canonical_contract
        descriptions = None
    elif mode == "distributed_tool_descriptions":
        instruction_text, descriptions = distributed_projection(
            canonical_contract, functions
        )
    else:
        raise ValueError(f"Unknown ONEPASS MCP projection mode: {mode}")

    mcp = FastMCP(name="ontosynthesis-onepass-projection-experiment")
    for name, function in functions.items():
        if descriptions is None:
            # Scheme A adds no generated/manual description. FastMCP retains only
            # the function's original docstring and its real input schema.
            mcp.tool(name=name)(function)
        else:
            mcp.tool(name=name, description=descriptions[name])(function)

    @mcp.prompt(name="instruction")
    def instruction() -> str:
        return instruction_text

    return mcp


@dataclass(frozen=True)
class MockAgentView:
    system_instruction: str
    user_task: str
    tool_catalog: dict[str, dict[str, Any]]

    def recover_canonical_contract(self) -> str:
        before, marker, remainder = self.system_instruction.partition(CONTRACT_BEGIN)
        if not marker:
            raise ValueError("MCP instruction has no canonical contract start marker")
        contract, marker, _after = remainder.partition(CONTRACT_END)
        if not marker:
            raise ValueError("MCP instruction has no canonical contract end marker")
        # build_mcp_instruction adds exactly one framing newline on each side.
        # Remove only those framing bytes; the canonical template itself ends in
        # a newline and must remain byte-for-byte intact for hash equivalence.
        if not contract.startswith("\n") or not contract.endswith("\n"):
            raise ValueError("MCP instruction canonical contract framing is malformed")
        contract = contract[1:-1]
        declared = next(
            (
                line.split("=", 1)[1]
                for line in before.splitlines()
                if line.startswith("canonical_sha256=")
            ),
            "",
        )
        if declared != _sha256(contract):
            raise ValueError("MCP instruction canonical contract hash mismatch")
        return contract


def recover_distributed_contract(view: MockAgentView) -> str:
    """Reassemble scheme B using only MCP-visible instruction/descriptions."""
    chunks: dict[int, str] = {}
    pattern = re.compile(rf"^\[{CHUNK_PREFIX}(\d{{5}})\](.*)$")
    texts = [view.system_instruction] + [
        str(tool.get("description") or "") for tool in view.tool_catalog.values()
    ]
    for text in texts:
        for line in text.splitlines():
            match = pattern.match(line)
            if not match:
                continue
            sequence = int(match.group(1))
            payload = match.group(2)
            previous = chunks.setdefault(sequence, payload)
            if previous != payload:
                raise ValueError(
                    f"Conflicting distributed ONEPASS chunk OP-{sequence:05d}"
                )
    if not chunks or sorted(chunks) != list(range(1, max(chunks) + 1)):
        raise ValueError("Distributed ONEPASS chunks are incomplete")
    contract = "\n".join(chunks[index] for index in sorted(chunks))
    trailing = next(
        (
            line.split("=", 1)[1]
            for line in view.system_instruction.splitlines()
            if line.startswith("canonical_trailing_newline=")
        ),
        "0",
    )
    if trailing == "1":
        contract += "\n"
    declared = next(
        (
            line.split("=", 1)[1]
            for line in view.system_instruction.splitlines()
            if line.startswith("canonical_sha256=")
        ),
        "",
    )
    if declared != _sha256(contract):
        raise ValueError("Distributed MCP projection canonical hash mismatch")
    return contract


async def expose_to_mock_agent(mcp: FastMCP, *, user_task: str) -> MockAgentView:
    """Observe only what a tool-calling agent receives from the MCP surface."""
    prompt = await mcp.get_prompt("instruction")
    messages = await prompt.render()
    mcp_instruction = "\n".join(
        str(message.content.text)
        for message in messages
        if getattr(message.content, "text", None) is not None
    )
    # BaseAgent compiles MCP prompt text once more as a LangChain system
    # template.  Simulate that exact visibility boundary, including literal
    # brace unescaping, instead of inspecting the pre-Agent MCP payload.
    template = ChatPromptTemplate.from_messages(
        [("system", mcp_instruction), MessagesPlaceholder("messages")]
    )
    rendered = template.format_messages(messages=[HumanMessage(content=user_task)])
    instruction = str(rendered[0].content)
    tools = await mcp.get_tools()
    catalog = {
        name: {
            "description": str(tool.description or ""),
            "input_schema": dict(tool.parameters or {}),
        }
        for name, tool in sorted(tools.items())
    }
    return MockAgentView(
        system_instruction=instruction,
        user_task=user_task,
        tool_catalog=catalog,
    )


def projection_report(
    *, canonical_contract: str, view: MockAgentView
) -> dict[str, Any]:
    """Return strict equivalence evidence for the manual projection."""
    recovered = view.recover_canonical_contract()
    missing_descriptions = [
        name
        for name, tool in view.tool_catalog.items()
        if not str(tool.get("description") or "").strip()
    ]
    exposed_rule_ids = {
        rule_id
        for tool in view.tool_catalog.values()
        for rule_id in RULES
        if f"[{rule_id}]" in str(tool.get("description") or "")
    }
    return {
        "equivalent": recovered == canonical_contract and not missing_descriptions,
        "canonical_sha256": _sha256(canonical_contract),
        "recovered_sha256": _sha256(recovered),
        "canonical_contract_exactly_recovered": recovered == canonical_contract,
        "instruction_contract_copy_count": view.system_instruction.count(
            canonical_contract
        ),
        "tool_count": len(view.tool_catalog),
        "missing_descriptions": missing_descriptions,
        "locally_indexed_rule_ids": sorted(exposed_rule_ids),
        "user_task_visible": bool(view.user_task.strip()),
    }
