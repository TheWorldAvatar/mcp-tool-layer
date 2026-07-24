"""
Local SQLite cache + building facets for TWA city SPARQL workflow steps.

- probe tier: small LIMIT, remote SPARQL (online workflow validation)
- full tier: uncapped atomics, pre-cached via warm_city_cache.py
- offline: read full tier + local_join only (no remote SPARQL)
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from mini_marie.cache_tiers import (
    TIER_FULL,
    TIER_PROBE,
    CacheMissError,
    make_cache_key,
    row_limit_for_tier,
)
from mini_marie.zaha.twa_city.sparql_plans import (
    DEFAULT_ONLINE_LIMIT,
    SparqlPlan,
    build_plan,
)

DEFAULT_ONLINE_PROBE_LIMIT = DEFAULT_ONLINE_LIMIT

# Backward-compatible alias for tests importing cache_key
def cache_key(
    tool: str,
    args: Dict[str, Any],
    tier_or_legacy_mode: str,
    row_limit: Optional[int] = None,
) -> str:
    tier = tier_or_legacy_mode
    if tier in ("online", "offline", "warm"):
        tier = TIER_PROBE if tier == "online" else TIER_FULL
    probe_limit = int(row_limit) if row_limit is not None and tier == TIER_PROBE else DEFAULT_ONLINE_PROBE_LIMIT
    return make_cache_key(tool, _canonical_args(args), tier, probe_limit=probe_limit)


from mini_marie.cache_paths import mini_marie_cache_root


def cache_dir() -> Path:
    d = mini_marie_cache_root() / "twa_city"
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_path() -> Path:
    return cache_dir() / "city_cache.sqlite"


def _canonical_args(args: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key in sorted(args):
        val = args[key]
        if key == "building_iris" and isinstance(val, list):
            out[key] = sorted(str(v) for v in val)
        else:
            out[key] = val
    return out


def _norm(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _float_val(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


class CityCache:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path or db_path())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.OperationalError:
            pass  # another warm process may hold the DB
        self._init_schema()

    def close(self) -> None:
        self._conn.close()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS atomic_calls (
                cache_key TEXT PRIMARY KEY,
                tool TEXT NOT NULL,
                city TEXT NOT NULL,
                args_json TEXT NOT NULL,
                mode TEXT NOT NULL,
                row_limit INTEGER,
                endpoint TEXT,
                fetched_at REAL NOT NULL,
                elapsed_ms INTEGER,
                row_count INTEGER NOT NULL,
                status TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS atomic_rows (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cache_key TEXT NOT NULL,
                row_index INTEGER NOT NULL,
                data_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_atomic_rows_key ON atomic_rows(cache_key);
            CREATE INDEX IF NOT EXISTS idx_atomic_calls_city ON atomic_calls(city);

            CREATE TABLE IF NOT EXISTS facet_building_height (
                city_lc TEXT NOT NULL,
                building TEXT NOT NULL,
                height REAL,
                storeys TEXT,
                usage_type TEXT,
                label TEXT,
                cache_key TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_facet_height_city ON facet_building_height(city_lc);
            CREATE INDEX IF NOT EXISTS idx_facet_height_building ON facet_building_height(building);

            CREATE TABLE IF NOT EXISTS facet_building_location (
                city_lc TEXT NOT NULL,
                building TEXT NOT NULL,
                height REAL,
                wkt TEXT,
                usage_type TEXT,
                label TEXT,
                cache_key TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_facet_loc_city ON facet_building_location(city_lc);
            CREATE INDEX IF NOT EXISTS idx_facet_loc_building ON facet_building_location(building);
            """
        )
        self._conn.commit()

    def has_tier(self, tool: str, args: Dict[str, Any], tier: str) -> bool:
        key = make_cache_key(tool, _canonical_args(args), tier, probe_limit=DEFAULT_ONLINE_PROBE_LIMIT)
        cur = self._conn.execute(
            "SELECT 1 FROM atomic_calls WHERE cache_key = ? LIMIT 1", (key,)
        )
        return cur.fetchone() is not None

    def has_full(self, tool: str, args: Dict[str, Any]) -> bool:
        return self.has_tier(tool, args, TIER_FULL)

    def get(self, tool: str, args: Dict[str, Any], tier: str) -> Optional[List[Dict[str, Any]]]:
        key = make_cache_key(tool, _canonical_args(args), tier, probe_limit=DEFAULT_ONLINE_PROBE_LIMIT)
        if not self.has_tier(tool, args, tier):
            return None
        cur = self._conn.execute(
            "SELECT data_json FROM atomic_rows WHERE cache_key = ? ORDER BY row_index",
            (key,),
        )
        return [json.loads(r["data_json"]) for r in cur]

    def put(
        self,
        tool: str,
        args: Dict[str, Any],
        rows: List[Dict[str, Any]],
        *,
        city: str,
        tier: str,
        row_limit: Optional[int],
        endpoint: str,
        elapsed_ms: int,
        status: str = "pass",
    ) -> str:
        key = make_cache_key(tool, _canonical_args(args), tier, probe_limit=DEFAULT_ONLINE_PROBE_LIMIT)
        self._conn.execute("DELETE FROM atomic_rows WHERE cache_key = ?", (key,))
        self._conn.execute("DELETE FROM atomic_calls WHERE cache_key = ?", (key,))
        self._conn.execute(
            """
            INSERT INTO atomic_calls
            (cache_key, tool, city, args_json, mode, row_limit, endpoint, fetched_at, elapsed_ms, row_count, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                key,
                tool,
                city,
                json.dumps(_canonical_args(args)),
                tier,
                row_limit,
                endpoint,
                time.time(),
                elapsed_ms,
                len(rows),
                status,
            ),
        )
        for i, row in enumerate(rows):
            self._conn.execute(
                "INSERT INTO atomic_rows (cache_key, row_index, data_json) VALUES (?, ?, ?)",
                (key, i, json.dumps(row, ensure_ascii=False)),
            )
        self._index_rows(tool, args, rows, city, key)
        self._conn.commit()
        return key

    def _clear_facet(self, table: str, cache_key: str) -> None:
        self._conn.execute(f"DELETE FROM {table} WHERE cache_key = ?", (cache_key,))

    def _index_rows(
        self,
        tool: str,
        args: Dict[str, Any],
        rows: List[Dict[str, Any]],
        city: str,
        ck: str,
    ) -> None:
        city_lc = _norm(city).lower()
        if tool in (
            "list_buildings_with_height",
            "rank_buildings_by_height",
            "buildings_by_usage",
        ):
            self._clear_facet("facet_building_height", ck)
            for r in rows:
                self._conn.execute(
                    """
                    INSERT INTO facet_building_height
                    (city_lc, building, height, storeys, usage_type, label, cache_key)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        city_lc,
                        _norm(r.get("building")),
                        _float_val(r.get("height")),
                        _norm(r.get("storeys")),
                        _norm(r.get("usage_type")),
                        _norm(r.get("label")),
                        ck,
                    ),
                )
        elif tool == "fetch_building_locations":
            self._clear_facet("facet_building_location", ck)
            for r in rows:
                self._conn.execute(
                    """
                    INSERT INTO facet_building_location
                    (city_lc, building, height, wkt, usage_type, label, cache_key)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        city_lc,
                        _norm(r.get("building")),
                        _float_val(r.get("height")),
                        _norm(r.get("wkt")),
                        _norm(r.get("usage_type")),
                        _norm(r.get("label")),
                        ck,
                    ),
                )

    def local_top_n_by_height(
        self,
        city: str,
        n: int,
        *,
        usage_contains: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        city_lc = _norm(city).lower()
        if usage_contains:
            cur = self._conn.execute(
                """
                SELECT building, height, storeys, usage_type, label
                FROM facet_building_height
                WHERE city_lc = ? AND LCASE(usage_type) LIKE ?
                ORDER BY height DESC
                LIMIT ?
                """,
                (city_lc, f"%{_norm(usage_contains).lower()}%", int(n)),
            )
        else:
            cur = self._conn.execute(
                """
                SELECT building, height, storeys, usage_type, label
                FROM facet_building_height
                WHERE city_lc = ?
                ORDER BY height DESC
                LIMIT ?
                """,
                (city_lc, int(n)),
            )
        return [dict(r) for r in cur]

    def local_all_heights(self, city: str) -> List[Dict[str, Any]]:
        city_lc = _norm(city).lower()
        cur = self._conn.execute(
            """
            SELECT building, height, storeys, usage_type, label
            FROM facet_building_height WHERE city_lc = ?
            ORDER BY height DESC
            """,
            (city_lc,),
        )
        return [dict(r) for r in cur]

    def local_all_locations(self, city: str) -> List[Dict[str, Any]]:
        """Full location facet for a city (deprecated for workflow joins — use SQL join helpers)."""
        city_lc = _norm(city).lower()
        cur = self._conn.execute(
            """
            SELECT building, height, wkt, usage_type, label
            FROM facet_building_location
            WHERE city_lc = ?
            """,
            (city_lc,),
        )
        return [dict(r) for r in cur]

    _SQL_HEIGHT_LOCATION_JOIN = """
        SELECT
            h.building AS building,
            h.height AS height,
            h.storeys AS storeys,
            h.usage_type AS usage_type,
            h.label AS label,
            l.building AS r_building,
            l.height AS r_height,
            l.wkt AS r_wkt,
            l.usage_type AS r_usage_type,
            l.label AS r_label
        FROM facet_building_height h
        INNER JOIN facet_building_location l
            ON h.building = l.building AND h.city_lc = l.city_lc
        WHERE h.city_lc = ?
    """

    def local_buildings_with_locations_sql(
        self,
        city: str,
        building_iris: List[str],
    ) -> List[Dict[str, Any]]:
        """SQL join height + location facets for specific building IRIs (indexed lookup)."""
        iris_list = [_norm(i) for i in building_iris if _norm(i)]
        if not iris_list:
            return []
        city_lc = _norm(city).lower()
        placeholders = ",".join("?" * len(iris_list))
        cur = self._conn.execute(
            f"{self._SQL_HEIGHT_LOCATION_JOIN} AND h.building IN ({placeholders})",
            (city_lc, *iris_list),
        )
        return [dict(r) for r in cur]

    def local_top_n_with_locations_sql(
        self,
        city: str,
        n: int,
        *,
        usage_contains: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """SQL join top-N buildings by height with all cached WKT rows for those buildings."""
        city_lc = _norm(city).lower()
        n = max(1, int(n))
        if usage_contains:
            cur = self._conn.execute(
                f"""
                WITH top_buildings AS (
                    SELECT building
                    FROM facet_building_height
                    WHERE city_lc = ? AND LCASE(usage_type) LIKE ?
                    ORDER BY height DESC
                    LIMIT ?
                )
                {self._SQL_HEIGHT_LOCATION_JOIN}
                    AND h.building IN (SELECT building FROM top_buildings)
                ORDER BY h.height DESC, h.building, l.wkt
                """,
                (city_lc, f"%{_norm(usage_contains).lower()}%", n, city_lc),
            )
        else:
            cur = self._conn.execute(
                f"""
                WITH top_buildings AS (
                    SELECT building
                    FROM facet_building_height
                    WHERE city_lc = ?
                    ORDER BY height DESC
                    LIMIT ?
                )
                {self._SQL_HEIGHT_LOCATION_JOIN}
                    AND h.building IN (SELECT building FROM top_buildings)
                ORDER BY h.height DESC, h.building, l.wkt
                """,
                (city_lc, n, city_lc),
            )
        return [dict(r) for r in cur]

    def local_locations_for_buildings(
        self,
        city: str,
        building_iris: List[str],
    ) -> List[Dict[str, Any]]:
        rows, _ = self.resolve_locations_for_buildings(city, building_iris)
        return rows

    def resolve_locations_for_buildings(
        self,
        city: str,
        building_iris: List[str],
    ) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """
        Resolve WKT rows for building IRIs using partial cache layers.

        Order: location facet -> full-tier atomic batch (exact IRI list) -> per-IRI atomic batches.
        """
        iris_list = [_norm(i) for i in building_iris if _norm(i)]
        if not iris_list:
            return [], {"requested": 0, "resolved": 0, "missing_iris": []}

        city_lc = _norm(city).lower()
        placeholders = ",".join("?" * len(iris_list))
        cur = self._conn.execute(
            f"""
            SELECT building, height, wkt, usage_type, label
            FROM facet_building_location
            WHERE city_lc = ? AND building IN ({placeholders})
            """,
            (city_lc, *iris_list),
        )
        by_building: Dict[str, List[Dict[str, Any]]] = {}
        for r in cur:
            row = dict(r)
            b = _norm(row.get("building"))
            if b:
                by_building.setdefault(b, []).append(row)

        facet_hits = len(by_building)
        missing = [b for b in iris_list if b not in by_building]
        atomic_hits = 0

        def _merge_atomic_rows(rows: List[Dict[str, Any]]) -> None:
            nonlocal atomic_hits
            for row in rows:
                b = _norm(row.get("building"))
                if b and b not in by_building:
                    by_building[b] = [row]
                    atomic_hits += 1
                elif b and b in by_building:
                    by_building[b].append(row)

        if missing:
            batch_args = {"city": city, "building_iris": sorted(missing)}
            cached = self.get("fetch_building_locations", batch_args, TIER_FULL)
            if cached:
                _merge_atomic_rows(cached)
                missing = [b for b in iris_list if b not in by_building]

        still_missing = [b for b in missing if b not in by_building]
        for b in still_missing:
            cached = self.get(
                "fetch_building_locations",
                {"city": city, "building_iris": [b]},
                TIER_FULL,
            )
            if cached:
                _merge_atomic_rows(cached)

        merged: List[Dict[str, Any]] = []
        for b in iris_list:
            merged.extend(by_building.get(b, []))

        missing_final = [b for b in iris_list if b not in by_building]
        meta = {
            "requested": len(iris_list),
            "resolved_buildings": len(iris_list) - len(missing_final),
            "row_count": len(merged),
            "facet_buildings": facet_hits,
            "atomic_buildings": atomic_hits,
            "missing_iris": missing_final,
            "partial": bool(missing_final) and bool(merged),
        }
        return merged, meta

    def stats(self) -> Dict[str, Any]:
        tables = ["atomic_calls", "facet_building_height", "facet_building_location"]
        counts = {}
        for t in tables:
            cur = self._conn.execute(f"SELECT COUNT(*) AS c FROM {t}")
            counts[t] = int(cur.fetchone()["c"])
        cur = self._conn.execute("SELECT COUNT(*) AS c FROM atomic_rows")
        counts["atomic_rows"] = int(cur.fetchone()["c"])
        return {"db_path": str(self.path), "counts": counts}


def invoke_plan(
    tool: str,
    args: Dict[str, Any],
    *,
    mode: str = "online",
    online_limit: int = DEFAULT_ONLINE_PROBE_LIMIT,
    use_cache: bool = True,
    cache: Optional[CityCache] = None,
    allow_remote: bool = True,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any], SparqlPlan]:
    """
    online: probe tier, remote SPARQL with LIMIT.
    warm: full tier, remote SPARQL without LIMIT (pre-cache).
    offline: full tier, cache only (no remote).
    """
    city = str(args.get("city", ""))
    if mode == "offline":
        tier = TIER_FULL
        plan = build_plan(tool=tool, mode="warm", online_limit=int(online_limit), **args)
        allow_remote = False
    elif mode == "warm":
        tier = TIER_FULL
        plan = build_plan(tool=tool, mode="warm", online_limit=int(online_limit), **args)
    else:
        tier = TIER_PROBE
        plan = build_plan(tool=tool, mode="online", online_limit=int(online_limit), **args)

    row_limit = row_limit_for_tier(tier, probe_limit=int(online_limit))
    ck_store = cache or CityCache()
    own_cache = cache is None
    meta: Dict[str, Any] = {
        "tool": tool,
        "mode": mode,
        "cache_tier": tier,
        "row_limit": row_limit,
        "from_cache": False,
        "cache_key": None,
        "endpoint": plan.endpoint,
    }

    if use_cache:
        cached = ck_store.get(tool, args, tier)
        if cached is not None:
            meta["from_cache"] = True
            meta["cache_key"] = make_cache_key(
                tool, _canonical_args(args), tier, probe_limit=int(online_limit)
            )
            if own_cache:
                ck_store.close()
            return cached, meta, plan

    if not allow_remote or mode == "offline":
        if own_cache:
            ck_store.close()
        raise CacheMissError(
            f"Missing full cache for tool={tool!r} args={_canonical_args(args)!r}. "
            f"Run: python -m mini_marie.zaha.twa_city.warm_city_cache --city {city!r}"
        )

    started = time.perf_counter()
    rows = plan.execute()
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    key = ck_store.put(
        tool,
        args,
        rows,
        city=city,
        tier=tier,
        row_limit=row_limit,
        endpoint=plan.endpoint,
        elapsed_ms=elapsed_ms,
        status="pass" if rows else "empty",
    )
    meta["cache_key"] = key
    meta["elapsed_ms"] = elapsed_ms
    if own_cache:
        ck_store.close()
    return rows, meta, plan


def warm_atomic(
    tool: str,
    args: Dict[str, Any],
    *,
    online_limit: int = DEFAULT_ONLINE_PROBE_LIMIT,
    force: bool = False,
    cache: Optional[CityCache] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Pre-cache one atomic tool at full tier (remote SPARQL, no row cap)."""
    ck = cache or CityCache()
    own = cache is None
    if force:
        key = make_cache_key(tool, _canonical_args(args), TIER_FULL, probe_limit=int(online_limit))
        ck._conn.execute("DELETE FROM atomic_rows WHERE cache_key = ?", (key,))
        ck._conn.execute("DELETE FROM atomic_calls WHERE cache_key = ?", (key,))
        ck._conn.commit()
    rows, meta, _ = invoke_plan(
        tool,
        args,
        mode="warm",
        online_limit=online_limit,
        use_cache=not force,
        cache=ck,
        allow_remote=True,
    )
    if own:
        ck.close()
    return rows, meta


def _dedupe_buildings_by_height(pool: List[Dict[str, Any]]) -> List[str]:
    """Unique building IRIs, best height per IRI, descending by height."""
    seen: Dict[str, Dict[str, Any]] = {}
    for r in pool:
        b = _norm(r.get("building"))
        if not b:
            continue
        if b not in seen or _float_val(r.get("height")) >= _float_val(seen[b].get("height")):
            seen[b] = r
    ordered = sorted(seen.values(), key=lambda r: _float_val(r.get("height")), reverse=True)
    return [_norm(r.get("building")) for r in ordered if _norm(r.get("building"))]


def _warm_location_batches(
    city: str,
    buildings: List[str],
    *,
    batch_size: int,
    online_limit: int,
    force: bool,
    skip_cached: bool,
    ck: CityCache,
    label: str = "locations",
) -> Dict[str, Any]:
    batches = [buildings[i : i + batch_size] for i in range(0, len(buildings), batch_size)]
    total_rows = 0
    skipped = 0
    batch_times: List[int] = []
    started_all = time.perf_counter()
    for bi, batch in enumerate(batches, 1):
        if not batch:
            continue
        args = {"city": city, "building_iris": batch}
        if skip_cached and not force and ck.has_full("fetch_building_locations", args):
            skipped += 1
            if bi == 1 or bi % 10 == 0 or bi == len(batches):
                print(
                    f"  {label} batch {bi}/{len(batches)} skip (cached)",
                    flush=True,
                )
            continue

        t0 = time.perf_counter()
        print(
            f"  {label} batch {bi}/{len(batches)} ({len(batch)} buildings) ...",
            flush=True,
        )
        last_err: Exception | None = None
        rows: List[Dict[str, Any]] = []
        meta: Dict[str, Any] = {}
        for attempt in range(1, 6):
            try:
                rows, meta = warm_atomic(
                    "fetch_building_locations",
                    args,
                    online_limit=online_limit,
                    force=force,
                    cache=ck,
                )
                last_err = None
                break
            except (RuntimeError, OSError) as exc:
                last_err = exc
                if attempt >= 5:
                    raise
                wait_s = min(60, 5 * attempt)
                print(
                    f"    retry {attempt}/5 after {type(exc).__name__}: {exc} (sleep {wait_s}s)",
                    flush=True,
                )
                time.sleep(wait_s)
        if last_err is not None:
            raise last_err
        elapsed_ms = int(meta.get("elapsed_ms") or round((time.perf_counter() - t0) * 1000))
        batch_times.append(elapsed_ms)
        total_rows += len(rows)
        avg_ms = sum(batch_times) // len(batch_times)
        remaining = len(batches) - bi
        eta_min = round(remaining * avg_ms / 60000, 1)
        print(
            f"    -> {len(rows)} rows in {elapsed_ms}ms "
            f"(avg {avg_ms}ms/batch, ~{eta_min} min left)",
            flush=True,
        )

    return {
        "buildings": len(buildings),
        "batches": len(batches),
        "batches_skipped": skipped,
        "batches_fetched": len(batch_times),
        "location_rows": total_rows,
        "wall_ms": round((time.perf_counter() - started_all) * 1000),
    }


def warm_locations_top_n(
    city: str,
    top_n: int,
    *,
    usage_type: Optional[str] = None,
    batch_size: int = 100,
    online_limit: int = DEFAULT_ONLINE_PROBE_LIMIT,
    force: bool = False,
    skip_cached: bool = True,
    cache: Optional[CityCache] = None,
) -> Dict[str, Any]:
    """
    Warm WKT only for the tallest N buildings (from height facet).

    Enough for top10/top50 height-ranked workflows; ~1 batch instead of ~1000+.
    """
    ck = cache or CityCache()
    own = cache is None
    pool = ck.local_all_heights(city)
    if usage_type:
        needle = _norm(usage_type).lower()
        pool = [
            r
            for r in pool
            if needle in _norm(r.get("usage_type")).lower()
            or needle in _norm(r.get("label")).lower()
        ]
    all_buildings = _dedupe_buildings_by_height(pool)
    n = max(1, int(top_n))
    buildings = all_buildings[:n]
    usage_note = f" usage={usage_type!r}" if usage_type else ""
    print(
        f"  locations top-{n}{usage_note}: {len(buildings)} buildings "
        f"({len(all_buildings)} candidates in pool)",
        flush=True,
    )
    stats_body = _warm_location_batches(
        city,
        buildings,
        batch_size=min(batch_size, max(n, 1)),
        online_limit=online_limit,
        force=force,
        skip_cached=skip_cached,
        ck=ck,
        label=f"locations-top-{n}",
    )
    stats = ck.stats()
    if own:
        ck.close()
    return {
        "city": city,
        "strategy": f"top_{n}" + (f"_usage_{usage_type}" if usage_type else ""),
        "usage_type": usage_type,
        "cache_stats": stats,
        **stats_body,
    }


def warm_locations_for_city(
    city: str,
    *,
    batch_size: int = 100,
    online_limit: int = DEFAULT_ONLINE_PROBE_LIMIT,
    force: bool = False,
    skip_cached: bool = True,
    cache: Optional[CityCache] = None,
) -> Dict[str, Any]:
    """Batch-fetch WKT for all buildings in the height facet (full location facet)."""
    ck = cache or CityCache()
    own = cache is None
    buildings = _dedupe_buildings_by_height(ck.local_all_heights(city))
    print(
        f"  locations full-city: {len(buildings)} unique buildings "
        f"(batch_size={batch_size})",
        flush=True,
    )
    stats_body = _warm_location_batches(
        city,
        buildings,
        batch_size=batch_size,
        online_limit=online_limit,
        force=force,
        skip_cached=skip_cached,
        ck=ck,
        label="locations",
    )
    stats = ck.stats()
    if own:
        ck.close()
    return {
        "city": city,
        "strategy": "full_city",
        "cache_stats": stats,
        **stats_body,
    }
