from __future__ import annotations

import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GT_ROOT = ROOT / "full_ground_truth" / "steps"


def _gt(doi: str) -> dict:
    return json.loads((GT_ROOT / f"{doi}.json").read_text(encoding="utf-8"))


def _types(synthesis: dict) -> list[str]:
    return [next(iter(step)) for step in synthesis["steps"]]


def _counts(doi: str) -> Counter[str]:
    data = _gt(doi)
    return Counter(
        next(iter(step))
        for synthesis in data["Synthesis"]
        for step in synthesis["steps"]
    )


def test_tbox_step_adjudication_boundaries_are_explicit() -> None:
    tbox = (ROOT / "data/ontologies/ontosynthesis.ttl").read_text(encoding="utf-8")

    required = (
        "already-existing mixture or solution",
        "collection or isolation alone is insufficient",
        "ONE CHEMICALINPUT PER ADD STEP",
        "'A solution of X in Y' requires Add(X) and Add(Y)",
        "'A dissolved in X and B in Y were mixed/combined'",
        "emit neither Stir nor Transfer",
        "Slow evaporation that produces crystals remains Evaporate",
        "do not add passive HeatChill for the same interval",
        "drying or evacuation for guest/solvent removal is Dry",
        "characterization, gas sorption, or other post-synthesis sample preparation",
        "Crystallize, Transfer, Separate, Filter, Dry",
    )
    for text in required:
        assert text in tbox

    assert 'instanceIntegrityRule "prohibit_instance_creation"' not in tbox
    assert "Never use this step in any circumstances" not in tbox
    assert "create a single Add step whose substance is 'solution of X in Y'" not in tbox


def test_all_adjudicated_add_steps_have_one_material_and_contiguous_order() -> None:
    dois = (
        "10.1021_ic402428m",
        "10.1021_acs.chemmater.8b01667",
        "10.1021_acs.cgd.6b00306",
        "10.1021_ja042802q",
        "10.1021_acsami.8b02015",
        "10.1021_acs.inorgchem.8b01130",
        "10.1021_acsami.7b09339",
        "10.1021_ic802382p",
        "10.1039_C8DT02580K",
        "10.1039_C2CC34265K",
        "10.1021_ic050460z",
    )
    for doi in dois:
        for synthesis in _gt(doi)["Synthesis"]:
            assert [
                step[next(iter(step))]["stepNumber"] for step in synthesis["steps"]
            ] == list(range(1, len(synthesis["steps"]) + 1))
            for step in synthesis["steps"]:
                if "Add" in step:
                    assert len(step["Add"]["addedChemical"]) == 1


def test_adjudicated_step_type_counts() -> None:
    expected = {
        "10.1021_ic402428m": {"Add": 17, "HeatChill": 4, "Filter": 4},
        "10.1021_acs.chemmater.8b01667": {
            "Add": 9,
            "Stir": 2,
            "Transfer": 2,
            "Separate": 2,
            "Evaporate": 1,
        },
        "10.1021_acs.cgd.6b00306": {"Add": 5, "Evaporate": 1},
        "10.1021_ja042802q": {
            "Add": 32,
            "Stir": 6,
            "Transfer": 6,
            "HeatChill": 15,
            "Separate": 2,
            "Filter": 4,
        },
        "10.1021_acsami.8b02015": {
            "Add": 23,
            "HeatChill": 12,
            "Separate": 4,
        },
        "10.1021_acs.inorgchem.8b01130": {
            "Add": 32,
            "Stir": 1,
            "HeatChill": 11,
            "Separate": 2,
        },
        "10.1021_acsami.7b09339": {"Add": 16, "HeatChill": 4},
        "10.1021_ic802382p": {
            "Add": 5,
            "Transfer": 1,
            "HeatChill": 4,
            "Filter": 1,
            "Dry": 1,
        },
        "10.1039_C8DT02580K": {
            "Add": 8,
            "HeatChill": 4,
            "Filter": 4,
            "Dry": 2,
        },
        "10.1039_C2CC34265K": {
            "Add": 16,
            "HeatChill": 6,
            "Filter": 6,
        },
        "10.1021_ic050460z": {
            "Add": 13,
            "HeatChill": 5,
            "Filter": 1,
            "Evaporate": 1,
            "Dry": 1,
        },
    }
    for doi, counts in expected.items():
        assert _counts(doi) == Counter(counts)


def test_key_source_grounded_sequences_and_scopes() -> None:
    zrt = _gt("10.1021_ic402428m")["Synthesis"]
    assert all(_types(item).count("HeatChill") == 1 for item in zrt)
    successful_zrt3 = zrt[2]
    assert "1,3,5-triphenylbenzene" in str(successful_zrt3)
    assert "CCl4" not in str(successful_zrt3)

    routes = _gt("10.1021_acs.chemmater.8b01667")["Synthesis"]
    assert len(routes) == 3
    assert "mechanochemical" in routes[0]["productNames"][0]
    assert "solvothermal" in routes[1]["productNames"][0]
    assert all("steel ball" not in str(route).casefold() for route in routes)
    assert all("Cu(NH2-bdc)" not in str(route) for route in routes)

    irmops = _gt("10.1021_ja042802q")["Synthesis"]
    triclinic = next(item for item in irmops if "IRMOP-51_triclinic" in item["productNames"])
    assert _types(triclinic)[7:10] == ["HeatChill", "HeatChill", "HeatChill"]
    washing = triclinic["steps"][-1]["Filter"]["washingSolvent"]
    assert {row["chemicalName"][0] for row in washing} == {
        "N,N-dimethylformamide",
        "cyclohexane",
    }

    exchange = _gt("10.1021_acsami.8b02015")["Synthesis"]
    assert all(_types(item)[-4:] == ["Add", "HeatChill", "Add", "HeatChill"] for item in exchange)
    assert all("Filter" not in _types(item) for item in exchange)

    activation = _gt("10.1021_acs.inorgchem.8b01130")["Synthesis"]
    assert all("Dry" not in _types(item) and "Filter" not in _types(item) for item in activation)
    chromium = _gt("10.1021_acsami.7b09339")["Synthesis"]
    assert all("Separate" not in _types(item) for item in chromium)
    assert all(
        _types(item) == ["Add", "Add", "Add", "Add", "HeatChill"]
        for item in chromium
    )

    tube = _gt("10.1021_ic802382p")["Synthesis"][0]
    assert _types(tube)[:4] == ["Add", "Add", "Transfer", "HeatChill"]
    assert "solvent mixture of DMA and acetonitrile" not in str(tube)

    for doi in ("10.1039_C8DT02580K", "10.1039_C2CC34265K"):
        assert all(_types(item)[-2:] == ["Filter", "Dry"] or _types(item)[-2:] == ["Filter", "Filter"] for item in _gt(doi)["Synthesis"])

    old_mops = _gt("10.1021_ic050460z")["Synthesis"]
    assert "Filter" not in _types(old_mops[1])
    assert len(old_mops[2]["steps"]) == 6
    assert all(kind == "Add" for kind in _types(old_mops[2])[:5])
    assert "ether" in str(old_mops[0]["steps"][6]["Filter"]["washingSolvent"]).casefold()
