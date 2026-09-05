from __future__ import annotations

from pathlib import Path

from src.agents.scripts_and_prompts_generation.level1_code_repair import LLMJsonResult
from src.agents.scripts_and_prompts_generation.presence_coverage_judge import (
    DEFAULT_MODEL,
    build_presence_coverage_prompt,
    catalog_for_groups,
    format_presence_coverage_feedback,
    hint_item_ledger,
    judge_presence_coverage,
    judge_tool_coverage,
)


JUDGE_PATH = (
    Path(__file__).resolve().parents[1]
    / "src/agents/scripts_and_prompts_generation/presence_coverage_judge.py"
)

TOOL_CATALOG = [
    {
        "name": "identity",
        "any_of": ["search_pubchem_by_name"],
        "hint_markers": ["hasAlternativeNames"],
    },
    {
        "name": "crystal",
        "any_of": ["search_ccdc"],
        "hint_markers": ["CCDC", "deposition"],
    },
]


def _invoke(data: dict) -> LLMJsonResult:
    return LLMJsonResult(data=data, elapsed_seconds=0.0, token_usage={})


def test_judge_module_has_no_domain_or_graph_heuristics() -> None:
    source = JUDGE_PATH.read_text(encoding="utf-8")
    banned = (
        "chemical_name",
        "linked_chemical",
        "ChemicalInput",
        "hasAmount",
        "hasAddedChemicalInput",
        "hasAlternativeNames",
        "pubchem",
        "ccdc",
        "mmol",
        "ontosyn",
        "rdflib",
        "_PROSE_QUANTITY",
        "_is_link_value",
        "_match_linked_node",
    )
    for token in banned:
        assert token not in source


def test_default_model_is_gpt_4o() -> None:
    assert DEFAULT_MODEL == "gpt-4o"


def test_prompt_is_presence_only_and_forbids_iris() -> None:
    prompt = build_presence_coverage_prompt(
        hints_text="Owner: step-1\nhasLink: Payload\n",
        abox_turtle="<urn:owner> a <http://example.org/Owner> .",
        mcp_catalog=[],
    )
    assert "scientifically correct" in prompt
    assert "HINT ITEM LEDGER" in prompt
    assert "Do not sample" in prompt
    assert "heading or evidence prose" in prompt
    assert "label-only node is not enough" in prompt
    assert "rolled back" in prompt
    assert "instance IRI" in prompt
    assert "accepted" in prompt
    assert "ChemicalInput" not in prompt
    assert "hasAmount" not in prompt


def test_llm_accepts_when_missing_lists_are_empty() -> None:
    report = judge_presence_coverage(
        hints_text="Owner: step-1",
        abox_turtle="<urn:owner> a <http://example.org/Owner> .",
        invoke=lambda model, prompt: _invoke(
            {
                "accepted": True,
                "summary": "all required work is present",
                "source_items": [
                    {
                        "item_id": "Owner: step-1",
                        "status": "pass",
                        "rebuild_target": "",
                        "missing_work": "",
                        "same_layer_only": False,
                    }
                ],
                "tool_missing": [],
            }
        ),
    )
    assert report["accepted"] is True
    assert report["judge"] == "llm"
    assert report["model"] == "gpt-4o"
    assert format_presence_coverage_feedback(report) == ""


def test_llm_rejects_and_feedback_uses_rebuild_text_not_iris() -> None:
    report = judge_presence_coverage(
        hints_text="Owner: step-1\nhasLink: Payload\n",
        abox_turtle="<urn:owner> a <http://example.org/Owner> .",
        invoke=lambda model, prompt: _invoke(
            {
                "accepted": False,
                "summary": "linked occurrence missing a source-supported quantity",
                "source_items": [
                    {
                        "item_id": "Owner: step-1",
                        "status": "fail",
                        "rebuild_target": "the occurrence created for `Payload` and linked by `hasLink`",
                        "missing_work": "include the source-supported quantity in that same create call",
                        "same_layer_only": True,
                    }
                ],
                "tool_missing": [],
            }
        ),
    )
    assert report["accepted"] is False
    feedback = format_presence_coverage_feedback(report)
    assert "First rebuild" in feedback
    assert "Payload" in feedback
    assert "hasLink" in feedback
    assert "urn:owner" not in feedback
    assert "http" not in feedback
    assert "same-label occurrence" in feedback


def test_feedback_strips_iris_if_model_emits_them() -> None:
    report = {
        "accepted": False,
        "missing": [
            {
                "item_id": "https://example.org/instance/abc",
                "rebuild_target": "patch https://example.org/instance/abc",
                "missing_work": "set a property",
                "same_layer_only": False,
            }
        ],
        "tool_missing": [],
    }
    feedback = format_presence_coverage_feedback(report)
    assert "https://example.org/instance/abc" not in feedback
    assert "<omitted-instance>" in feedback


