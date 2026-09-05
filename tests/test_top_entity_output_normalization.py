import asyncio
import json
from pathlib import Path

import pytest
from rdflib import Graph, Literal, RDF, RDFS, URIRef

from src.pipelines.utils import top_entity_identity
from src.pipelines.top_entity_extraction.extract import (
    _append_conservative_top_class_gate,
    _apply_top_class_membership_checks,
    _split_outcome_reminder,
    _build_top_class_membership_prompt,
    bind_paper_content,
    _format_membership_candidate_set,
    _format_top_entity_feedback_history,
    _load_tbox_class_comment,
    _normalize_top_entity_output,
    _persist_and_validate_top_class_selection,
    _resolve_selected_top_class_iri,
    _run_top_class_membership_judge,
    _run_top_class_omission_judge,
    _top_candidate_identity_key,
    _top_entity_line_prefixes,
    _top_entity_semantic_audit_errors,
)
from src.pipelines.top_entity_kg_building.build import (
    _materialize_supplemented_top_entities,
    _merge_txt_top_entity_fallback,
    _mint_top_entity_iri,
    _reset_iter1_shared_persistence,
    _seed_iter1_top_entity_lock,
    parse_top_entities_from_ttl,
)
from src.pipelines.utils.top_entity_identity import (
    attach_entity_identity_dossiers,
    entity_scope_name,
    hydrate_and_validate_top_entity_types,
    persist_entity_identity_sidecars,
)


def test_top_entity_iri_is_short_and_deterministic() -> None:
    first = _mint_top_entity_iri(
        "  Example   Synthesis  ",
        "https://example.com/ChemicalSynthesis",
    )
    second = _mint_top_entity_iri(
        "example synthesis",
        "https://example.com/ChemicalSynthesis",
    )

    assert first == second
    assert len(first.rsplit("/", 1)[-1]) == 16


def test_top_entity_feedback_history_preserves_all_failures() -> None:
    rendered = _format_top_entity_feedback_history(
        ["missing synthesis route B", "excluded characterization sample retained"]
    )

    assert "ATTEMPT 1" in rendered
    assert "missing synthesis route B" in rendered
    assert "ATTEMPT 2" in rendered
    assert "excluded characterization sample retained" in rendered
    assert "do not regress" in rendered


def test_top_entity_extraction_gate_requires_positive_class_evidence() -> None:
    rendered = _append_conservative_top_class_gate("Extract selected entities.")

    assert "not by itself" in rendered
    assert "positive source evidence" in rendered
    assert "applicable exclusion" in rendered
    assert "omit the candidate" in rendered
    assert "SPLIT OUTCOMES" in rendered
    assert "extract ONLY those named outcomes" in rendered
    assert "Do not extract the parent" in rendered
    assert "parent or family label" in rendered


def test_split_outcome_reminder_forbids_parent_when_routes_are_named() -> None:
    reminder = _split_outcome_reminder()

    assert "independently executed outcomes" in reminder
    assert "extract ONLY those named outcomes" in reminder
    assert "The parent is not a member" in reminder
    assert "keep only the named outcomes" in reminder


def test_top_entity_contract_loader_includes_selected_class_comment() -> None:
    class_iri = _resolve_selected_top_class_iri(
        "configs/meta_task/meta_task_config.json",
        "ontosynthesis",
        "ChemicalSynthesis",
    )
    class_contract = _load_tbox_class_comment(
        "configs/meta_task/meta_task_config.json",
        "ontosynthesis",
        class_iri,
    )

    assert class_iri.endswith("/ChemicalSynthesis")
    assert "[Definition]" in class_contract
    assert "[Scope]" in class_contract


def test_top_class_membership_checks_fail_closed_per_candidate() -> None:
    candidate_text = (
        "Target-1 [qualified item]\n"
        "Target-2 [described but excluded item]\n"
        "Target-3 [missing judge decision]\n"
    )
    source = (
        "The qualified item is explicitly finite. "
        "The described but excluded item has a preparation heading but is extended."
    )
    contract = "Keep explicitly finite items. Exclude extended items."
    filtered, checks = _apply_top_class_membership_checks(
        candidate_text=candidate_text,
        judge_payload={
            "candidate_checks": [
                {
                    "candidate_id": "candidate_1",
                    "decision": "keep",
                    "source_evidence": (
                        "qualified item is explicitly finite\n"
                        "Additional noncontiguous discussion."
                    ),
                    "class_contract_evidence": (
                        "Keep explicitly finite items.\n"
                        "Another contract boundary."
                    ),
                    "exclusion_status": "cleared",
                    "ambiguity_status": "resolved",
                    "reason": "The defining characteristic is explicit.",
                },
                {
                    "candidate_id": "candidate_2",
                    "decision": "keep",
                    "source_evidence": "preparation heading",
                    "class_contract_evidence": "Exclude extended items.",
                    "exclusion_status": "triggered",
                    "ambiguity_status": "resolved",
                    "reason": "The source explicitly triggers an exclusion.",
                },
            ]
        },
        source_text=source,
        top_class_comment=contract,
    )

    assert filtered == "Target-1 [qualified item]\n"
    assert [item["effective_decision"] for item in checks] == [
        "keep",
        "remove",
        "remove",
    ]


