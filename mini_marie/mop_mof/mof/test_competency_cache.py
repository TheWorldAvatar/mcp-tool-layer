"""Unit tests for competency cache keys and local joins (no SPARQL)."""

from __future__ import annotations

import tempfile
from pathlib import Path

from mini_marie.cache_tiers import TIER_FULL, TIER_PROBE
from mini_marie.mop_mof.mof.competency_cache import CompetencyCache, cache_key, invoke_tool


def test_cache_key_stable() -> None:
    k1 = cache_key("get_mof_identity_by_name", {"mof_name": "ZIF-8"}, TIER_PROBE, 10)
    k2 = cache_key("get_mof_identity_by_name", {"mof_name": "ZIF-8"}, TIER_PROBE, 10)
    k3 = cache_key("get_mof_identity_by_name", {"mof_name": "ZIF-8"}, TIER_FULL)
    assert k1 == k2
    assert k1 != k3


def test_local_topology_join() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "test.sqlite"
        cache = CompetencyCache(db)
        cache.put(
            "get_mof_identity_by_name",
            {"mof_name": "ZIF-8"},
            [{"topology": "sod", "sourcedb": "Tobassco", "mof": "mof:a"}],
            tier=TIER_FULL,
            row_limit=None,
            elapsed_ms=1,
        )
        cache.put(
            "get_mofs_with_same_topology_as",
            {"reference_name": "ZIF-8"},
            [
                {"mof": "mof:b", "source": "ARC", "topology": "sod"},
                {"mof": "mof:c", "source": "Tobassco", "topology": "sod"},
            ],
            tier=TIER_FULL,
            row_limit=None,
            elapsed_ms=1,
        )
        assert cache.local_topology_count("sod") == 2
        sample = cache.local_topology_sample("sod", limit=1)
        assert len(sample) == 1
        cache.close()


def test_offline_requires_full_cache() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "test.sqlite"
        cache = CompetencyCache(db)
        try:
            invoke_tool(
                "get_mof_identity_by_name",
                {"mof_name": "Missing"},
                mode="offline",
                cache=cache,
            )
            raise AssertionError("expected CacheMissError")
        except Exception as exc:
            assert "CacheMiss" in type(exc).__name__ or "Missing full cache" in str(exc)
        finally:
            cache.close()


def test_invoke_tool_uses_cache() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "test.sqlite"
        cache = CompetencyCache(db)

        def fake_identity(mof_name: str, *, limit=None):  # noqa: ARG001
            return [{"name": mof_name, "topology": "pcu"}]

        from mini_marie.mop_mof.mof import competency_cache as cc

        orig = cc.TOOL_REGISTRY["get_mof_identity_by_name"]
        cc.TOOL_REGISTRY["get_mof_identity_by_name"] = fake_identity
        try:
            rows1, m1 = invoke_tool(
                "get_mof_identity_by_name",
                {"mof_name": "Test"},
                mode="online",
                online_limit=10,
                cache=cache,
            )
            assert len(rows1) == 1
            assert m1["from_cache"] is False

            rows2, m2 = invoke_tool(
                "get_mof_identity_by_name",
                {"mof_name": "Test"},
                mode="online",
                online_limit=10,
                cache=cache,
            )
            assert m2["from_cache"] is True
            assert rows2 == rows1
        finally:
            cc.TOOL_REGISTRY["get_mof_identity_by_name"] = orig
        cache.close()


def main() -> None:
    test_cache_key_stable()
    test_local_topology_join()
    test_offline_requires_full_cache()
    test_invoke_tool_uses_cache()
    print("ALL COMPETENCY CACHE TESTS PASSED")


if __name__ == "__main__":
    main()
