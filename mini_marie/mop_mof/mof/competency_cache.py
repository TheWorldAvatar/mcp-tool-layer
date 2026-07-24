"""
Local SQLite cache + facet indexes for MOF competency atomic tools.

- probe tier: online validation (small LIMIT, remote SPARQL)
- full tier: pre-cached atomics (no LIMIT); offline reads cache + local_join only
"""

from __future__ import annotations

import inspect
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from mini_marie.cache_tiers import (
    TIER_FULL,
    TIER_PROBE,
    CacheMissError,
    make_cache_key,
    row_limit_for_tier,
)
from mini_marie.mop_mof.mof import mof_competency_operations as co
from mini_marie.mop_mof.mof.mof_operations import DEFAULT_SPARQL_ENDPOINT, execute_sparql
from mini_marie.sparql_utils import RESIDUAL_TOOL, apply_sparql_limit, normalize_sparql

DEFAULT_ONLINE_LIMIT = co.DEFAULT_ONLINE_PROBE_LIMIT
DEFAULT_OFFLINE_CAP = co.DEFAULT_OFFLINE_CAP  # deprecated; offline uses full tier only

TOOL_REGISTRY: Dict[str, Callable[..., List[Dict[str, Any]]]] = {
    "get_pld_stats_by_mof_name": co.get_pld_stats_by_mof_name,
    "get_mofs_by_metal": co.get_mofs_by_metal,
    "get_synthesis_by_mof_name": co.get_synthesis_by_mof_name,
    "get_mof_identity_by_name": co.get_mof_identity_by_name,
    "get_linkers_by_mof_name": co.get_linkers_by_mof_name,
    "get_publications_by_mof_name": co.get_publications_by_mof_name,
    "get_mofs_with_same_topology_as": co.get_mofs_with_same_topology_as,
    "count_hypothetical_same_topology_as": co.count_hypothetical_same_topology_as,
    "count_experimental_by_topology": co.count_experimental_by_topology,
    "get_pore_metrics_by_mof_name": co.get_pore_metrics_by_mof_name,
    "get_asa_by_mof_name": co.get_asa_by_mof_name,
    "get_mofs_by_gsa_min": co.get_mofs_by_gsa_min,
    "get_mofs_by_lcd_max": co.get_mofs_by_lcd_max,
    "get_top_synthesis_solvents": co.get_top_synthesis_solvents,
    "get_mofs_by_metal_and_linker": co.get_mofs_by_metal_and_linker,
    "get_mofs_by_topology_all_sources": co.get_mofs_by_topology_all_sources,
    "get_refcodes_by_mof_name": co.get_refcodes_by_mof_name,
    "get_water_stable_mofs": co.get_water_stable_mofs,
    "get_thermal_stable_mofs": co.get_thermal_stable_mofs,
    "get_aqueous_low_temp_syntheses": co.get_aqueous_low_temp_syntheses,
    "get_high_binary_gas_uptake_mofs": co.get_high_binary_gas_uptake_mofs,
    "get_nist_exp_adsorption_rows": co.get_nist_exp_adsorption_rows,
    "get_core_name_chemistry_rows": co.get_core_name_chemistry_rows,
    "get_tobassco_func_groups_by_mofid": co.get_tobassco_func_groups_by_mofid,
}


from mini_marie.cache_paths import mini_marie_cache_root


def cache_dir() -> Path:
    d = mini_marie_cache_root() / "mof_competency"
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_path() -> Path:
    return cache_dir() / "competency_cache.sqlite"


def _canonical_args(args: Dict[str, Any]) -> Dict[str, Any]:
    return {k: args[k] for k in sorted(args)}