def test_top_class_membership_records_ungrounded_source_without_veto() -> None:
    filtered, checks = _apply_top_class_membership_checks(
        candidate_text="Target-1 [IRMOP-50]\n",
        judge_payload={
            "candidate_checks": [
                {
                    "candidate_id": "candidate_1",
                    "decision": "keep",
                    "source_evidence": (
                        "The synthesis of this series of metal-organic "
                        "polyhedra employs sulfate-capped nodes."
                    ),
                    "class_contract_evidence": "Keep explicitly finite items.",
                    "exclusion_status": "cleared",
                    "ambiguity_status": "resolved",
                    "reason": "The source describes a standalone IRMOP-50 synthesis.",
                }
            ]
        },
        source_text="A few orange octahedral crystals of IRMOP-50 formed.",
        top_class_comment="Keep explicitly finite items.",
    )

    assert filtered == "Target-1 [IRMOP-50]\n"
    assert checks[0]["effective_decision"] == "keep"
    assert checks[0]["requested_decision"] == "keep"
    assert checks[0]["source_evidence_grounded"] is False


def test_top_class_membership_records_ungrounded_contract_without_veto() -> None:
    filtered, checks = _apply_top_class_membership_checks(
        candidate_text="Target-1 [qualified item]\n",
        judge_payload={
            "candidate_checks": [
                {
                    "candidate_id": "candidate_1",
                    "decision": "keep",
                    "source_evidence": "qualified item is explicitly finite",
                    "class_contract_evidence": "This quote is not in the contract.",
                    "exclusion_status": "cleared",
                    "ambiguity_status": "resolved",
                    "reason": "The defining characteristic is explicit.",
                }
            ]
        },
        source_text="The qualified item is explicitly finite.",
        top_class_comment="Keep explicitly finite items.",
    )

    assert filtered == "Target-1 [qualified item]\n"
    assert checks[0]["effective_decision"] == "keep"
    assert checks[0]["source_evidence_grounded"] is True
    assert checks[0]["class_contract_evidence_grounded"] is False


@pytest.mark.asyncio
async def test_top_class_membership_judge_retries_incomplete_candidate_coverage() -> None:
    class FakeLlm:
        def __init__(self) -> None:
            self.calls: dict[str, int] = {}
            self.active = 0
            self.max_active = 0

        async def ainvoke(self, prompt: str) -> str:
            start = prompt.index("<<<CANDIDATE\n") + len("<<<CANDIDATE\n")
            candidate_id = prompt[start:].split(":", 1)[0].strip()
            self.calls[candidate_id] = self.calls.get(candidate_id, 0) + 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            await asyncio.sleep(0.01)
            self.active -= 1
            if self.calls[candidate_id] == 1:
                return json.dumps({"candidate_checks": []})
            assert "structurally invalid" in prompt
            label = "First item" if candidate_id == "candidate_1" else "Second item"
            return json.dumps(
                {
                    "candidate_checks": [
                        {
                            "candidate_id": candidate_id,
                            "decision": "keep",
                            "source_evidence": f"{label} is explicitly finite.",
                            "class_contract_evidence": "Keep explicitly finite items.",
                            "exclusion_status": "cleared",
                            "ambiguity_status": "resolved",
                            "reason": "The defining finite characteristic is explicit.",
                        }
                    ]
                }
            )

    llm = FakeLlm()
    filtered, report = await _run_top_class_membership_judge(
        llm=llm,
        candidate_text="Target-1 [First item]\nTarget-2 [Second item]\n",
        source_text="First item is explicitly finite. Second item is explicitly finite.",
        top_class_iri="https://example.com/Target",
        top_class_comment="Keep explicitly finite items.",
    )

    assert llm.calls == {"candidate_1": 2, "candidate_2": 2}
    assert llm.max_active == 2
    assert filtered == "Target-1 [First item]\nTarget-2 [Second item]\n"
    assert report["accepted_count"] == 2
    assert [item["attempts"] for item in report["per_candidate_calls"]] == [2, 2]


