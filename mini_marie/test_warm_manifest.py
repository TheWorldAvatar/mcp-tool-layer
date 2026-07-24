"""Tests for workflow-driven warm manifest collection."""

from __future__ import annotations

from mini_marie.mop_mof.mof.atomic_warm_manifest import workflow_driven_warm_specs
from mini_marie.warm_manifest import spec_key


def test_cq03_count_only_variant_in_workflow_driven() -> None:
    specs = workflow_driven_warm_specs()
    keys = {spec_key(s) for s in specs}
    needed = spec_key(
        {
            "tool": "get_mofs_by_metal",
            "args": {
                "metal": "Cu",
                "count_only": True,
                "experimental_only": True,
            },
        }
    )
    assert needed in keys, "CQ03 arg variant must be in workflow-driven warm specs"


def test_cq02_list_sources_variant() -> None:
    specs = workflow_driven_warm_specs()
    keys = {spec_key(s) for s in specs}
    needed = spec_key(
        {"tool": "get_mofs_by_metal", "args": {"metal": "Zn", "list_sources": True}}
    )
    assert needed in keys


def main() -> None:
    test_cq03_count_only_variant_in_workflow_driven()
    test_cq02_list_sources_variant()
    print("ALL WARM MANIFEST TESTS PASSED")


if __name__ == "__main__":
    main()
