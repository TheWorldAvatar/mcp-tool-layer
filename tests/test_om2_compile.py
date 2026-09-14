"""OM-2 tables compile from T-Box + alias JSON; compact matcher is table-driven."""

from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path

import pytest
from rdflib import Graph, URIRef

from src.extraction_prompt_generation.pipeline.runtime_support import (
    write_fixed_om2_runtime,
)
from src.extraction_prompt_generation.runtime_support.om2 import (
    OM2,
    OM2_UNIT_MAP,
    _UNIT_QUANTITY_CLASSES,
    find_or_create_om2_quantity_from_label,
    parse_om2_quantity_label,
    resolve_om2_unit,
)
from src.kg_building_mcp_generation_v2.emit import scripts as v2_scripts
from src.kg_building_mcp_generation_v2.overlay.om2_runtime_emit import emit_om2_runtime
from src.kg_building_mcp_generation_v2.overlay.policy import (
    forbidden_hits,
    template_source_paths,
)
from src.kg_building_mcp_generation_v2.overlay.quantity_surface import (
    compile_om2_runtime_tables,
    compile_quantity_surface,
    load_source_unit_aliases,
)

REPO = Path(__file__).resolve().parents[1]
TBOX = REPO / "data" / "ontologies" / "om2.ttl"
ALIASES = REPO / "data" / "ontologies" / "om2_unit_aliases.json"
PROSE = "slowly cooled at about 4 degC / h"
COMPACT = "4 degC/h"
DURATION = OM2.Duration
RATE = OM2.TemperatureRate
HOUR_UNIT = OM2.degreeCelsiusPerHour


@pytest.mark.skipif(not TBOX.is_file(), reason="om2.ttl is missing")
def test_compiled_tables_match_alias_json_and_tbox() -> None:
    aliases = load_source_unit_aliases()
    unit_map, quantity_classes = compile_om2_runtime_tables()
    assert set(unit_map) == set(aliases)
    assert {str(iri).rsplit("/", 1)[-1] for iri in unit_map.values()} == set(
        aliases.values()
    )
    assert unit_map == OM2_UNIT_MAP
    tbox_classes = {
        URIRef("http://www.ontology-of-units-of-measure.org/resource/om-2/" + local): (
            URIRef(class_iri)
        )
        for local, class_iri in compile_quantity_surface({})["unit_local_to_class"].items()
    }
    assert set(quantity_classes) == set(tbox_classes)
    assert quantity_classes[HOUR_UNIT] == frozenset({RATE})
    assert quantity_classes == _UNIT_QUANTITY_CLASSES
    assert json.loads(ALIASES.read_text(encoding="utf-8"))["aliases"]["degc/h"] == (
        "degreeCelsiusPerHour"
    )


@pytest.mark.skipif(not TBOX.is_file(), reason="om2.ttl is missing")
def test_unknown_alias_target_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "aliases.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": "om2-source-unit-aliases.v1",
                    "aliases": {"nope": "notAUnitInTheTBox"},
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="notAUnitInTheTBox"):
            compile_om2_runtime_tables(alias_path=path)


@pytest.mark.skipif(not TBOX.is_file(), reason="om2.ttl is missing")
def test_compact_matcher_rejects_prose_and_class_mismatch() -> None:
    with pytest.raises(ValueError, match="compact"):
        parse_om2_quantity_label(PROSE, RATE)
    with pytest.raises(ValueError, match="not valid for Duration"):
        parse_om2_quantity_label(COMPACT, DURATION)
    with pytest.raises(ValueError, match="not valid for Duration"):
        resolve_om2_unit("degc/h", DURATION)
    value, unit = parse_om2_quantity_label(COMPACT, RATE)
    assert value == 4.0
    assert resolve_om2_unit(unit, RATE) == HOUR_UNIT
    graph = Graph()

    def mint(class_local: str, _label: str) -> URIRef:
        return URIRef(f"http://example.org/{class_local}")

    with pytest.raises(ValueError):
        find_or_create_om2_quantity_from_label(
            graph,
            quantity_class=DURATION,
            label=COMPACT,
            mint_iri=mint,
        )
    iri = find_or_create_om2_quantity_from_label(
        graph,
        quantity_class=RATE,
        label=COMPACT,
        mint_iri=mint,
    )
    from rdflib.namespace import RDF as RDFNS

    assert (iri, RDFNS.type, RATE) in graph
    assert (iri, OM2.hasUnit, HOUR_UNIT) in graph


@pytest.mark.skipif(not TBOX.is_file(), reason="om2.ttl is missing")
def test_flattened_runtime_is_compact_matcher_not_om2_py() -> None:
    surface = compile_quantity_surface({})
    text = emit_om2_runtime(surface)
    assert "from .om2_compile import" not in text
    assert "compile_om2_runtime_tables" not in text
    assert "extraction_prompt_generation.runtime_support.om2" not in text
    assert "_COMPACT_RE" in text
    assert "OM2.degreeCelsiusPerHour" in text
    assert "_UNIT_QUANTITY_CLASSES" in text
    with tempfile.TemporaryDirectory() as tmp:
        dest = write_fixed_om2_runtime(Path(tmp) / "_fixed_om2_runtime.py")
        spec = importlib.util.spec_from_file_location("flat_om2", dest)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with pytest.raises(ValueError, match="compact"):
            mod.parse_om2_quantity_label(PROSE, mod.OM2.TemperatureRate)
        with pytest.raises(ValueError, match="not valid for Duration"):
            mod.parse_om2_quantity_label(COMPACT, mod.OM2.Duration)
        with pytest.raises(ValueError):
            mod.find_or_create_om2_quantity_from_label(
                Graph(),
                quantity_class=mod.OM2.Duration,
                label=COMPACT,
                mint_iri=lambda cls, _lab: URIRef(f"http://example.org/{cls}"),
            )
        node = mod.find_or_create_om2_quantity_from_label(
            Graph(),
            quantity_class=mod.OM2.TemperatureRate,
            label=COMPACT,
            mint_iri=lambda cls, _lab: URIRef(f"http://example.org/{cls}"),
        )
        assert str(node).endswith("TemperatureRate")


def test_v2_scripts_emit_om2_runtime_like_occurrence_mcp() -> None:
    text = Path(v2_scripts.__file__).read_text(encoding="utf-8")
    assert "emit_om2_runtime" in text
    assert 'files["_fixed_om2_runtime.py"]' in text


def test_v2_om2_emit_templates_are_generic() -> None:
    for path in template_source_paths():
        hits = forbidden_hits(path.read_text(encoding="utf-8"))
        assert not hits, f"{path.name} contains domain tokens: {hits}"
