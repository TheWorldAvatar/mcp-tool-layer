from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ADAPTER = REPO_ROOT / "baselines" / "ontologx_ontosyn"
if str(ADAPTER) not in sys.path:
    sys.path.insert(0, str(ADAPTER))

from generate_shacl import generate, generate_layer  # noqa: E402
from iteration_guides import load_iteration_surfaces  # noqa: E402
from layered_shacl import shacl_path_for_layer  # noqa: E402


def test_iter2_surface_shacl_skips_step_obligations():
    text = generate_layer(2)
    assert "retrievedFrom" in text
    assert "hasChemicalOutput" in text
    assert "hasSynthesisStep" not in text
    assert "hasAddedChemicalInput" not in text
    assert "hasYield" not in text
    assert "CoolingNeedsTemperatureShape" not in text


def test_iter3_surface_shacl_owns_steps_not_yield():
    text = generate_layer(3)
    assert "hasSynthesisStep" in text
    assert "hasAddedChemicalInput" in text
    assert "CoolingNeedsTemperatureShape" in text
    assert "hasYield" not in text
    assert "hasEquipment" not in text
    assert "retrievedFrom" not in text


def test_iter4_surface_shacl_is_remainder_only():
    text = generate_layer(4)
    assert "hasYield" in text
    assert "hasEquipment" in text
    assert "NumericMeasureNeedsIriUnitShape" in text
    assert "hasAddedChemicalInput" not in text
    assert "hasSynthesisStep" not in text
    assert "retrievedFrom" not in text
    assert "CoolingNeedsTemperatureShape" not in text


def test_full_graph_shacl_still_has_all_obligations():
    text = generate()
    assert "hasSynthesisStep" in text
    assert "retrievedFrom" in text
    assert "hasYield" in text
    assert "CoolingNeedsTemperatureShape" in text


def test_layered_paths_are_ready_and_default_stays_full():
    assert shacl_path_for_layer(2, surface=False).name == "ontosynthesis_shacl.ttl"
    assert shacl_path_for_layer(2, surface=True).name == "ontosynthesis_shacl_iter2.ttl"
    assert shacl_path_for_layer(3, surface=True).name == "ontosynthesis_shacl_iter3.ttl"
    assert shacl_path_for_layer(4, surface=True).name == "ontosynthesis_shacl_iter4.ttl"


def test_surface_blueprint_matches_generated_owned_paths():
    surfaces = load_iteration_surfaces()
    iter2 = generate_layer(2, surfaces[2])
    iter3 = generate_layer(3, surfaces[3])
    iter4 = generate_layer(4, surfaces[4])
    for name in surfaces[2]["object_properties"]:
        if name == "isSuppliedBy":
            continue
        assert name in iter2
    assert "hasSynthesisStep" in iter3
    assert "hasYield" in iter4
