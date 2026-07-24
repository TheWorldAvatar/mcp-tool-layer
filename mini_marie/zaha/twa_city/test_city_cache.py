"""Unit tests for city SQLite cache (no SPARQL)."""

from __future__ import annotations

import tempfile
from pathlib import Path

from mini_marie.cache_tiers import TIER_FULL, TIER_PROBE
from mini_marie.zaha.twa_city.city_cache import CityCache, cache_key


def test_cache_key_stable() -> None:
    k1 = cache_key(
        "fetch_building_locations",
        {"city": "bremen", "building_iris": ["http://a/1", "http://b/2"]},
        TIER_PROBE,
        100,
    )
    k2 = cache_key(
        "fetch_building_locations",
        {"city": "bremen", "building_iris": ["http://b/2", "http://a/1"]},
        TIER_PROBE,
        100,
    )
    assert k1 == k2


def test_local_top_n_and_locations() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cache = CityCache(Path(tmp) / "test.sqlite")
        ck = cache.put(
            "rank_buildings_by_height",
            {"city": "bremen"},
            [
                {"building": "http://ex/1", "height": "50", "label": "A"},
                {"building": "http://ex/2", "height": "30", "label": "B"},
                {"building": "http://ex/3", "height": "40", "label": "C"},
            ],
            city="bremen",
            tier=TIER_FULL,
            row_limit=None,
            endpoint="http://example/sparql",
            elapsed_ms=1,
        )
        assert ck
        top = cache.local_top_n_by_height("bremen", 2)
        assert len(top) == 2
        assert top[0]["building"] == "http://ex/1"

        cache.put(
            "fetch_building_locations",
            {"city": "bremen", "building_iris": ["http://ex/1"]},
            [{"building": "http://ex/1", "height": "50", "wkt": "POLYGON(...)", "label": "A"}],
            city="bremen",
            tier=TIER_FULL,
            row_limit=None,
            endpoint="http://example/sparql",
            elapsed_ms=1,
        )
        locs = cache.local_locations_for_buildings("bremen", ["http://ex/1"])
        assert len(locs) == 1
        assert "POLYGON" in locs[0]["wkt"]
        cache.close()


def test_resolve_locations_facet_and_atomic() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cache = CityCache(Path(tmp) / "test.sqlite")
        cache.put(
            "fetch_building_locations",
            {"city": "kl", "building_iris": ["http://ex/a", "http://ex/b"]},
            [
                {"building": "http://ex/a", "height": "10", "wkt": "POLYGON((0 0))"},
                {"building": "http://ex/b", "height": "9", "wkt": "POLYGON((1 1))"},
            ],
            city="kl",
            tier=TIER_FULL,
            row_limit=None,
            endpoint="http://example/sparql",
            elapsed_ms=1,
        )
        rows, meta = cache.resolve_locations_for_buildings(
            "kl", ["http://ex/a", "http://ex/missing"]
        )
        assert len(rows) == 1
        assert meta["resolved_buildings"] == 1
        assert meta["partial"] is True
        assert "http://ex/missing" in meta["missing_iris"]
        cache.close()


def test_local_sql_buildings_with_locations() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cache = CityCache(Path(tmp) / "test.sqlite")
        cache.put(
            "list_buildings_with_height",
            {"city": "bremen"},
            [
                {"building": "http://ex/1", "height": "50", "storeys": "5", "label": "A"},
                {"building": "http://ex/2", "height": "30", "storeys": "3", "label": "B"},
            ],
            city="bremen",
            tier=TIER_FULL,
            row_limit=None,
            endpoint="http://example/sparql",
            elapsed_ms=1,
        )
        cache.put(
            "fetch_building_locations",
            {"city": "bremen", "building_iris": ["http://ex/1", "http://ex/2"]},
            [
                {"building": "http://ex/1", "height": "50", "wkt": "POLYGON((0 0))", "label": "A"},
                {"building": "http://ex/2", "height": "30", "wkt": "POLYGON((1 1))", "label": "B"},
            ],
            city="bremen",
            tier=TIER_FULL,
            row_limit=None,
            endpoint="http://example/sparql",
            elapsed_ms=1,
        )
        joined = cache.local_buildings_with_locations_sql("bremen", ["http://ex/1"])
        assert len(joined) == 1
        assert joined[0]["building"] == "http://ex/1"
        assert joined[0]["r_wkt"] == "POLYGON((0 0))"

        top_joined = cache.local_top_n_with_locations_sql("bremen", 1)
        assert len(top_joined) == 1
        assert top_joined[0]["building"] == "http://ex/1"
        cache.close()


def main() -> None:
    test_cache_key_stable()
    test_local_top_n_and_locations()
    test_local_sql_buildings_with_locations()
    test_resolve_locations_facet_and_atomic()
    print("ALL CITY CACHE TESTS PASSED")


if __name__ == "__main__":
    main()
