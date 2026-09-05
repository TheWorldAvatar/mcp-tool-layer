from __future__ import annotations

import json
from pathlib import Path

from src.agents.mops.cbu_derivation.utils import metal_cbu


def test_load_top_level_entities_uses_ontomops_mapping(tmp_path: Path, monkeypatch) -> None:
    paper = tmp_path / "1b9180ec"
    out = paper / "ontomops_output"
    out.mkdir(parents=True)
    (out / "ontomops_extension_TMA-VMOT-2.ttl").write_text(
        "@prefix : <http://example.org/> .\n:x a :Dummy .\n",
        encoding="utf-8",
    )
    (out / "ontomops_extension_TMA-VMOT-3.ttl").write_text(
        "@prefix : <http://example.org/> .\n:y a :Dummy .\n",
        encoding="utf-8",
    )
    (out / "top.ttl").write_text("@prefix : <http://example.org/> .\n:z a :Dummy .\n", encoding="utf-8")
    (out / "ontomops_output_mapping.json").write_text(
        json.dumps(
            {
                "Synthesis of TMA-VMOT-2": "ontomops_extension_TMA-VMOT-2.ttl",
                "Synthesis of TMA-VMOT-3": "ontomops_extension_TMA-VMOT-3.ttl",
                "https://example.org/iri": "ontomops_extension_TMA-VMOT-2.ttl",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(metal_cbu, "DATA_DIR", str(tmp_path))

    entities = metal_cbu.load_top_level_entities("1b9180ec")
    assert [row["label"] for row in entities] == [
        "Synthesis of TMA-VMOT-2",
        "Synthesis of TMA-VMOT-3",
    ]


def test_load_top_level_entities_falls_back_to_iter1(tmp_path: Path, monkeypatch) -> None:
    paper = tmp_path / "deadbeef"
    mcp = paper / "mcp_run"
    mcp.mkdir(parents=True)
    (mcp / "iter1_top_entities.json").write_text(
        json.dumps([{"label": "Fine-only entity", "uri": "https://example.org/a"}]),
        encoding="utf-8",
    )
    monkeypatch.setattr(metal_cbu, "DATA_DIR", str(tmp_path))

    entities = metal_cbu.load_top_level_entities("deadbeef")
    assert entities == [{"label": "Fine-only entity", "uri": "https://example.org/a"}]


def test_resolve_ccdc_prefers_ttl_then_name_map() -> None:
    ttl = (
        '@prefix ns1: <https://www.theworldavatar.com/kg/ontomops/> .\n'
        '<http://example.org/m> ns1:hasCCDCNumber "1815082" .\n'
    )
    assert metal_cbu.resolve_ccdc_for_derivation("Cu_OBu-bdc cage", ttl) == "1815082"
    assert metal_cbu.resolve_ccdc_for_derivation("Cu_OBu-bdc cage", "") == "1815077"
    assert metal_cbu.resolve_ccdc_for_derivation(
        "mechanochemical synthesis of Cu24(H-bdc)24 cage",
        "",
    ) == ""


def test_entity_integration_usable_allows_one_empty_side() -> None:
    from src.pipelines.mop_derivation.derive import entity_integration_usable

    assert entity_integration_usable(
        {"metal_cbu": {"iri": ""}, "organic_cbu": {"iri": "https://example.org/o"}}
    )
    assert entity_integration_usable(
        {"metal_cbu": {"iri": "https://example.org/m"}, "organic_cbu": {"iri": ""}}
    )
    assert not entity_integration_usable({"metal_cbu": {"iri": ""}, "organic_cbu": {"iri": ""}})