def test_membership_prompt_includes_full_set_and_unsplit_prefix_rule() -> None:
    candidate_lines = [
        "Target-1 [Family product]",
        "Target-2 [Family product (route A)]",
        "Target-3 [Family product (route B)]",
    ]
    prompt = _build_top_class_membership_prompt(
        top_class_iri="https://example.com/Target",
        top_class_comment="Keep explicitly finite items.",
        candidate_id="candidate_1",
        candidate_line=candidate_lines[0],
        candidate_set=_format_membership_candidate_set(candidate_lines),
        source_text="Shared prefix then route A and route B.",
    )

    assert "<<<CANDIDATE_SET" in prompt
    assert "candidate_1: Target-1 [Family product]" in prompt
    assert "candidate_2: Target-2 [Family product (route A)]" in prompt
    assert "candidate_3: Target-3 [Family product (route B)]" in prompt
    assert "Candidate under review:" in prompt
    assert "unsplit prefix or family label" in prompt
    assert "not already on the list, do not" in prompt
    assert "set is not emptied" in prompt
    assert "Never prefer the parent or family label" in prompt
    assert "SPLIT OUTCOMES" in prompt


@pytest.mark.asyncio
async def test_membership_judge_removes_unsplit_prefix_only_when_outcomes_listed() -> None:
    source = (
        "A shared mixture is prepared. For route A the mixture is heated to give "
        "Family product (route A). For route B the mixture is sealed to give "
        "Family product (route B)."
    )
    contract = (
        "When one continuous source passage produces a shared intermediate and then "
        "names N distinct outcomes, create exactly N instances — one per named "
        "outcome. Do not also keep a parent or family-level entity for the unsplit "
        "prefix. Keep explicitly finite items."
    )

    class FakeLlm:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        async def ainvoke(self, prompt: str) -> str:
            self.prompts.append(prompt)
            start = prompt.index("<<<CANDIDATE\n") + len("<<<CANDIDATE\n")
            reviewed = prompt[start:].split(":", 1)[0].strip()
            set_block = prompt.split("<<<CANDIDATE_SET\n", 1)[1].split(
                "\nCANDIDATE_SET", 1
            )[0]
            outcomes_listed = (
                "Family product (route A)" in set_block
                and "Family product (route B)" in set_block
            )
            is_prefix = "Target-1 [Family product]" in prompt.split(
                "<<<CANDIDATE\n", 1
            )[1].split("\nCANDIDATE", 1)[0]
            remove_prefix = is_prefix and outcomes_listed
            return json.dumps(
                {
                    "candidate_checks": [
                        {
                            "candidate_id": reviewed,
                            "decision": "remove" if remove_prefix else "keep",
                            "source_evidence": (
                                "For route A the mixture is heated to give "
                                "Family product (route A). For route B the mixture "
                                "is sealed to give Family product (route B)."
                            ),
                            "class_contract_evidence": (
                                "Do not also keep a parent or family-level entity "
                                "for the unsplit prefix."
                            ),
                            "exclusion_status": (
                                "triggered" if remove_prefix else "cleared"
                            ),
                            "ambiguity_status": "resolved",
                            "reason": (
                                "More specific named outcomes are already listed, "
                                "so the unsplit family label is not a member."
                                if remove_prefix
                                else "This identity is a source-supported outcome "
                                "or the only listed handle for the passage."
                            ),
                        }
                    ]
                }
            )

    llm = FakeLlm()
    with_children, with_report = await _run_top_class_membership_judge(
        llm=llm,
        candidate_text=(
            "Target-1 [Family product]\n"
            "Target-2 [Family product (route A)]\n"
            "Target-3 [Family product (route B)]\n"
        ),
        source_text=source,
        top_class_iri="https://example.com/Target",
        top_class_comment=contract,
    )
    without_children, without_report = await _run_top_class_membership_judge(
        llm=llm,
        candidate_text="Target-1 [Family product]\n",
        source_text=source,
        top_class_iri="https://example.com/Target",
        top_class_comment=contract,
    )

    assert with_children == (
        "Target-2 [Family product (route A)]\n"
        "Target-3 [Family product (route B)]\n"
    )
    assert [item["effective_decision"] for item in with_report["candidate_checks"]] == [
        "remove",
        "keep",
        "keep",
    ]
    assert without_children == "Target-1 [Family product]\n"
    assert without_report["accepted_count"] == 1
    assert all("<<<CANDIDATE_SET" in prompt for prompt in llm.prompts)
    assert all(
        "unsplit prefix or family label" in prompt for prompt in llm.prompts
    )


