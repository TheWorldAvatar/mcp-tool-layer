"""Unit tests for generic row_joins."""

from __future__ import annotations

from mini_marie.row_joins import join_rows


def test_inner_join_on_building() -> None:
    heights = [
        {"building": "http://ex/a", "height": "90"},
        {"building": "http://ex/b", "height": "70"},
    ]
    locations = [
        {"building": "http://ex/a", "wkt": "POLYGON(...)"},
        {"building": "http://ex/c", "wkt": "POLYGON(...)"},
    ]
    out = join_rows(
        heights,
        locations,
        {"left_key": "building", "right_key": "building", "how": "inner"},
        {},
    )
    assert len(out) == 1
    assert out[0]["height"] == "90"
    assert out[0]["r_wkt"] == "POLYGON(...)"


def test_left_join() -> None:
    left = [{"building": "http://ex/a", "height": "1"}]
    right = [{"building": "http://ex/b", "wkt": "X"}]
    out = join_rows(left, right, {"left_key": "building", "how": "left"}, {})
    assert len(out) == 1
    assert out[0]["building"] == "http://ex/a"
    assert "r_wkt" not in out[0]


def test_right_is_list_semi_join() -> None:
    synth = [
        {"refcode": "ABC", "solvent": "DMF"},
        {"refcode": "XYZ", "solvent": "water"},
    ]
    refcodes = ["ABC"]
    out = join_rows(
        synth,
        refcodes,
        {"left_key": "refcode", "right_is_list": True},
        {},
    )
    assert len(out) == 1
    assert out[0]["refcode"] == "ABC"


def test_post_join_filter() -> None:
    rows = [
        {"building": "a", "height": "50"},
        {"building": "b", "height": "90"},
    ]
    out = join_rows(
        rows,
        rows,
        {
            "left_key": "building",
            "right_key": "building",
            "how": "inner",
            "filters": [{"field": "height", "op": "gte", "value": 80}],
        },
        {},
    )
    assert len(out) == 1
    assert out[0]["building"] == "b"


def main() -> None:
    test_inner_join_on_building()
    test_left_join()
    test_right_is_list_semi_join()
    test_post_join_filter()
    print("ALL ROW JOIN TESTS PASSED")


if __name__ == "__main__":
    main()
