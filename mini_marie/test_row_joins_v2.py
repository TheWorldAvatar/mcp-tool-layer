"""Tests for join_rows v2: anti-join and composite keys."""

from __future__ import annotations

from mini_marie.row_joins import join_rows


def test_anti_join() -> None:
    left = [{"mof": "a"}, {"mof": "b"}]
    right = [{"mof": "a", "x": 1}]
    out = join_rows(left, right, {"left_key": "mof", "right_key": "mof", "how": "anti"}, {})
    assert len(out) == 1
    assert out[0]["mof"] == "b"


def test_composite_keys() -> None:
    left = [{"mof": "a", "src": "X"}]
    right = [{"mof": "a", "src": "X", "wkt": "P"}]
    out = join_rows(
        left,
        right,
        {"keys": [{"left": "mof", "right": "mof"}, {"left": "src", "right": "src"}]},
        {},
    )
    assert len(out) == 1
    assert out[0]["r_wkt"] == "P"


def main() -> None:
    test_anti_join()
    test_composite_keys()
    print("ALL JOIN V2 TESTS PASSED")


if __name__ == "__main__":
    main()