@pytest.mark.asyncio
async def test_top_class_omission_judge_adds_only_grounded_novel_candidates() -> None:
    source = (
        "Existing item is explicitly finite. "
        "Missing item is explicitly finite and independently prepared."
    )
    contract = "Keep explicitly finite items. Exclude extended items."

    class FakeLlm:
        async def ainvoke(self, prompt: str) -> str:
            assert "exhaustive recall audit" in prompt
            assert "Existing item" in prompt
            assert "SPLIT OUTCOMES" in prompt
            assert "named outcomes are the members to recall" in prompt
            return json.dumps(
                {
                    "missing_candidates": [
                        {
                            "candidate_label": "Missing item",
                            "source_evidence": (
                                "Missing item is explicitly finite and independently prepared."
                            ),
                            "class_contract_evidence": "Keep explicitly finite items.",
                            "exclusion_status": "cleared",
                            "ambiguity_status": "resolved",
                            "reason": "The distinct omitted item satisfies the defining boundary.",
                        },
                        {
                            "candidate_label": "Existing item",
                            "source_evidence": "Existing item is explicitly finite.",
                            "class_contract_evidence": "Keep explicitly finite items.",
                            "exclusion_status": "cleared",
                            "ambiguity_status": "resolved",
                            "reason": "This is already present.",
                        },
                        {
                            "candidate_label": "Ungrounded item",
                            "source_evidence": "This quote does not occur.",
                            "class_contract_evidence": "Keep explicitly finite items.",
                            "exclusion_status": "cleared",
                            "ambiguity_status": "resolved",
                            "reason": "The proposed item lacks grounded source evidence.",
                        },
                    ]
                }
            )

    augmented, report = await _run_top_class_omission_judge(
        llm=FakeLlm(),
        candidate_text="Target-1 [Existing item]\n",
        source_text=source,
        top_class_iri="https://example.test/Target",
        top_class_comment=contract,
        line_prefixes=["Target"],
    )

    assert augmented == (
        "Target-1 [Existing item]\n"
        "Target-2 [Missing item]\n"
    )
    assert report["added_count"] == 1
    assert [item["effective_decision"] for item in report["candidate_checks"]] == [
        "add",
        "reject",
        "reject",
    ]


@pytest.mark.asyncio
async def test_top_class_omission_records_ungrounded_contract_without_veto() -> None:
    source = "The missing item is explicitly finite."
    contract = "Keep explicitly finite items."

    class FakeLlm:
        async def ainvoke(self, prompt: str) -> str:
            return json.dumps(
                {
                    "missing_candidates": [
                        {
                            "candidate_label": "Missing item",
                            "source_evidence": "The missing item is explicitly finite.",
                            "class_contract_evidence": "This quote is not in the contract.",
                            "exclusion_status": "cleared",
                            "ambiguity_status": "resolved",
                            "reason": "The distinct omitted item satisfies the defining boundary.",
                        }
                    ]
                }
            )

    augmented, report = await _run_top_class_omission_judge(
        llm=FakeLlm(),
        candidate_text="Target-1 [Existing item]\n",
        source_text=source,
        top_class_iri="https://example.test/Target",
        top_class_comment=contract,
        line_prefixes=["Target"],
    )

    assert augmented == (
        "Target-1 [Existing item]\n"
        "Target-2 [Missing item]\n"
    )
    assert report["added_count"] == 1
    assert report["candidate_checks"][0]["effective_decision"] == "add"
    assert report["candidate_checks"][0]["source_evidence_grounded"] is True
    assert report["candidate_checks"][0]["class_contract_evidence_grounded"] is False


def test_top_candidate_identity_key_unwraps_only_listing_wrapper() -> None:
    listed = (
        "ChemicalSynthesis-4 [Synthesis of (TMA)8{[V6O6(OCH3)9(SO4)]4(NDBDC)6} "
        "(TMA-VMOT-2)]"
    )
    bare_same = (
        "Synthesis of (TMA)8{[V6O6(OCH3)9(SO4)]4(NDBDC)6} (TMA-VMOT-2)"
    )
    bare_other = (
        "Synthesis of (TMA)8{[V6O6(OCH3)9(SO4)]4(ADBDC)6} (TMA-VMOT-3)"
    )

    assert _top_candidate_identity_key(listed) == _top_candidate_identity_key(bare_same)
    assert _top_candidate_identity_key(listed) != _top_candidate_identity_key(bare_other)
    assert _top_candidate_identity_key("Target-1 [Existing item]") == (
        _top_candidate_identity_key("Existing item")
    )


@pytest.mark.asyncio
async def test_top_class_omission_judge_keeps_labels_that_share_inner_brackets() -> None:
    source = (
        "Product A uses unit [V6O6(OCH3)9(SO4)] with NDBDC. "
        "Product B uses unit [V6O6(OCH3)9(SO4)] with ADBDC."
    )
    contract = "Keep explicitly named products. Exclude aliases of listed products."

    class FakeLlm:
        async def ainvoke(self, prompt: str) -> str:
            return json.dumps(
                {
                    "missing_candidates": [
                        {
                            "candidate_label": (
                                "Product B uses unit [V6O6(OCH3)9(SO4)] with ADBDC"
                            ),
                            "source_evidence": (
                                "Product B uses unit [V6O6(OCH3)9(SO4)] with ADBDC."
                            ),
                            "class_contract_evidence": "Keep explicitly named products.",
                            "exclusion_status": "cleared",
                            "ambiguity_status": "resolved",
                            "reason": "A distinct named product is absent from the list.",
                        }
                    ]
                }
            )

    augmented, report = await _run_top_class_omission_judge(
        llm=FakeLlm(),
        candidate_text=(
            "Target-1 [Product A uses unit [V6O6(OCH3)9(SO4)] with NDBDC]\n"
        ),
        source_text=source,
        top_class_iri="https://example.test/Target",
        top_class_comment=contract,
        line_prefixes=["Target"],
    )

    assert "Product B uses unit [V6O6(OCH3)9(SO4)] with ADBDC" in augmented
    assert report["added_count"] == 1
    assert report["candidate_checks"][0]["effective_decision"] == "add"
    assert report["candidate_checks"][0]["duplicate_of_existing"] is False