def cache_key(tool: str, args: Dict[str, Any], tier_or_legacy: str, row_limit: Optional[int] = None) -> str:
    tier = tier_or_legacy
    if tier in ("online", "offline", "warm"):
        tier = TIER_PROBE if tier == "online" else TIER_FULL
    probe_limit = (
        int(row_limit) if row_limit is not None and tier == TIER_PROBE else DEFAULT_ONLINE_LIMIT
    )
    return make_cache_key(tool, _canonical_args(args), tier, probe_limit=probe_limit)


def _norm(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _norm_lc(value: Any) -> str:
    return _norm(value).lower()


class CompetencyCache:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path or db_path())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def close(self) -> None:
        self._conn.close()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS atomic_calls (
                cache_key TEXT PRIMARY KEY,
                tool TEXT NOT NULL,
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
                data_json TEXT NOT NULL,
                FOREIGN KEY(cache_key) REFERENCES atomic_calls(cache_key)
            );
            CREATE INDEX IF NOT EXISTS idx_atomic_rows_key ON atomic_rows(cache_key);

            CREATE TABLE IF NOT EXISTS facet_identity (
                name_lc TEXT NOT NULL,
                mof TEXT,
                sourcedb TEXT,
                topology TEXT,
                space_group TEXT,
                refcode TEXT,
                mofid TEXT,
                cache_key TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_facet_identity_name ON facet_identity(name_lc);

            CREATE TABLE IF NOT EXISTS facet_synthesis (
                name_lc TEXT,
                refcode TEXT,
                sourcedb TEXT,
                method TEXT,
                solvent TEXT,
                temp TEXT,
                yield_val TEXT,
                doi TEXT,
                cache_key TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_facet_synthesis_name ON facet_synthesis(name_lc);
            CREATE INDEX IF NOT EXISTS idx_facet_synthesis_ref ON facet_synthesis(refcode);

            CREATE TABLE IF NOT EXISTS facet_topology_mof (
                topology_lc TEXT NOT NULL,
                mof TEXT,
                sourcedb TEXT,
                cache_key TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_facet_topo ON facet_topology_mof(topology_lc);

            CREATE TABLE IF NOT EXISTS facet_metal_source (
                metal_lc TEXT NOT NULL,
                sourcedb TEXT NOT NULL,
                mof_count INTEGER,
                cache_key TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_facet_metal ON facet_metal_source(metal_lc);

            CREATE TABLE IF NOT EXISTS facet_linker (
                name_lc TEXT NOT NULL,
                linker TEXT,
                sourcedb TEXT,
                cache_key TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_facet_linker_name ON facet_linker(name_lc);

            CREATE TABLE IF NOT EXISTS facet_refcodes (
                name_lc TEXT NOT NULL,
                refcode TEXT NOT NULL,
                sourcedb TEXT,
                cache_key TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_facet_refcodes_name ON facet_refcodes(name_lc);
            """
        )
        self._conn.commit()

    def has_tier(self, tool: str, args: Dict[str, Any], tier: str) -> bool:
        key = make_cache_key(tool, _canonical_args(args), tier, probe_limit=DEFAULT_ONLINE_LIMIT)
        cur = self._conn.execute(
            "SELECT 1 FROM atomic_calls WHERE cache_key = ? LIMIT 1", (key,)
        )
        return cur.fetchone() is not None

    def has_full(self, tool: str, args: Dict[str, Any]) -> bool:
        return self.has_tier(tool, args, TIER_FULL)

    def get(self, tool: str, args: Dict[str, Any], tier: str) -> Optional[List[Dict[str, Any]]]:
        key = make_cache_key(tool, _canonical_args(args), tier, probe_limit=DEFAULT_ONLINE_LIMIT)
        cur = self._conn.execute(
            "SELECT status FROM atomic_calls WHERE cache_key = ?", (key,)
        )
        row = cur.fetchone()
        if row is None:
            return None
        cur = self._conn.execute(
            "SELECT data_json FROM atomic_rows WHERE cache_key = ? ORDER BY row_index",
            (key,),
        )
        out: List[Dict[str, Any]] = []
        for r in cur:
            out.append(json.loads(r["data_json"]))
        return out

    def put(
        self,
        tool: str,
        args: Dict[str, Any],
        rows: List[Dict[str, Any]],
        *,
        tier: str,
        row_limit: Optional[int],
        elapsed_ms: int,
        status: str = "pass",
    ) -> str:
        key = make_cache_key(tool, _canonical_args(args), tier, probe_limit=DEFAULT_ONLINE_LIMIT)
        now = time.time()
        self._conn.execute("DELETE FROM atomic_rows WHERE cache_key = ?", (key,))
        self._conn.execute("DELETE FROM atomic_calls WHERE cache_key = ?", (key,))
        self._conn.execute(
            """
            INSERT INTO atomic_calls
            (cache_key, tool, args_json, mode, row_limit, endpoint, fetched_at, elapsed_ms, row_count, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                key,
                tool,
                json.dumps(_canonical_args(args)),
                tier,
                row_limit,
                DEFAULT_SPARQL_ENDPOINT,
                now,
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
        self._index_rows(tool, args, rows, key)
        self._conn.commit()
        return key

    def _clear_facets_for_key(self, cache_key: str, table: str) -> None:
        self._conn.execute(f"DELETE FROM {table} WHERE cache_key = ?", (cache_key,))

    def _index_rows(self, tool: str, args: Dict[str, Any], rows: List[Dict[str, Any]], ck: str) -> None:
        if tool == "get_mof_identity_by_name":
            self._clear_facets_for_key(ck, "facet_identity")
            name_lc = _norm_lc(args.get("mof_name", ""))
            for r in rows:
                self._conn.execute(
                    """
                    INSERT INTO facet_identity
                    (name_lc, mof, sourcedb, topology, space_group, refcode, mofid, cache_key)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        name_lc,
                        _norm(r.get("mof")),
                        _norm(r.get("sourcedb")),
                        _norm(r.get("topology")),
                        _norm(r.get("space_group")),
                        _norm(r.get("refcode")),
                        _norm(r.get("mofid")),
                        ck,
                    ),
                )
        elif tool == "get_synthesis_by_mof_name":
            self._clear_facets_for_key(ck, "facet_synthesis")
            name_lc = _norm_lc(args.get("mof_name", ""))
            for r in rows:
                self._conn.execute(
                    """
                    INSERT INTO facet_synthesis
                    (name_lc, refcode, sourcedb, method, solvent, temp, yield_val, doi, cache_key)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        name_lc,
                        _norm(r.get("refcode")),
                        _norm(r.get("sourcedb")),
                        _norm(r.get("method")),
                        _norm(r.get("solvent")),
                        _norm(r.get("temp")),
                        _norm(r.get("yield")),
                        _norm(r.get("doi")),
                        ck,
                    ),
                )
        elif tool == "get_mofs_with_same_topology_as" and not args.get("count_only"):
            topo = _norm_lc(rows[0].get("topology")) if rows else ""
            if not topo and args.get("reference_name"):
                id_rows = self.local_identity(args["reference_name"])
                topos = {_norm_lc(r.get("topology")) for r in id_rows if r.get("topology")}
                topo = sorted(topos)[0] if topos else ""
            if topo:
                self._conn.execute(
                    "DELETE FROM facet_topology_mof WHERE cache_key = ?", (ck,)
                )
                for r in rows:
                    self._conn.execute(
                        """
                        INSERT INTO facet_topology_mof (topology_lc, mof, sourcedb, cache_key)
                        VALUES (?, ?, ?, ?)
                        """,
                        (topo, _norm(r.get("mof")), _norm(r.get("source") or r.get("sourcedb")), ck),
                    )
        elif tool == "get_mofs_by_metal" and args.get("list_sources"):
            self._clear_facets_for_key(ck, "facet_metal_source")
            metal_lc = _norm_lc(args.get("metal", ""))
            for r in rows:
                cnt = r.get("count")
                try:
                    count_val = int(float(cnt)) if cnt is not None else 0
                except (TypeError, ValueError):
                    count_val = 0
                self._conn.execute(
                    """
                    INSERT INTO facet_metal_source (metal_lc, sourcedb, mof_count, cache_key)
                    VALUES (?, ?, ?, ?)
                    """,
                    (metal_lc, _norm(r.get("sourcedb")), count_val, ck),
                )
        elif tool == "get_linkers_by_mof_name":
            self._clear_facets_for_key(ck, "facet_linker")
            name_lc = _norm_lc(args.get("mof_name", ""))
            for r in rows:
                self._conn.execute(
                    """
                    INSERT INTO facet_linker (name_lc, linker, sourcedb, cache_key)
                    VALUES (?, ?, ?, ?)
                    """,
                    (name_lc, _norm(r.get("linker")), _norm(r.get("source")), ck),
                )
        elif tool == "get_refcodes_by_mof_name":
            self._clear_facets_for_key(ck, "facet_refcodes")
            name_lc = _norm_lc(args.get("mof_name", ""))
            for r in rows:
                self._conn.execute(
                    """
                    INSERT INTO facet_refcodes (name_lc, refcode, sourcedb, cache_key)
                    VALUES (?, ?, ?, ?)
                    """,
                    (name_lc, _norm(r.get("refcode")), _norm(r.get("sourcedb")), ck),
                )

    def local_identity(self, mof_name: str) -> List[Dict[str, Any]]:
        cur = self._conn.execute(
            """
            SELECT name_lc, mof, sourcedb, topology, space_group, refcode, mofid
            FROM facet_identity WHERE name_lc = ?
            """,
            (_norm_lc(mof_name),),
        )
        return [dict(r) for r in cur]

    def local_topology_sample(
        self,
        topology: str,
        *,
        limit: int = 100,
        exclude_name: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        topo_lc = _norm_lc(topology)
        cur = self._conn.execute(
            """
            SELECT DISTINCT mof, sourcedb, topology_lc AS topology
            FROM facet_topology_mof
            WHERE topology_lc = ?
            LIMIT ?
            """,
            (topo_lc, int(limit)),
        )
        rows = [dict(r) for r in cur]
        if exclude_name:
            ex = _norm_lc(exclude_name)
            id_rows = self.local_identity(exclude_name)
            ref_mofs = {_norm(r.get("mof")) for r in id_rows if r.get("mof")}
            rows = [r for r in rows if _norm(r.get("mof")) not in ref_mofs]
        return rows

    def local_topology_count(self, topology: str, exclude_name: Optional[str] = None) -> int:
        topo_lc = _norm_lc(topology)
        cur = self._conn.execute(
            "SELECT COUNT(DISTINCT mof) AS c FROM facet_topology_mof WHERE topology_lc = ?",
            (topo_lc,),
        )
        row = cur.fetchone()
        count = int(row["c"]) if row else 0
        if exclude_name and count:
            id_rows = self.local_identity(exclude_name)
            ref_mofs = {_norm(r.get("mof")) for r in id_rows if r.get("mof")}
            if ref_mofs:
                cur = self._conn.execute(
                    """
                    SELECT COUNT(DISTINCT mof) AS c FROM facet_topology_mof
                    WHERE topology_lc = ? AND mof NOT IN ({})
                    """.format(",".join("?" * len(ref_mofs))),
                    (topo_lc, *sorted(ref_mofs)),
                )
                row = cur.fetchone()
                count = int(row["c"]) if row else 0
        return count

    def local_synthesis_for_name(self, mof_name: str, refcodes: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        name_lc = _norm_lc(mof_name)
        if refcodes:
            placeholders = ",".join("?" * len(refcodes))
            cur = self._conn.execute(
                f"""
                SELECT name_lc, refcode, sourcedb, method, solvent, temp, yield_val, doi
                FROM facet_synthesis
                WHERE name_lc = ? AND refcode IN ({placeholders})
                """,
                (name_lc, *refcodes),
            )
        else:
            cur = self._conn.execute(
                """
                SELECT name_lc, refcode, sourcedb, method, solvent, temp, yield_val, doi
                FROM facet_synthesis WHERE name_lc = ?
                """,
                (name_lc,),
            )
        return [dict(r) for r in cur]

    def local_metal_sources(self, metal: str) -> List[Dict[str, Any]]:
        cur = self._conn.execute(
            """
            SELECT metal_lc, sourcedb, mof_count AS count
            FROM facet_metal_source WHERE metal_lc = ?
            ORDER BY mof_count DESC
            """,
            (_norm_lc(metal),),
        )
        return [dict(r) for r in cur]

    def stats(self) -> Dict[str, Any]:
        tables = [
            "atomic_calls",
            "facet_identity",
            "facet_synthesis",
            "facet_topology_mof",
            "facet_metal_source",
            "facet_linker",
            "facet_refcodes",
        ]
        counts = {}
        for t in tables:
            cur = self._conn.execute(f"SELECT COUNT(*) AS c FROM {t}")
            counts[t] = int(cur.fetchone()["c"])
        return {"db_path": str(self.path), "counts": counts}


def _tool_accepts_limit(fn: Callable[..., Any]) -> bool:
    return "limit" in inspect.signature(fn).parameters


def invoke_residual_sparql(
    query: str,
    *,
    mode: str = "online",
    online_limit: int = DEFAULT_ONLINE_LIMIT,
    use_cache: bool = True,
    cache: Optional[CompetencyCache] = None,
    timeout: int = 120,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Execute or load cached residual SPARQL (not tied to a named atomic tool)."""
    q = normalize_sparql(query)
    args = {"query": q}
    if mode == "offline":
        tier = TIER_FULL
        allow_remote = False
        exec_query = q
    elif mode == "warm":
        tier = TIER_FULL
        allow_remote = True
        exec_query = q
    else:
        tier = TIER_PROBE
        allow_remote = True
        exec_query = apply_sparql_limit(q, int(online_limit))

    ck = cache or CompetencyCache()
    own = cache is None
    meta: Dict[str, Any] = {
        "tool": RESIDUAL_TOOL,
        "mode": mode,
        "cache_tier": tier,
        "from_cache": False,
    }
    if use_cache:
        cached = ck.get(RESIDUAL_TOOL, args, tier)
        if cached is not None:
            meta["from_cache"] = True
            if own:
                ck.close()
            return cached, meta

    if not allow_remote:
        if own:
            ck.close()
        raise CacheMissError(
            f"Missing full cache for residual SPARQL (hash={args['query'][:40]!r}...). "
            "Warm with workflow sparql step or run online/warm first."
        )

    started = time.perf_counter()
    rows = execute_sparql(exec_query, DEFAULT_SPARQL_ENDPOINT, timeout=timeout)
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    ck.put(
        RESIDUAL_TOOL,
        args,
        rows,
        tier=tier,
        row_limit=row_limit_for_tier(tier, probe_limit=int(online_limit)),
        elapsed_ms=elapsed_ms,
        status="pass" if rows else "empty",
    )
    meta["elapsed_ms"] = elapsed_ms
    if own:
        ck.close()
    return rows, meta


def invoke_tool(
    tool: str,
    args: Dict[str, Any],
    *,
    mode: str = "online",
    online_limit: int = DEFAULT_ONLINE_LIMIT,
    offline_cap: Optional[int] = None,  # deprecated, ignored for offline
    use_cache: bool = True,
    cache: Optional[CompetencyCache] = None,
    allow_remote: bool = True,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    online: probe tier + remote SPARQL (small LIMIT).
    warm: full tier + remote SPARQL (uncapped) for pre-cache.
    offline: full tier, cache only (no remote).
    """
    del offline_cap  # noqa: kept for call-site compatibility
    fn = TOOL_REGISTRY.get(tool)
    if fn is None:
        raise ValueError(f"Unknown competency tool: {tool}")

    if mode == "offline":
        tier = TIER_FULL
        sparql_limit: Optional[int] = None
        allow_remote = False
    elif mode == "warm":
        tier = TIER_FULL
        sparql_limit = None
    else:
        tier = TIER_PROBE
        sparql_limit = int(online_limit)

    row_limit = row_limit_for_tier(tier, probe_limit=int(online_limit))
    call_args = dict(args)
    if _tool_accepts_limit(fn) and not args.get("count_only"):
        call_args["limit"] = sparql_limit

    ck_store = cache or CompetencyCache()
    own_cache = cache is None
    meta: Dict[str, Any] = {
        "tool": tool,
        "mode": mode,
        "cache_tier": tier,
        "row_limit": row_limit,
        "from_cache": False,
        "cache_key": None,
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
            return cached, meta

    if not allow_remote or mode == "offline":
        if own_cache:
            ck_store.close()
        raise CacheMissError(
            f"Missing full cache for tool={tool!r} args={_canonical_args(args)!r}. "
            f"Run: python -m mini_marie.mop_mof.mof.warm_competency_cache --tool {tool}"
        )

    started = time.perf_counter()
    rows = fn(**call_args)
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    status = "pass" if rows else "empty"
    key = ck_store.put(
        tool,
        args,
        rows,
        tier=tier,
        row_limit=row_limit,
        elapsed_ms=elapsed_ms,
        status=status,
    )
    meta["cache_key"] = key
    meta["elapsed_ms"] = elapsed_ms
    if own_cache:
        ck_store.close()
    return rows, meta


def warm_full_calls(
    specs: List[Dict[str, Any]],
    *,
    online_limit: int = DEFAULT_ONLINE_LIMIT,
    force: bool = False,
) -> Dict[str, Any]:
    """Pre-cache full (uncapped) results for atomic {tool, args} specs."""
    cache = CompetencyCache()
    results: List[Dict[str, Any]] = []
    for i, spec in enumerate(specs, 1):
        tool = spec["tool"]
        args = spec.get("args") or {}
        print(f"[{i}/{len(specs)}] warm {tool} {args} ...", flush=True)
        if force:
            key = make_cache_key(tool, _canonical_args(args), TIER_FULL, probe_limit=int(online_limit))
            cache._conn.execute("DELETE FROM atomic_rows WHERE cache_key = ?", (key,))
            cache._conn.execute("DELETE FROM atomic_calls WHERE cache_key = ?", (key,))
            cache._conn.commit()
        rows, meta = invoke_tool(
            tool,
            args,
            mode="warm",
            online_limit=online_limit,
            use_cache=not force,
            cache=cache,
        )
        results.append({"tool": tool, "args": args, "row_count": len(rows), **meta})
        print(f"  -> {len(rows)} rows in {meta.get('elapsed_ms')}ms", flush=True)
    stats = cache.stats()
    cache.close()
    return {"warmed": results, "cache_stats": stats}


def warm_probe_calls(
    probes: List[Dict[str, Any]],
    *,
    online_limit: int = DEFAULT_ONLINE_LIMIT,
    force: bool = False,
) -> Dict[str, Any]:
    """Online probe tier only (small LIMIT); use warm_full_calls before offline replay."""
    cache = CompetencyCache()
    results: List[Dict[str, Any]] = []
    for spec in probes:
        tool = spec["tool"]
        args = spec.get("args") or {}
        rows, meta = invoke_tool(
            tool,
            args,
            mode="online",
            online_limit=online_limit,
            use_cache=not force,
            cache=cache,
        )
        results.append({"tool": tool, "args": args, "row_count": len(rows), **meta})
    stats = cache.stats()
    cache.close()
    return {"probes": results, "cache_stats": stats}
