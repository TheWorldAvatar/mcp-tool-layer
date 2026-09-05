from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

from langchain_core.messages import AIMessage

REPO_ROOT = Path(__file__).resolve().parents[1]
ADAPTER = REPO_ROOT / "baselines" / "ontologx_ontosyn"
if str(ADAPTER) not in sys.path:
    sys.path.insert(0, str(ADAPTER))
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))

from extraction_hints import DEFAULT_HINT_RUNS, hint_dir, set_hint_runs  # noqa: E402
from kg_token_budget import HINT_RUNS, entity_kg_building_budget  # noqa: E402
from parser import OntoSynParser, extract_call_usage  # noqa: E402
from src.pipelines.utils.top_entity_identity import entity_scope_name  # noqa: E402


def test_set_hint_runs_mutates_shared_priority_list() -> None:
    original = list(HINT_RUNS)
    try:
        gpt5 = [
            "20260827_eval30_rest18-ext-gpt5-norev",
            "20260827_eval30_next6-ext-gpt5-norev",
            "20260826_eval30_6case-ext-gpt5-norev",
        ]
        assert set_hint_runs(gpt5) == gpt5
        assert HINT_RUNS == gpt5
        directory, run = hint_dir("f4f7330e")
        assert run == "20260827_eval30_rest18-ext-gpt5-norev"
        assert directory is not None
        assert "rest18-ext-gpt5-norev" in str(directory)
    finally:
        set_hint_runs(original or DEFAULT_HINT_RUNS)


def test_extract_call_usage_reads_langchain_metadata() -> None:
    raw = SimpleNamespace(
        usage_metadata={"input_tokens": 10, "output_tokens": 4, "total_tokens": 14}
    )
    assert extract_call_usage({"raw": raw}) == {
        "prompt_tokens": 10,
        "completion_tokens": 4,
        "total_tokens": 14,
    }


def test_entity_budget_sums_all_kg_traces(tmp_path: Path) -> None:
    label = "VMOP-16"
    uri = "https://example.org/vmop-16"
    scope = entity_scope_name(label, uri)
    mcp = tmp_path / "mcp_run"
    mcp.mkdir()
    (mcp / "iter1_top_entities.json").write_text(
        json.dumps([{"label": label, "uri": uri}]),
        encoding="utf-8",
    )
    for folder, tokens in (
        ("iter2_kg_building", 100),
        ("iter3_kg_building", 250),
        ("iter4_kg_building", 50),
    ):
        dest = tmp_path / "responses" / folder
        dest.mkdir(parents=True)
        (dest / f"{scope}.attempt_1.trace.json").write_text(
            json.dumps({"usage": {"prompt_tokens": tokens, "completion_tokens": 0, "total_tokens": tokens, "calls": 1}}),
            encoding="utf-8",
        )
    cont = tmp_path / "responses" / "iteration_continuity"
    cont.mkdir(parents=True)
    (cont / f"{scope}.continuity_audit.json").write_text(
        json.dumps({"token_usage": {"input_tokens": 20, "output_tokens": 5, "total_tokens": 25}}),
        encoding="utf-8",
    )
    kg_only = entity_kg_building_budget("49613153", label, runtime=tmp_path)
    assert kg_only["total_tokens"] == 400
    assert kg_only["by_dir"]["iter3_kg_building"] == 250
    assert "continuity" not in kg_only["by_dir"]
    with_judge = entity_kg_building_budget(
        "49613153", label, runtime=tmp_path, include_continuity=True
    )
    assert with_judge["total_tokens"] == 425
    assert with_judge["by_dir"]["continuity"] == 25


class _FakeStructured:
    def __init__(self, usages: list[int], parsed) -> None:
        self.usages = list(usages)
        self.parsed = parsed
        self.invokes = 0
        self.messages = []

    def invoke(self, messages):
        self.invokes += 1
        self.messages.append(messages)
        tokens = self.usages.pop(0) if self.usages else 1
        raw = AIMessage(
            content="ok",
            usage_metadata={"input_tokens": tokens, "output_tokens": 0, "total_tokens": tokens},
        )
        return {"parsed": self.parsed, "raw": raw}


def _parser_with_fake(usages: list[int], *, conforms: bool, spend_full: bool = False):
    parser = OntoSynParser.__new__(OntoSynParser)
    parser.ontology_path = REPO_ROOT / "data" / "ontologies" / "ontosynthesis.ttl"
    parser.shacl_path = ADAPTER / "resources" / "ontosynthesis_shacl.ttl"
    parser.prompt = "sys"
    parser.correction_steps = 3
    parser.spend_full_budget = spend_full
    parser.max_rounds = 8
    fake_graph = SimpleNamespace(nodes=[], relationships=[])
    parsed = SimpleNamespace(graph=lambda *_args, **_kwargs: fake_graph)
    parser.structured_model = _FakeStructured(usages, parsed)

    parser._validate_graph = lambda *_args, **_kwargs: (conforms, ["Validation Report"], 0.0)
    return parser


def test_parser_stops_on_shacl_inside_budget() -> None:
    parser = _parser_with_fake([40, 40, 40], conforms=True)
    _graph, conforms, _messages, usage = parser.parse("hint", {"entity_key": "cs1"}, "1b9180ec", token_budget=1000)
    assert conforms
    assert usage.calls == 1
    assert usage.stop_reason == "conforms_within_budget"
    assert usage.total_tokens == 40


def test_parser_can_use_original_ontologx_event_label() -> None:
    parser = _parser_with_fake([40], conforms=True)
    parser.input_label = "Event"

    parser.parse("combined hints", {"entity_key": "cs1"}, "1b9180ec")

    assert parser.structured_model.messages[0][1].content.startswith(
        "Event:\ncombined hints"
    )


def test_parser_keeps_going_until_budget_if_requested() -> None:
    parser = _parser_with_fake([40, 40, 40], conforms=True, spend_full=True)
    _graph, conforms, _messages, usage = parser.parse("hint", {"entity_key": "cs1"}, "1b9180ec", token_budget=100)
    assert conforms
    assert usage.calls >= 2
    assert usage.total_tokens >= 80
    assert usage.stop_reason == "budget_exhausted"


def test_real_hint_run_budget_for_vmop16() -> None:
    detail = entity_kg_building_budget("49613153", "VMOP-16")
    assert detail["total_tokens"] > 100_000
    assert detail["by_dir"]["iter3_kg_building"] > 0


def test_parser_budget_replaces_three_round_cap_when_still_failing() -> None:
    parser = _parser_with_fake([10, 10, 10, 10, 10], conforms=False)
    parser.correction_steps = 3
    _graph, conforms, _messages, usage = parser.parse("hint", {}, "1b9180ec", token_budget=35)
    assert not conforms
    assert usage.calls == 4
    assert usage.stop_reason == "budget_exhausted"


def test_full_hints_correction_requests_complete_graph() -> None:
    parser = _parser_with_fake([10, 10], conforms=False)
    parser.max_rounds = 2
    parser.parse(
        "combined hints",
        {"entity_key": "cs1", "source": "full_hints"},
        "1b9180ec",
        token_budget=100,
    )
    correction_text = parser.structured_model.messages[1][-1].content
    assert "COMPLETE SynthesisGraph" in correction_text
    assert "not a patch" in correction_text