def test_configured_groups_apply_without_reading_hint_classes() -> None:
    report = judge_tool_coverage(
        hints_text="",
        tool_activity={"executed_tool_names": []},
        catalog=TOOL_CATALOG,
        require_configured_groups=True,
    )
    assert "identity" in report["applicable_groups"]
    assert report["accepted"] is False


def test_identity_group_required_when_hint_has_configured_marker() -> None:
    report = judge_tool_coverage(
        hints_text="hasAlternativeNames: example",
        tool_activity={"executed_tool_names": [], "tool_outputs": []},
        catalog=TOOL_CATALOG,
    )
    assert report["accepted"] is False
    assert "identity" in report["applicable_groups"]


def test_identity_success_result_is_enough() -> None:
    report = judge_tool_coverage(
        hints_text="hasAlternativeNames: example",
        tool_activity={
            "tool_outputs": [
                {
                    "name": "search_pubchem_by_name",
                    "status": "success",
                    "structured_content": {"ok": True},
                }
            ]
        },
        catalog=TOOL_CATALOG,
    )
    assert report["accepted"] is True


def test_identity_error_result_is_not_done() -> None:
    report = judge_tool_coverage(
        hints_text="hasAlternativeNames: example",
        tool_activity={
            "tool_outputs": [
                {
                    "name": "search_pubchem_by_name",
                    "status": "error",
                    "structured_content": {"ok": False, "error": "timeout"},
                }
            ]
        },
        catalog=TOOL_CATALOG,
    )
    assert report["accepted"] is False


def test_crystal_group_not_required_without_its_markers() -> None:
    report = judge_tool_coverage(
        hints_text="hasAlternativeNames: example",
        tool_activity={"executed_tool_names": ["search_pubchem_by_name"]},
        catalog=TOOL_CATALOG,
    )
    assert "crystal" not in report["applicable_groups"]


def test_crystal_group_required_when_hint_has_its_marker() -> None:
    report = judge_tool_coverage(
        hints_text="hasAlternativeNames: CCDC 2359340",
        tool_activity={"executed_tool_names": []},
        catalog=TOOL_CATALOG,
    )
    assert "crystal" in report["applicable_groups"]
    assert report["accepted"] is False


def test_catalog_for_groups_does_not_invent_domain_tools() -> None:
    groups = catalog_for_groups(["identity"])
    assert groups[0]["any_of"] == ["identity"]


def test_hint_item_ledger_enumerates_every_block() -> None:
    hints = """SEMANTIC_HINTS_V1

Owner - first
hasOrder: 1
hasLink: A

Owner - second
hasOrder: 2
hasLink: B
"""
    ledger = hint_item_ledger(hints)
    assert [row["item_id"] for row in ledger] == ["Owner - first", "Owner - second"]


def test_hint_item_ledger_parses_ref_entity_relations_json() -> None:
    hints = """{
  "entities": [
    {
      "ref": "e_timeline_1",
      "class": "CaseTimeline",
      "label": "CaseTimeline",
      "datatype_properties": {"OP_Datum": "21.08.2024"}
    },
    {
      "ref": "e_patient_1",
      "class": "PatientInfo",
      "label": "Lea Wagner",
      "datatype_properties": {"Name": "Lea Wagner"}
    }
  ],
  "relations": [
    {
      "subject_ref": "https://example.org/case/1",
      "property": "hasTimeline",
      "object_ref": "e_timeline_1"
    }
  ]
}
"""
    ledger = hint_item_ledger(hints)
    assert [row["item_id"] for row in ledger] == [
        "e_timeline_1",
        "e_patient_1",
    ]
    assert ledger[0]["class_hint"] == "CaseTimeline"
    assert ledger[0]["fields"] == [{"key": "OP_Datum", "value": "21.08.2024"}]
    assert "{" not in {row["item_id"] for row in ledger}
    assert all(not str(row["item_id"]).startswith("has") for row in ledger)


def test_omitted_ledger_items_are_filled_after_retry() -> None:
    hints = """SEMANTIC_HINTS_V1

Owner - first
hasOrder: 1
hasLink: A

Owner - second
hasOrder: 2
hasLink: B
"""
    calls: list[str] = []

    def invoke(model: str, prompt: str) -> LLMJsonResult:
        calls.append(prompt)
        return _invoke(
            {
                "accepted": False,
                "summary": "sampled",
                "source_items": [
                    {
                        "item_id": "Owner - first",
                        "status": "fail",
                        "rebuild_target": "A",
                        "missing_work": "include hasLink",
                        "same_layer_only": True,
                    }
                ],
                "tool_missing": [],
            }
        )

    report = judge_presence_coverage(
        hints_text=hints,
        abox_turtle="<urn:owner> a <http://example.org/Owner> .",
        invoke=invoke,
    )
    assert len(calls) == 2
    assert "Omitted ids" in calls[1]
    assert report["hint_coverage"]["checked"] == 2
    missing_ids = {row["item_id"] for row in report["missing"]}
    assert missing_ids == {"Owner - first", "Owner - second"}
    assert report["accepted"] is False