def test_top_entity_audit_ignores_ungrounded_scope_expansion() -> None:
    contract = "Only target cages qualify. Exclude frameworks and ligands."
    report = {
        "judges": [
            {
                "deductions": [
                    {
                        "amount": 0.1,
                        "dimension": "coverage",
                        "ontology_evidence": "All chemical processes qualify.",
                        "reason": "framework route missing",
                    },
                    {
                        "amount": 0.1,
                        "dimension": "semantic_correctness",
                        "ontology_evidence": "Exclude frameworks and ligands.",
                        "reason": "framework candidate retained",
                    },
                ],
                "critical_errors": [],
            }
        ]
    }

    errors = _top_entity_semantic_audit_errors(
        report, top_class_comment=contract
    )

    assert errors == ["semantic_correctness: framework candidate retained"]


def test_identity_dossier_preserves_only_explicit_iter1_neighbourhood(
    tmp_path: Path,
) -> None:
    entity = URIRef("https://example.com/synthesis/1")
    document = URIRef("https://example.com/document/1")
    top_type = URIRef("https://example.com/TopEntity")
    retrieved_from = URIRef("https://example.com/retrievedFrom")
    graph = Graph()
    graph.add((entity, RDF.type, top_type))
    graph.add((entity, RDFS.label, Literal("specific route")))
    graph.add((entity, retrieved_from, document))
    graph.add((document, RDFS.label, Literal("route-specific source context")))
    ttl_path = tmp_path / "iteration_1.ttl"
    graph.serialize(destination=ttl_path, format="turtle")

    result = attach_entity_identity_dossiers(
        entities=[
            {
                "uri": str(entity),
                "label": "specific route",
                "types": [str(top_type)],
                "scope_index": 1,
                "source_anchor": "TopEntity-1 [specific route]",
            }
        ],
        iteration_1_ttl=str(ttl_path),
    )

    dossier = result[0]["identity_dossier"]
    assert dossier["scope_index"] == 1
    assert dossier["source_anchor"] == "TopEntity-1 [specific route]"
    assert dossier["explicit_iteration_1_facts"] == [
        {
            "predicate_iri": str(retrieved_from),
            "value_kind": "iri",
            "object_iri": str(document),
            "object_labels": ["route-specific source context"],
            "object_types": [],
        }
    ]


def test_pipeline_binds_source_when_top_entity_prompt_omits_slot() -> None:
    rendered = bind_paper_content(
        "Extract the top-level entity under the T-Box rules.",
        "Source procedure text.",
    )

    assert "Extract the top-level entity" in rendered
    assert "---- PIPELINE-INJECTED SOURCE TEXT: BEGIN ----" in rendered
    assert "Source procedure text." in rendered


def test_pipeline_replaces_declared_top_entity_source_slot() -> None:
    rendered = bind_paper_content(
        "Source:\n{paper_content}",
        "Source procedure text.",
    )

    assert "{paper_content}" not in rendered
    assert "Source:\nSource procedure text." in rendered
    assert "PIPELINE-INJECTED" not in rendered


def test_top_entity_prefix_defaults_to_active_tbox_class() -> None:
    assert _top_entity_line_prefixes(
        {},
        "https://www.theworldavatar.com/kg/OntoSyn/ChemicalSynthesis",
    ) == ["ChemicalSynthesis"]


def test_normalize_top_entity_output_accepts_structured_json() -> None:
    output = _normalize_top_entity_output(
        '[{"class":"ChemicalSynthesis","label":"UMC-1 synthesis"}]',
        line_prefixes=["ChemicalSynthesis"],
    )

    assert output == "ChemicalSynthesis-1 [UMC-1]\n"


def test_normalize_top_entity_output_accepts_iter1_ref_entity_json() -> None:
    output = _normalize_top_entity_output(
        json.dumps(
            {
                "entities": [
                    {
                        "ref": "synthesis-1",
                        "class": "ChemicalSynthesis",
                        "label": "route alpha",
                        "datatype_properties": {},
                    }
                ],
                "relations": [],
            }
        ),
        line_prefixes=["ChemicalSynthesis"],
    )

    assert output == "ChemicalSynthesis-1 [route alpha]\n"


