"""Unit tests for group_aggregate transform."""

from __future__ import annotations

from mini_marie.row_aggregates import group_aggregate_rows


def test_group_count_distinct() -> None:
    rows = [
        {"sourcedb": "CoRE MOF 2025", "mof": "a"},
        {"sourcedb": "CoRE MOF 2025", "mof": "b"},
        {"sourcedb": "CoRE MOF 2019", "mof": "c"},
    ]
    out = group_aggregate_rows(
        rows,
        {
            "group_by": ["sourcedb"],
            "aggregations": [{"op": "count_distinct", "field": "mof", "as": "count"}],
            "order_by": {"field": "count", "order": "desc"},
        },
        {},
    )
    assert len(out) == 2
    assert out[0]["sourcedb"] == "CoRE MOF 2025"
    assert out[0]["count"] == 2


def main() -> None:
    test_group_count_distinct()
    print("ALL ROW AGGREGATE TESTS PASSED")


if __name__ == "__main__":
    main()
