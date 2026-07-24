"""Integration test: filter_rows + join_rows transform pipeline (in-memory)."""

from __future__ import annotations

from mini_marie.mop_mof.mof.competency_engine import extract_from_step
from mini_marie.row_filters import filter_rows
from mini_marie.row_joins import join_rows
from mini_marie.workflow_steps import apply_step_extract


def test_height_filter_then_location_join() -> None:
    pool = [
        {"building": "http://ex/a", "height": "95"},
        {"building": "http://ex/b", "height": "60"},
        {"building": "http://ex/c", "height": "88"},
    ]
    tall = filter_rows(
        pool,
        {"filters": [{"field": "height", "op": "gte", "value": 80}]},
        {},
    )
    assert len(tall) == 2

    locations = [
        {"building": "http://ex/a", "wkt": "POLYGON((1 1))"},
        {"building": "http://ex/c", "wkt": "POLYGON((2 2))"},
        {"building": "http://ex/d", "wkt": "POLYGON((3 3))"},
    ]
    joined = join_rows(
        tall,
        locations,
        {"left_key": "building", "right_key": "building", "how": "inner"},
        {},
    )
    assert len(joined) == 2
    assert all("r_wkt" in r for r in joined)


def test_transform_extract_hook() -> None:
    variables: dict = {}
    result = {
        "rows": [{"refcode": "ABC", "x": 1}, {"refcode": "DEF", "x": 2}],
        "row_count": 2,
    }
    step = {"extract": {"filtered_synthesis_rows": {"pick": "all_rows"}}}
    apply_step_extract(step, result, variables, extract_from_step)
    assert len(variables["filtered_synthesis_rows"]) == 2


def test_extract_skips_empty_skipped() -> None:
    variables: dict = {"keep": 1}
    apply_step_extract(
        {"extract": {"x": {"pick": "row_field", "field": "count"}}},
        {"status": "skipped", "rows": []},
        variables,
        extract_from_step,
    )
    assert "x" not in variables


def main() -> None:
    test_height_filter_then_location_join()
    test_transform_extract_hook()
    test_extract_skips_empty_skipped()
    print("ALL TRANSFORM PIPELINE TESTS PASSED")


if __name__ == "__main__":
    main()