def test_normalize_top_entity_output_accepts_canonical_class_sections() -> None:
    output = _normalize_top_entity_output(
        '{"ChemicalSynthesis":[{"entity_label":"route alpha","evidence":[]}]}',
        line_prefixes=["ChemicalSynthesis"],
    )

    assert output == "ChemicalSynthesis-1 [route alpha]\n"


def test_normalize_top_entity_output_accepts_fenced_json() -> None:
    output = _normalize_top_entity_output(
        '```json\n{"type":"ontosyn:ChemicalSynthesis","name":"UMC-1"}\n```',
        line_prefixes=["ChemicalSynthesis"],
    )

    assert output == "ChemicalSynthesis-1 [UMC-1]\n"


def test_top_entity_merge_enforces_unique_label_and_uri(tmp_path) -> None:
    (tmp_path / "top_entities.txt").write_text(
        "ChemicalSynthesis-1 [Preferred label]\n", encoding="utf-8"
    )
    top_class = "https://example.test/ChemicalSynthesis"
    shared_uri = _mint_top_entity_iri("Preferred label", top_class)

    merged = _merge_txt_top_entity_fallback(
        str(tmp_path),
        [
            {"uri": shared_uri, "label": "runtime label", "types": []},
            {"uri": "https://example.test/top/2", "label": "Preferred label", "types": []},
        ],
        top_class,
    )

    assert len(merged) == 1
    assert merged[0]["label"] == "Preferred label"


def test_top_entity_merge_rejects_blank_node_identifiers(tmp_path) -> None:
    merged = _merge_txt_top_entity_fallback(
        str(tmp_path),
        [
            {
                "uri": "n26ecf16c948145bc9bba819573fba278b1",
                "label": "n26ecf16c948145bc9bba819573fba278b1",
                "types": [],
            }
        ],
        "https://example.test/TopEntity",
    )

    assert merged == []


def test_extracted_scopes_exclude_agent_minted_top_entity_forks(tmp_path) -> None:
    top_class = "https://example.test/ChemicalSynthesis"
    (tmp_path / "top_entities.txt").write_text(
        "ChemicalSynthesis-1 [CS-1]\nChemicalSynthesis-2 [CS-2]\n",
        encoding="utf-8",
    )

    merged = _merge_txt_top_entity_fallback(
        str(tmp_path),
        [
            {"uri": "urn:uuid:one", "label": "CS-1 detailed route", "types": []},
            {"uri": "urn:uuid:two", "label": "CS-2 modified route", "types": []},
        ],
        top_class,
    )

    assert [(item["label"], item["uri"]) for item in merged] == [
        ("CS-1", _mint_top_entity_iri("CS-1", top_class)),
        ("CS-2", _mint_top_entity_iri("CS-2", top_class)),
    ]


def test_top_entity_materialization_merges_same_class_and_label() -> None:
    graph = Graph()
    top_class = URIRef("https://example.test/ChemicalSynthesis")
    runtime_node = URIRef("urn:uuid:runtime")
    canonical_node = URIRef("https://example.test/top/canonical")
    child = URIRef("https://example.test/child")
    predicate = URIRef("https://example.test/hasChild")
    label = "Primary synthesis"
    graph.add((runtime_node, RDF.type, top_class))
    graph.add((runtime_node, RDFS.label, Literal(label)))
    graph.add((runtime_node, predicate, child))

    changed = _materialize_supplemented_top_entities(
        graph,
        [{"uri": str(canonical_node), "label": label}],
        str(top_class),
    )

    assert changed
    assert (canonical_node, RDF.type, top_class) in graph
    assert (canonical_node, predicate, child) in graph
    assert not list(graph.triples((runtime_node, None, None)))


def test_top_entity_materialization_removes_noncanonical_root() -> None:
    graph = Graph()
    top_class = URIRef("https://example.test/ChemicalSynthesis")
    canonical = URIRef("https://example.test/top/canonical")
    rogue = URIRef("urn:uuid:rogue")
    graph.add((rogue, RDF.type, top_class))
    graph.add((rogue, RDFS.label, Literal("Expanded narrative label")))

    changed = _materialize_supplemented_top_entities(
        graph,
        [{"uri": str(canonical), "label": "CS-1"}],
        str(top_class),
    )

    assert changed
    assert set(graph.subjects(RDF.type, top_class)) == {canonical}


