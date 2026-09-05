from __future__ import annotations

from pathlib import Path

from src.agents.scripts_and_prompts_generation.level1_code_repair import LLMJsonResult
from src.agents.scripts_and_prompts_generation.presence_tool_recipe_judge import (
    DEFAULT_MODEL,
    extract_tool_inventory,
    format_tool_recipe_feedback,
    propose_tool_recipes,
)


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "src/agents/scripts_and_prompts_generation/presence_tool_recipe_judge.py"
)

PROMPT = """
Required MCP tool surface:
  - create_Owner(label: 'str', hasPayloadData: 'str | None' = None) -> 'str'
  - add_hasLink(subject_iri: str, object_iri: str)
  - init_memory(doi: 'str', top_level_entity_name: 'str') -> 'str'
"""


def _invoke(data: dict) -> LLMJsonResult:
    return LLMJsonResult(data=data, elapsed_seconds=0.0, token_usage={})


def test_module_has_no_domain_vocab() -> None:
    source = MODULE_PATH.read_text(encoding="utf-8")
    for token in (
        "ChemicalInput",
        "hasAmount",
        "hasAddedChemicalInput",
        "pubchem",
        "ontosyn",
    ):
        assert token not in source


def test_default_model_is_gpt_4o() -> None:
    assert DEFAULT_MODEL == "gpt-4o"


def test_extract_tool_inventory_from_prompt_and_activity() -> None:
    inventory = extract_tool_inventory(
        PROMPT,
        {"executed_tool_names": ["create_Owner", "export_memory"]},
    )
    names = {row["name"] for row in inventory}
    assert "create_Owner" in names
    assert "add_hasLink" in names
    assert "export_memory" in names
    owner = next(row for row in inventory if row["name"] == "create_Owner")
    assert "hasPayloadData" in owner["signature"]
    assert owner["seen_in_attempt"] == "true"


def test_invented_tool_names_are_dropped() -> None:
    report = propose_tool_recipes(
        missing=[
            {
                "item_id": "Owner: step-1",
                "rebuild_target": "the occurrence created for Payload",
                "missing_work": "include hasPayloadData at create time",
            }
        ],
        inventory=extract_tool_inventory(PROMPT),
        hints_text="Owner: step-1\nhasLink: Payload\nhasPayloadData: 1 g\n",
        invoke=lambda model, prompt: _invoke(
            {
                "recipes": [
                    {
                        "item_id": "Owner: step-1",
                        "sequence": [
                            {
                                "tool": "create_MissingSetter",
                                "arguments": {"value": "1 g"},
                                "why": "invented",
                            },
                            {
                                "tool": "create_Owner",
                                "arguments": {
                                    "label": "Payload",
                                    "hasPayloadData": "1 g",
                                },
                                "why": "create-time parameter",
                            },
                        ],
                        "do_not": "do not create a label-only occurrence",
                    }
                ]
            }
        ),
    )
    tools = [step["tool"] for step in report["recipes"][0]["sequence"]]
    assert tools == ["create_Owner"]
    assert "create_MissingSetter" not in tools


def test_recipe_only_invented_tools_yields_empty() -> None:
    report = propose_tool_recipes(
        missing=[{"item_id": "x", "missing_work": "y"}],
        inventory=extract_tool_inventory(PROMPT),
        hints_text="",
        invoke=lambda model, prompt: _invoke(
            {
                "recipes": [
                    {
                        "item_id": "x",
                        "sequence": [{"tool": "patch_thing", "arguments": {}, "why": ""}],
                        "do_not": "",
                    }
                ]
            }
        ),
    )
    assert report["recipes"] == []


def test_format_tool_recipe_feedback_names_inventory_tools() -> None:
    text = format_tool_recipe_feedback(
        [
            {
                "item_id": "Owner: step-1",
                "sequence": [
                    {
                        "tool": "create_Owner",
                        "arguments": {"label": "Payload", "hasPayloadData": "1 g"},
                        "why": "create-time parameter",
                    }
                ],
                "do_not": "do not create a label-only occurrence",
            }
        ]
    )
    assert "create_Owner" in text
    assert "hasPayloadData" in text
    assert "Do not: do not create a label-only occurrence" in text
    assert "live inventory" in text
