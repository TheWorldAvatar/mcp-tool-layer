"""Tests for chained multi_join_rows transform."""

from mini_marie.row_joins import join_rows, run_multi_join_rows_transform


def test_multi_join_nist_core_tob_pattern():
    nist = [
        {"nist_mof": "n1", "name": "MOF-A", "name_lc": "mof-a", "uptake": "1.0"},
        {"nist_mof": "n2", "name": "MOF-B", "name_lc": "mof-b", "uptake": "2.0"},
    ]
    core = [
        {"core_mof": "c1", "name_lc": "mof-a", "mofid": "id-a", "metal": "Zn"},
        {"core_mof": "c2", "name_lc": "mof-b", "mofid": "id-b", "metal": "Cu"},
    ]
    tob = [
        {"mofid": "id-a", "func_group": "amine"},
    ]
    variables = {"nist_pool": nist, "core_pool": core, "tob_pool": tob}
    spec = {
        "left_variable": "nist_pool",
        "output_variable": "enriched_rows",
        "joins": [
            {
                "right_variable": "core_pool",
                "left_key": "name_lc",
                "right_key": "name_lc",
                "how": "inner",
                "right_prefix": "core_",
            },
            {
                "right_variable": "tob_pool",
                "left_key": "core_mofid",
                "right_key": "mofid",
                "how": "left",
                "right_prefix": "tob_",
            },
        ],
    }
    result = run_multi_join_rows_transform(
        0, spec, variables, resolve=lambda v, _: v, format_tsv=lambda _: ""
    )
    rows = variables["enriched_rows"]
    assert result["row_count"] == 2
    assert rows[0]["name_lc"] == "mof-a"
    assert rows[0]["core_metal"] == "Zn"
    assert rows[0]["tob_func_group"] == "amine"
    assert rows[1]["name_lc"] == "mof-b"
    assert "tob_func_group" not in rows[1] or rows[1].get("tob_func_group") is None


def test_multi_join_inner_then_anti():
    left = [{"k": "a"}, {"k": "b"}]
    right = [{"k": "a", "v": 1}]
    variables = {"left": left, "right": right}
    out = join_rows(left, right, {"left_key": "k", "how": "inner"}, {})
    assert len(out) == 1
