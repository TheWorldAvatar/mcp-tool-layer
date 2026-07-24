"""Unit tests for probed_sequence build/replay helpers."""

from __future__ import annotations

from mini_marie.probe_sequence import build_probed_sequence, steps_from_probed_sequence


def test_build_and_roundtrip() -> None:
    workflow = {
        "steps": [
            {"tool": "list_buildings_with_height", "args": {"city": "$city"}},
            {
                "type": "transform",
                "transform": "top_n_by_field",
                "input_variable": "building_pool",
                "field": "height",
                "n": "$top_n",
                "output_variable": "top_building_iris",
            },
        ]
    }
    trace = [
        {
            "step": 1,
            "step_type": "sparql_plan",
            "tool": "list_buildings_with_height",
            "input": {"city": "bremen"},
            "status": "pass",
        },
        {
            "step": 2,
            "step_type": "transform",
            "name": "top_n_by_field",
            "status": "pass",
        },
    ]
    seq = build_probed_sequence(workflow, trace)
    assert len(seq) == 2
    assert seq[0]["args"] == {"city": "bremen"}
    steps = steps_from_probed_sequence(seq)
    assert steps[0]["tool"] == "list_buildings_with_height"
    assert steps[1]["type"] == "transform"


def main() -> None:
    test_build_and_roundtrip()
    print("ALL PROBE SEQUENCE TESTS PASSED")


if __name__ == "__main__":
    main()
