"""Unit tests for generic row_filters."""

from __future__ import annotations

from mini_marie.row_filters import filter_rows


def test_numeric_gte() -> None:
    rows = [
        {"building": "a", "height": "50"},
        {"building": "b", "height": "85"},
        {"building": "c", "height": "90"},
    ]
    out = filter_rows(
        rows,
        {"filters": [{"field": "height", "op": ">=", "value": 80}]},
        {},
    )
    assert len(out) == 2
    assert {r["building"] for r in out} == {"b", "c"}


def test_string_icontains() -> None:
    rows = [
        {"name": "UiO-66", "sourcedb": "ARC MOF"},
        {"name": "ZIF-8", "sourcedb": "CoRE MOF 2025"},
    ]
    out = filter_rows(
        rows,
        {"filter": {"field": "sourcedb", "op": "icontains", "value": "core"}},
        {},
    )
    assert len(out) == 1
    assert out[0]["name"] == "ZIF-8"


def test_logic_or() -> None:
    rows = [{"metal": "Zn"}, {"metal": "Cu"}, {"metal": "Fe"}]
    out = filter_rows(
        rows,
        {
            "logic": "or",
            "filters": [
                {"field": "metal", "op": "eq", "value": "Zn"},
                {"field": "metal", "op": "eq", "value": "Cu"},
            ],
        },
        {},
    )
    assert len(out) == 2


def test_variable_resolution() -> None:
    rows = [{"height": 70}, {"height": 88}]
    out = filter_rows(
        rows,
        {"filters": [{"field": "height", "op": "gt", "value": "$min_h"}]},
        {"min_h": 80},
        resolve=lambda v, vars: vars[v[1:]] if isinstance(v, str) and v.startswith("$") else v,
    )
    assert len(out) == 1
    assert out[0]["height"] == 88


def main() -> None:
    test_numeric_gte()
    test_string_icontains()
    test_logic_or()
    test_variable_resolution()
    print("ALL ROW FILTER TESTS PASSED")


if __name__ == "__main__":
    main()