def test_iter1_reset_only_clears_shared_context_artifacts(tmp_path) -> None:
    memory = tmp_path / "memory"
    exports = tmp_path / "exports"
    memory.mkdir()
    exports.mkdir()
    shared = memory / "top.ttl"
    entity = memory / "entity-scope.ttl"
    export = exports / "top_20260101.ttl"
    shared.write_text("shared", encoding="utf-8")
    entity.write_text("entity", encoding="utf-8")
    export.write_text("shared export", encoding="utf-8")

    removed = _reset_iter1_shared_persistence(
        doi_folder=str(tmp_path),
        entity_context_names=["top"],
    )

    assert set(map(Path, removed)) == {shared, export}
    assert entity.is_file()


def test_top_entity_lock_preseeds_one_uri_per_extracted_scope(tmp_path) -> None:
    top_class = "https://example.test/ChemicalSynthesis"
    entities = [
        {
            "label": "CS-1",
            "uri": _mint_top_entity_iri("CS-1", top_class),
        },
        {
            "label": "CS-2",
            "uri": _mint_top_entity_iri("CS-2", top_class),
        },
    ]

    locked = _seed_iter1_top_entity_lock(
        doi_hash="paper",
        doi_folder=str(tmp_path),
        top_entities=entities,
        top_class_iri=top_class,
        entity_context_name="top",
        entity_context_aliases=["top"],
    )

    graph = Graph()
    graph.parse(tmp_path / "memory" / "top.ttl", format="turtle")
    assert set(graph.subjects(RDF.type, URIRef(top_class))) == {
        URIRef(entity["uri"]) for entity in entities
    }
    assert [entity["scope_index"] for entity in locked] == [1, 2]
    manifest = json.loads(
        (tmp_path / "mcp_run" / "top_entity_identity_lock.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["policy"] == "one_uri_per_extracted_scope"
    assert manifest["entities"] == locked


def test_pipeline_selected_top_class_resolves_without_tbox_top_role(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "src.pipelines.top_entity_extraction.extract.build_ontology_publish_contract",
        lambda **_: {
            "top_role": {"status": "unknown"},
            "classes": [
                {"class_iri": "https://example.test/schema/NeutralRoot"},
                {"class_iri": "https://example.test/schema/Other"},
            ],
        },
    )

    resolved = _resolve_selected_top_class_iri(
        str(tmp_path / "meta.json"),
        "neutral",
        "NeutralRoot",
    )

    assert resolved == "https://example.test/schema/NeutralRoot"


def test_top_class_selection_is_successful_extraction_postcondition(tmp_path) -> None:
    assert _persist_and_validate_top_class_selection(
        doi_dir=str(tmp_path),
        class_local="NeutralRoot",
        class_iri="https://example.test/schema/NeutralRoot",
    )

    persisted = (tmp_path / "top_entity_selection.json").read_text(encoding="utf-8")
    assert '"class_local": "NeutralRoot"' in persisted
    assert '"class_iri": "https://example.test/schema/NeutralRoot"' in persisted


def test_top_class_selection_rejects_incomplete_lineage(tmp_path) -> None:
    assert not _persist_and_validate_top_class_selection(
        doi_dir=str(tmp_path),
        class_local="NeutralRoot",
        class_iri="",
    )
    assert not (tmp_path / "top_entity_selection.json").exists()


def test_parse_top_entities_preserves_rdf_types_and_writes_sidecar(
    tmp_path, monkeypatch
) -> None:
    doi_hash = "paper"
    doi_folder = tmp_path / doi_hash
    doi_folder.mkdir()
    top_class = "https://example.test/schema/NeutralRoot"
    ancestor_class = "https://example.test/schema/NamedThing"
    entity_uri = "https://example.test/entity/root"
    (doi_folder / "top_entity_selection.json").write_text(
        json.dumps({"class_iri": top_class, "class_local": "NeutralRoot"}),
        encoding="utf-8",
    )
    (doi_folder / "iteration_1.ttl").write_text(
        f"""
        <{entity_uri}> a <{top_class}>, <{ancestor_class}> ;
            <http://www.w3.org/2000/01/rdf-schema#label> "Root A" .
        """,
        encoding="utf-8",
    )
    query = tmp_path / "top_entity_parsing.sparql"
    query.write_text(
        f"""
        SELECT ?entity ?label WHERE {{
          ?entity a <{top_class}> ;
                  <http://www.w3.org/2000/01/rdf-schema#label> ?label .
        }}
        """,
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "src.pipelines.top_entity_kg_building.build.resolve_generated_file",
        lambda _: str(query),
    )
    monkeypatch.setattr(
        "src.pipelines.top_entity_kg_building.build.load_meta_config",
        lambda _: {},
    )

    assert parse_top_entities_from_ttl(
        doi_hash,
        "neutral",
        data_dir=str(tmp_path),
        meta_task_config_path=str(tmp_path / "meta.json"),
    )

    manifest = json.loads(
        (doi_folder / "mcp_run" / "iter1_top_entities.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest == [
        {
            "uri": entity_uri,
            "label": "Root A",
            "types": sorted([top_class, ancestor_class]),
            "identity_dossier": {
                "schema_version": 1,
                "uri": entity_uri,
                "label": "Root A",
                "types": sorted([top_class, ancestor_class]),
                "scope_index": None,
                "source_anchor": "",
                "explicit_iteration_1_facts": [],
            },
        }
    ]
    scope = entity_scope_name("Root A", entity_uri)
    sidecar = json.loads(
        (doi_folder / "memory" / f"{scope}.identity.json").read_text(
            encoding="utf-8"
        )
    )
    assert sidecar["identity"] == {
        "uri": entity_uri,
        "label": "Root A",
        "types": sorted([top_class, ancestor_class]),
        "top_class_iri": top_class,
        "dossier": manifest[0]["identity_dossier"],
    }
    assert sidecar["checkpoint"]["last_completed_iteration"] == 1


def test_entity_scope_is_safe_and_uri_disambiguated() -> None:
    first = entity_scope_name("../same:name", "urn:entity:one")
    second = entity_scope_name("../same:name", "urn:entity:two")

    assert first.startswith("same_name--")
    assert second.startswith("same_name--")
    assert first != second
    assert "/" not in first and "\\" not in first and ":" not in first


def test_legacy_manifest_types_are_backfilled_only_after_ttl_validation(
    tmp_path,
) -> None:
    top_class = "https://example.test/schema/NeutralRoot"
    ancestor_class = "https://example.test/schema/NamedThing"
    entity_uri = "urn:entity:legacy"
    ttl_path = tmp_path / "iteration_1.ttl"
    ttl_path.write_text(
        f"<{entity_uri}> a <{top_class}>, <{ancestor_class}> .",
        encoding="utf-8",
    )

    hydrated = hydrate_and_validate_top_entity_types(
        entities=[{"uri": entity_uri, "label": "Legacy root"}],
        iteration_1_ttl=str(ttl_path),
        top_class_iri=top_class,
    )

    assert hydrated[0]["types"] == sorted([top_class, ancestor_class])


def test_legacy_manifest_backfill_fails_closed_for_wrong_selected_class(
    tmp_path,
) -> None:
    ttl_path = tmp_path / "iteration_1.ttl"
    ttl_path.write_text(
        "<urn:entity:legacy> a <https://example.test/schema/Other> .",
        encoding="utf-8",
    )

    try:
        hydrate_and_validate_top_entity_types(
            entities=[{"uri": "urn:entity:legacy", "label": "Legacy root"}],
            iteration_1_ttl=str(ttl_path),
            top_class_iri="https://example.test/schema/NeutralRoot",
        )
    except ValueError as exc:
        assert "is not typed as selected class" in str(exc)
    else:
        raise AssertionError("Expected fail-closed top-class validation")


def test_sidecars_do_not_collide_for_same_label_different_uri(tmp_path) -> None:
    top_class = "https://example.test/schema/NeutralRoot"
    paths = persist_entity_identity_sidecars(
        doi_hash="paper",
        doi_folder=str(tmp_path),
        entities=[
            {
                "uri": "urn:entity:one",
                "label": "Same root",
                "types": [top_class],
            },
            {
                "uri": "urn:entity:two",
                "label": "Same root",
                "types": [top_class],
            },
        ],
        top_class_iri=top_class,
    )

    assert len(paths) == 2
    assert paths[0] != paths[1]
    assert all((tmp_path / "memory" / Path(path).name).is_file() for path in paths)


def test_entity_scope_caps_long_labels_for_windows_paths() -> None:
    scope = entity_scope_name("very-long-" * 40, "urn:entity:long")

    assert len(scope) <= 46
    label_part, uri_hash = scope.rsplit("--", 1)
    assert len(label_part) <= 32
    assert len(uri_hash) == 12


def test_identity_atomic_staging_is_outside_watched_memory(
    tmp_path, monkeypatch
) -> None:
    captured_dirs = []
    captured_prefixes = []
    original_mkstemp = top_entity_identity.tempfile.mkstemp

    def recording_mkstemp(*args, **kwargs):
        captured_dirs.append(Path(kwargs["dir"]))
        captured_prefixes.append(kwargs["prefix"])
        return original_mkstemp(*args, **kwargs)

    monkeypatch.setattr(
        top_entity_identity.tempfile,
        "mkstemp",
        recording_mkstemp,
    )
    persist_entity_identity_sidecars(
        doi_hash="paper",
        doi_folder=str(tmp_path),
        entities=[
            {
                "uri": "urn:entity:one",
                "label": "Root",
                "types": ["https://example.test/schema/NeutralRoot"],
            }
        ],
        top_class_iri="https://example.test/schema/NeutralRoot",
    )

    assert captured_dirs == [tmp_path]
    assert captured_prefixes == [".identity."]
