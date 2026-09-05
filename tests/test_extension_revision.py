from __future__ import annotations

import json
from pathlib import Path

import pytest
from rdflib import Graph, URIRef
from rdflib.namespace import RDF

from src.pipelines.utils.extension_revision import (
    collect_extension_structural_messages,
    collect_hint_violations,
    hint_revision_prompt_block,
    missing_bound_target_messages,
    missing_hinted_links_on_bound_subjects,
    revision_attempt_limits,
)


SPECIES = "https://example.test/output/1"
HNMR = "https://example.test/hnmr/1"
SPECIES_CLASS = (
    "http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#Species"
)
HNMR_CLASS = (
    "http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#HNMRData"
)
HAS_HNMR = (
    "http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#hasHNMRData"
)
SYNTHESIS_CLASS = "https://www.theworldavatar.com/kg/OntoSyn/ChemicalSynthesis"


def _contract(tmp_path: Path) -> dict:
    tbox = tmp_path / "tbox.ttl"
    tbox.write_text("@prefix ex: <https://example.test/> .\nex:Dummy a ex:Dummy .\n")
    return {
        "resolved_ttl_file": str(tbox),
        "classes": [
            {"class_iri": SPECIES_CLASS},
            {"class_iri": HNMR_CLASS},
            {"class_iri": SYNTHESIS_CLASS},
        ],
        "object_properties": [
            {
                "property_iri": HAS_HNMR,
                "domain_iris": [SPECIES_CLASS],
                "range_iris": [HNMR_CLASS],
            }
        ],
        "subclass_closure": [],
        "required_links": [],
    }


def _hints(*, subject_class: str = "Species") -> str:
    return json.dumps(
        {
            "entities": [
                {"ref": "E1", "class": subject_class, "label": "product"},
                {"ref": "E2", "class": "HNMRData", "label": "nmr"},
            ],
            "relations": [
                {
                    "subject_ref": "E1",
                    "property": "hasHNMRData",
                    "object_ref": "E2",
                }
            ],
        }
    )


def test_hint_revision_prompt_block_is_authoritative_replacement() -> None:
    block = hint_revision_prompt_block(
        json.dumps({"schema_version": "kg-hint-contract-revision.v1", "violations": []})
    )
    assert "PIPELINE KG CONTRACT CORRECTION" in block
    assert "full-authoritative-replacement" in block


def test_collect_hint_violations_flags_domain_mismatch() -> None:
    violations = collect_hint_violations(
        _hints(subject_class="ChemicalSynthesis"),
        {
            "object_properties": [
                {
                    "property_iri": HAS_HNMR,
                    "domain_iris": [SPECIES_CLASS],
                    "range_iris": [HNMR_CLASS],
                }
            ]
        },
    )
    assert violations
    assert violations[0]["code"] == "HINT_RELATION_DOMAIN_MISMATCH"


def test_collect_hint_violations_accepts_matching_domain() -> None:
    assert collect_hint_violations(
        _hints(),
        {
            "object_properties": [
                {
                    "property_iri": HAS_HNMR,
                    "domain_iris": [SPECIES_CLASS],
                    "range_iris": [HNMR_CLASS],
                }
            ]
        },
    ) == []


def test_missing_bound_target_and_hinted_link(tmp_path: Path) -> None:
    ttl = tmp_path / "ext.ttl"
    ttl.write_text(
        f"<{HNMR}> a <{HNMR_CLASS}> .\n",
        encoding="utf-8",
    )
    graph = Graph().parse(str(ttl), format="turtle")
    targets = [{"target_iri": SPECIES, "class_iri": SPECIES_CLASS}]
    contract = _contract(tmp_path)

    assert missing_bound_target_messages(graph, targets)
    assert not missing_hinted_links_on_bound_subjects(
        hints_content=_hints(),
        graph=graph,
        targets=targets,
        ontology_contract=contract,
    )

    graph.add((URIRef(SPECIES), RDF.type, URIRef(SPECIES_CLASS)))
    assert not missing_bound_target_messages(graph, targets)
    assert missing_hinted_links_on_bound_subjects(
        hints_content=_hints(),
        graph=graph,
        targets=targets,
        ontology_contract=contract,
    )

    graph.add((URIRef(SPECIES), URIRef(HAS_HNMR), URIRef(HNMR)))
    graph.serialize(destination=ttl, format="turtle")
    messages = collect_extension_structural_messages(
        ttl_path=str(ttl),
        entity_uri="https://example.test/synthesis/1",
        entity_label="product",
        ontology_contract=contract,
        enrichment_targets=targets,
        hints_content=_hints(),
    )
    assert messages == []


def test_revision_attempt_limits_use_pipeline_config() -> None:
    assert revision_attempt_limits(
        {"kg_hint_revision_max_attempts": 4, "post_publish_structural_retries": 1}
    ) == (4, 1)


def test_disable_kg_revisions_zeros_extension_limits() -> None:
    assert revision_attempt_limits(
        {
            "disable_kg_revisions": True,
            "kg_hint_revision_max_attempts": 4,
            "post_publish_structural_retries": 3,
        }
    ) == (0, 0)


@pytest.mark.asyncio
async def test_run_extraction_revision_overwrites_and_appends_correction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.pipelines.extensions_extractions import extract as ext

    captured: dict[str, str] = {}

    async def fake_extract_content(**kwargs):
        captured["goal"] = kwargs["goal"]
        captured["previous"] = kwargs.get("previous_extraction") or ""
        return json.dumps(
            {
                "entities": [
                    {"ref": "E1", "class": "Species", "label": "fixed"}
                ],
                "relations": [],
            }
        )

    monkeypatch.setattr(ext, "extract_content", fake_extract_content)
    out = tmp_path / "extraction.txt"
    out.write_text(_hints(subject_class="ChemicalSynthesis"), encoding="utf-8")

    result = await ext.run_extraction(
        doi_hash="case",
        entity_label="product",
        entity_uri="https://example.test/synthesis/1",
        paper_content="paper",
        tbox_content="",
        extraction_prompt_template="Extract facts.",
        model_name="gpt-4o",
        output_file=str(out),
        prompt_file=str(tmp_path / "prompt.md"),
        data_dir=str(tmp_path),
        revision_feedback=json.dumps(
            {
                "schema_version": "kg-hint-contract-revision.v1",
                "violations": [
                    {
                        "subject_ref": "E1",
                        "property": "hasHNMRData",
                        "object_ref": "E2",
                    }
                ],
            }
        ),
        force=True,
    )

    assert "PIPELINE KG CONTRACT CORRECTION" in captured["goal"]
    assert "ChemicalSynthesis" in captured["previous"]
    payload = json.loads(result)
    assert payload["entities"][0]["class"] == "Species"
    assert json.loads(out.read_text(encoding="utf-8"))["entities"][0]["class"] == "Species"
