"""OntoSyn MCP: v2 compact OM-2 matcher on generated create_HeatChill."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from rdflib import RDF, URIRef

from src.kg_building_mcp_generation_v2.abox_mock.emit import emit_occurrence_package
from src.kg_building_mcp_generation_v2.abox_mock.runner import (
    load_package,
    unload_generated_package,
)

REPO = Path(__file__).resolve().parents[1]
PACK = REPO / "generated" / "runs" / "20260912_parentless_mcp"
PARSED = PACK / "ontology_structures" / "ontosynthesis" / "parsed.json"
CONTRACT = (
    PACK / "ontology_structures" / "ontosynthesis" / "generation_contract.json"
)
PROSE = "slowly cooled at about 4 degC / h"
RATE_LABEL = "4 degC/h"
DURATION_LABEL = "8 h"
TEMP_LABEL = "60 degC"


def _payload(raw: str) -> dict:
    return json.loads(raw)


@pytest.mark.skipif(not CONTRACT.is_file() or not PARSED.is_file(), reason="OntoSyn pack missing")
def test_ontosynthesis_heatchill_uses_compact_om2_matcher(tmp_path, monkeypatch) -> None:
    parsed = json.loads(PARSED.read_text(encoding="utf-8"))
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    scripts_dir = tmp_path / "ontosynthesis"
    emit_occurrence_package(
        ontology_name="ontosynthesis",
        parsed=parsed,
        contract=contract,
        scripts_dir=scripts_dir,
        output_root=tmp_path / "output",
    )
    runtime = (scripts_dir / "_fixed_om2_runtime.py").read_text(encoding="utf-8")
    assert "_COMPACT_RE" in runtime
    assert "extraction_prompt_generation.runtime_support.om2" not in runtime
    assert "def create_HeatChill(" in (
        scripts_dir / "ontosynthesis_occurrence_operations.py"
    ).read_text(encoding="utf-8")

    monkeypatch.setenv("TWA_AGENTIC_DATA_DIR", str(tmp_path / "data"))
    os.environ.pop("TWA_MCP_ENTITY_CONTEXT_EXPECTED_IRI", None)
    module = load_package(scripts_dir, "ontosynthesis")
    try:
        init = _payload(
            module.rdf_runtime.init_memory(
                "10.1000/om2-compact",
                "TestSynthesis",
                root_iri="https://example.test/om2-compact/root",
            )
        )
        assert str(init.get("status") or "").lower() == "ok"
        root = str(init.get("bound_root_iri") or "https://example.test/om2-compact/root")

        prose = _payload(
            module.operations.create_HeatChill(
                label="cool-prose",
                parent_iri=root,
                hasOrder=1,
                hasStepDuration=PROSE,
            )
        )
        assert str(prose.get("status") or "").lower() != "ok"
        assert "compact" in str(prose.get("message") or "").casefold() or str(
            prose.get("code") or ""
        ) in {"INVALID_OM2_QUANTITY", "QUANTITY_UNIT_CLASS_MISMATCH"}

        mismatch = _payload(
            module.operations.create_HeatChill(
                label="cool-mismatch",
                parent_iri=root,
                hasOrder=2,
                hasStepDuration=RATE_LABEL,
            )
        )
        assert str(mismatch.get("status") or "").lower() != "ok"
        blob = json.dumps(mismatch, ensure_ascii=False)
        assert "Duration" in blob
        assert "TemperatureRate" in blob or "not valid" in blob.casefold() or (
            str(mismatch.get("code") or "") == "QUANTITY_UNIT_CLASS_MISMATCH"
        )

        ok = _payload(
            module.operations.create_HeatChill(
                label="cool-compact",
                parent_iri=root,
                hasOrder=3,
                hasStepDuration=DURATION_LABEL,
                hasTargetTemperature=TEMP_LABEL,
                hasTemperatureRate=RATE_LABEL,
            )
        )
        assert str(ok.get("status") or "").lower() == "ok", ok
        graph = module.rdf_runtime.retained_graph()
        duration_iri = "http://www.ontology-of-units-of-measure.org/resource/om-2/Duration"
        rate_iri = (
            "http://www.ontology-of-units-of-measure.org/resource/om-2/TemperatureRate"
        )
        hour = "http://www.ontology-of-units-of-measure.org/resource/om-2/hour"
        degc_h = (
            "http://www.ontology-of-units-of-measure.org/resource/om-2/degreeCelsiusPerHour"
        )

        duration_nodes = [s for s, _, o in graph.triples((None, RDF.type, URIRef(duration_iri)))]
        rate_nodes = [s for s, _, o in graph.triples((None, RDF.type, URIRef(rate_iri)))]
        assert duration_nodes
        assert rate_nodes
        om2_has_unit = URIRef(
            "http://www.ontology-of-units-of-measure.org/resource/om-2/hasUnit"
        )
        duration_units = {str(o) for node in duration_nodes for o in graph.objects(node, om2_has_unit)}
        rate_units = {str(o) for node in rate_nodes for o in graph.objects(node, om2_has_unit)}
        assert hour in duration_units
        assert degc_h not in duration_units
        assert degc_h in rate_units
    finally:
        unload_generated_package("ontosynthesis", scripts_dir.parent)
