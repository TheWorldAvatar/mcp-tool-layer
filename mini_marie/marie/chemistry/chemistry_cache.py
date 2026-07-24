"""
Local SQLite cache for chemistry competency atomic tools.

- probe tier: online MCP validation (LIMIT 5, remote GET SPARQL)
- full tier: warm pre-cache (namespace row cap, remote GET SPARQL)
- offline: read full tier only (no remote)
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from mini_marie.cache_paths import mini_marie_cache_root
from mini_marie.cache_tiers import (
    TIER_FULL,
    TIER_PROBE,
    CacheMissError,
    make_cache_key,
    row_limit_for_tier,
)
from mini_marie.marie.chemistry import query_builder as qb
from mini_marie.marie.chemistry.limits import (
    DEFAULT_ONLINE_PROBE_LIMIT,
    WARM_DELAY_SECONDS,
    WARM_MAX_RETRIES,
    WARM_RETRY_BACKOFF_BASE,
    WARM_RETRY_BACKOFF_MAX,
    warm_max_rows,
)
from mini_marie.marie.chemistry.registry import endpoint

CORPUS_OFFLINE_TOOLS = frozenset({"compare_rate_constants", "query_species_pka"})
CORPUS_DIRECT_OFFLINE_TOOLS = CORPUS_OFFLINE_TOOLS | frozenset(
    {
        "traverse_mechanism_reactions",
        "filter_by_literal",
        "query_zeolite_property",
        "query_calculation_results",
        "list_zeolite_numeric_props",
        "query_pka_enriched",
        "query_physprops_wide",
        "search_uses_enriched",
        "query_species_profile_top",
        "query_pka_with_provenance",
    }
)

TOOL_REGISTRY: Dict[str, RowFn] = {
    "lookup_individuals": qb.lookup_individuals_rows,
    "get_linked_values": qb.get_linked_values_rows,
    "filter_by_literal": qb.filter_by_literal_rows,
    "count_instances": qb.count_instances_rows,
    "traverse_mechanism_reactions": qb.traverse_mechanism_reactions_rows,
    "query_calculation_results": qb.query_calculation_results_rows,
    "query_zeolite_property": qb.query_zeolite_property_rows,
    "compare_rate_constants": qb.compare_rate_constants_rows,
    "query_species_pka": qb.query_species_pka_rows,
    "list_zeolite_numeric_props": qb.list_zeolite_numeric_props_rows,
    "query_pka_enriched": qb.query_pka_enriched_rows,
    "query_physprops_wide": qb.query_physprops_wide_rows,
    "search_uses_enriched": qb.search_uses_enriched_rows,
    "query_species_profile_top": qb.query_species_profile_top_rows,
    "query_pka_with_provenance": qb.query_pka_with_provenance_rows,
}


def cache_dir() -> Path:
    d = mini_marie_cache_root() / "chemistry"
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_path() -> Path:
    return cache_dir() / "chemistry_cache.sqlite"


def _canonical_args(args: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key in sorted(args):
        val = args[key]
        if isinstance(val, list):
            out[key] = sorted(str(v) for v in val)
        elif val is None:
            continue
        elif isinstance(val, str) and not val.strip():
            continue
        else:
            out[key] = val
    return out


def _norm(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _norm_lc(value: Any) -> str:
    return _norm(value).lower()


class ChemistryCache:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path or db_path())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.OperationalError:
            pass
        self._init_schema()

    def close(self) -> None:
        self._conn.close()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS atomic_calls (
                cache_key TEXT PRIMARY KEY,
                tool TEXT NOT NULL,
                namespace TEXT NOT NULL,
                args_json TEXT NOT NULL,
                mode TEXT NOT NULL,
                row_limit INTEGER,
                endpoint TEXT,
                fetched_at REAL NOT NULL,
                elapsed_ms INTEGER,
                row_count INTEGER NOT NULL,
                status TEXT NOT NULL,
                error TEXT
            );
            CREATE TABLE IF NOT EXISTS atomic_rows (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cache_key TEXT NOT NULL,
                row_index INTEGER NOT NULL,
                data_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_atomic_rows_key ON atomic_rows(cache_key);
            CREATE INDEX IF NOT EXISTS idx_atomic_calls_ns ON atomic_calls(namespace);

            CREATE TABLE IF NOT EXISTS facet_species (
                namespace TEXT NOT NULL,
                species TEXT NOT NULL,
                label TEXT,
                formula TEXT,
                smiles TEXT,
                cache_key TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_facet_species_ns ON facet_species(namespace);

            CREATE TABLE IF NOT EXISTS facet_pka (
                namespace TEXT NOT NULL,
                species TEXT NOT NULL,
                pka_value TEXT,
                cache_key TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_facet_pka_species ON facet_pka(species);

            CREATE TABLE IF NOT EXISTS facet_mechanism (
                mechanism TEXT,
                reaction TEXT,
                equation TEXT,
                cache_key TEXT
            );

            CREATE TABLE IF NOT EXISTS facet_zeolite (
                framework_code TEXT,
                material TEXT,
                formula TEXT,
                cache_key TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_facet_zeolite_code ON facet_zeolite(framework_code);
            """
        )
        self._conn.commit()

    def has_tier(self, tool: str, args: Dict[str, Any], tier: str) -> bool:
        key = make_cache_key(
            tool, _canonical_args(args), tier, probe_limit=DEFAULT_ONLINE_PROBE_LIMIT
        )
        cur = self._conn.execute(
            "SELECT 1 FROM atomic_calls WHERE cache_key = ? LIMIT 1", (key,)
        )
        return cur.fetchone() is not None

    def has_full(self, tool: str, args: Dict[str, Any]) -> bool:
        return self.has_tier(tool, args, TIER_FULL)

    def get(self, tool: str, args: Dict[str, Any], tier: str) -> Optional[List[Dict[str, Any]]]:
        key = make_cache_key(
            tool, _canonical_args(args), tier, probe_limit=DEFAULT_ONLINE_PROBE_LIMIT
        )
        cur = self._conn.execute(
            "SELECT 1 FROM atomic_calls WHERE cache_key = ?", (key,)
        )
        if cur.fetchone() is None:
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
        tier: str,
        row_limit: Optional[int],
        elapsed_ms: int,
        status: str = "pass",
        error: str = "",
    ) -> str:
        ns = str(args.get("namespace", ""))
        key = make_cache_key(
            tool, _canonical_args(args), tier, probe_limit=DEFAULT_ONLINE_PROBE_LIMIT
        )
        now = time.time()
        self._conn.execute("DELETE FROM atomic_rows WHERE cache_key = ?", (key,))
        self._conn.execute("DELETE FROM atomic_calls WHERE cache_key = ?", (key,))
        self._conn.execute(
            """
            INSERT INTO atomic_calls
            (cache_key, tool, namespace, args_json, mode, row_limit, endpoint,
             fetched_at, elapsed_ms, row_count, status, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                key,
                tool,
                ns,
                json.dumps(_canonical_args(args)),
                tier,
                row_limit,
                endpoint(ns) if ns else "",
                now,
                elapsed_ms,
                len(rows),
                status,
                error[:500],
            ),
        )
        for i, row in enumerate(rows):
            self._conn.execute(
                "INSERT INTO atomic_rows (cache_key, row_index, data_json) VALUES (?, ?, ?)",
                (key, i, json.dumps(row, ensure_ascii=False)),
            )
        if status == "pass" and rows:
            self._index_rows(tool, args, rows, key)
        self._conn.commit()
        return key

    def _clear_facet_key(self, table: str, cache_key: str) -> None:
        self._conn.execute(f"DELETE FROM {table} WHERE cache_key = ?", (cache_key,))

    def _index_rows(
        self, tool: str, args: Dict[str, Any], rows: List[Dict[str, Any]], ck: str
    ) -> None:
        ns = str(args.get("namespace", ""))
        if tool == "lookup_individuals" and ns == "ontospecies":
            self._clear_facet_key("facet_species", ck)
            for r in rows:
                self._conn.execute(
                    """
                    INSERT INTO facet_species (namespace, species, label, cache_key)
                    VALUES (?, ?, ?, ?)
                    """,
                    (ns, _norm(r.get("subject")), _norm(r.get("label")), ck),
                )
        elif tool == "get_linked_values" and args.get("link_property") == "hasDissociationConstants":
            self._clear_facet_key("facet_pka", ck)
            for r in rows:
                self._conn.execute(
                    """
                    INSERT INTO facet_pka (namespace, species, pka_value, cache_key)
                    VALUES (?, ?, ?, ?)
                    """,
                    (ns, _norm(r.get("subject")), _norm(r.get("value")), ck),
                )
        elif tool == "traverse_mechanism_reactions":
            self._clear_facet_key("facet_mechanism", ck)
            for r in rows:
                self._conn.execute(
                    """
                    INSERT INTO facet_mechanism (mechanism, reaction, equation, cache_key)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        _norm(r.get("mechanism")),
                        _norm(r.get("reaction")),
                        _norm(r.get("equation")),
                        ck,
                    ),
                )
        elif tool in ("filter_by_literal", "query_zeolite_property") and ns == "ontozeolite":
            self._clear_facet_key("facet_zeolite", ck)
            code = _norm(args.get("framework_code") or args.get("value_fragment"))
            for r in rows:
                self._conn.execute(
                    """
                    INSERT INTO facet_zeolite (framework_code, material, formula, cache_key)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        code.upper(),
                        _norm(r.get("subject") or r.get("material")),
                        _norm(r.get("propVal") or r.get("formula")),
                        ck,
                    ),
                )

    def stats(self) -> Dict[str, Any]:
        tables = [
            "atomic_calls",
            "atomic_rows",
            "facet_species",
            "facet_pka",
            "facet_mechanism",
            "facet_zeolite",
        ]
        counts = {}
        for t in tables:
            cur = self._conn.execute(f"SELECT COUNT(*) AS c FROM {t}")
            counts[t] = int(cur.fetchone()["c"])
        return {"db_path": str(self.path), "counts": counts}


def _dispatch_tool(
    tool: str, args: Dict[str, Any], row_limit: Optional[int]
) -> List[Dict[str, Any]]:
    fn = TOOL_REGISTRY.get(tool)
    if fn is None:
        raise ValueError(f"Unknown chemistry cache tool: {tool}")
    ns = str(args.get("namespace", ""))
    if not ns:
        raise ValueError(f"args must include namespace for tool {tool}")

    a = {k: v for k, v in args.items() if k != "namespace"}

    if tool == "lookup_individuals":
        return fn(
            ns,
            a.get("class_local", ""),
            a.get("label_fragment", ""),
            a.get("identifier_type", ""),
            row_limit=row_limit,
        )
    if tool == "get_linked_values":
        return fn(
            ns,
            a.get("class_local", ""),
            a.get("subject_label", ""),
            a.get("link_property", ""),
            a.get("value_properties"),
            a.get("include_metadata", False),
            a.get("identifier_type", ""),
            a.get("related_class", ""),
            row_limit=row_limit,
        )
    if tool == "filter_by_literal":
        return fn(
            ns,
            a.get("class_local", ""),
            a.get("property_local", ""),
            a.get("value_fragment", ""),
            a.get("match", "contains"),
            a.get("use_value_node", False),
            a.get("subject_label", ""),
            row_limit=row_limit,
        )
    if tool == "count_instances":
        req = a.get("required_property") or None
        return fn(ns, a.get("class_local", ""), req)
    if tool == "traverse_mechanism_reactions":
        return fn(
            ns,
            a.get("mechanism_label", ""),
            a.get("reaction_fragment", ""),
            a.get("mechanism_iri_fragment", ""),
            row_limit=row_limit,
        )
    if tool == "query_calculation_results":
        return fn(
            ns,
            a.get("species_label", ""),
            a.get("result_kinds"),
            a.get("method_fragment", ""),
            a.get("basis_fragment", ""),
            row_limit=row_limit,
        )
    if tool == "query_zeolite_property":
        return fn(
            ns,
            a.get("material_label", ""),
            a.get("framework_code", ""),
            a.get("property_local", ""),
            a.get("value_filter", ""),
            row_limit=row_limit,
        )
    if tool == "compare_rate_constants":
        return fn(ns, a.get("equation_fragment", ""), row_limit=row_limit)
    if tool == "query_species_pka":
        return fn(
            ns,
            a.get("reliability", ""),
            a.get("method", ""),
            a.get("acidity_label", ""),
            row_limit=row_limit,
        )
    if tool == "list_zeolite_numeric_props":
        return fn(ns, row_limit=row_limit)
    if tool == "query_pka_enriched":
        return fn(
            ns,
            a.get("species_fragment", ""),
            a.get("reliability_fragment", ""),
            a.get("method_fragment", ""),
            a.get("ref_label_fragment", ""),
            a.get("acidity_label_fragment", ""),
            row_limit=row_limit,
        )
    if tool == "query_physprops_wide":
        return fn(ns, a.get("smiles", ""), row_limit=row_limit)
    if tool == "search_uses_enriched":
        return fn(ns, a.get("use_fragment", ""), row_limit=row_limit)
    if tool == "query_species_profile_top":
        lim = a.get("limit") or row_limit
        return fn(ns, row_limit=lim)
    if tool == "query_pka_with_provenance":
        return fn(ns, a.get("species_fragment", ""), row_limit=row_limit)
    raise ValueError(f"Unhandled tool: {tool}")


def invoke_tool(
    tool: str,
    args: Dict[str, Any],
    *,
    mode: str = "online",
    online_limit: int = DEFAULT_ONLINE_PROBE_LIMIT,
    use_cache: bool = True,
    cache: Optional[ChemistryCache] = None,
    allow_remote: bool = True,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    online: probe tier + remote SPARQL (LIMIT online_limit).
    warm: full tier + remote SPARQL (namespace warm cap).
    offline: full tier, cache only.
    """
    if tool not in TOOL_REGISTRY:
        raise ValueError(f"Unknown chemistry cache tool: {tool}")

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
    ck_store = cache or ChemistryCache()
    own_cache = cache is None
    meta: Dict[str, Any] = {
        "tool": tool,
        "namespace": args.get("namespace"),
        "mode": mode,
        "cache_tier": tier,
        "row_limit": row_limit,
        "warm_cap": warm_max_rows(str(args.get("namespace", ""))) if sparql_limit is None else None,
        "from_cache": False,
        "cache_key": None,
    }

    skip_cache = mode == "offline" and tool in CORPUS_DIRECT_OFFLINE_TOOLS
    if use_cache and not skip_cache:
        cached = ck_store.get(tool, args, tier)
        if cached is not None:
            meta["from_cache"] = True
            meta["cache_key"] = make_cache_key(
                tool, _canonical_args(args), tier, probe_limit=int(online_limit)
            )
            if own_cache:
                ck_store.close()
            return cached, meta

    if not allow_remote:
        if tool in CORPUS_DIRECT_OFFLINE_TOOLS:
            rows = _dispatch_tool(tool, args, row_limit)
            if own_cache:
                ck_store.close()
            return rows, meta
        if own_cache:
            ck_store.close()
        raise CacheMissError(
            f"Missing full cache for tool={tool!r} args={_canonical_args(args)!r}. "
            f"Run: python -m mini_marie.marie.chemistry.warm_chemistry_cache --tool {tool} "
            f"--namespace {args.get('namespace')!r}"
        )

    started = time.perf_counter()
    rows = _dispatch_tool(tool, args, sparql_limit)
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


def _retry_invoke(
    tool: str,
    args: Dict[str, Any],
    *,
    online_limit: int,
    force: bool,
    cache: ChemistryCache,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    last_exc: Optional[Exception] = None
    for attempt in range(1, WARM_MAX_RETRIES + 1):
        try:
            return invoke_tool(
                tool,
                args,
                mode="warm",
                online_limit=online_limit,
                use_cache=not force,
                cache=cache,
            )
        except Exception as exc:
            last_exc = exc
            if attempt >= WARM_MAX_RETRIES:
                break
            delay = min(WARM_RETRY_BACKOFF_MAX, WARM_RETRY_BACKOFF_BASE * attempt)
            print(f"  retry {attempt}/{WARM_MAX_RETRIES} after {delay:.0f}s: {exc}", flush=True)
            time.sleep(delay)
    assert last_exc is not None
    cache.put(
        tool,
        args,
        [],
        tier=TIER_FULL,
        row_limit=None,
        elapsed_ms=0,
        status="error",
        error=str(last_exc),
    )
    raise last_exc


def warm_full_calls(
    specs: List[Dict[str, Any]],
    *,
    online_limit: int = DEFAULT_ONLINE_PROBE_LIMIT,
    force: bool = False,
    delay_seconds: float = WARM_DELAY_SECONDS,
    show_progress: bool = True,
) -> Dict[str, Any]:
    """Pre-cache full-tier results with retries and inter-call delay."""
    import sys

    try:
        from tqdm import tqdm
    except ImportError:
        tqdm = None  # type: ignore[assignment,misc]

    cache = ChemistryCache()
    results: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    use_bar = (
        show_progress
        and tqdm is not None
        and len(specs) > 1
        and sys.stderr.isatty()
    )
    iterator = tqdm(
        specs,
        desc="Warming chemistry cache",
        unit="spec",
        dynamic_ncols=True,
    ) if use_bar else specs

    for i, spec in enumerate(iterator, 1):
        tool = spec["tool"]
        args = dict(spec.get("args") or {})
        ns = str(args.get("namespace", "?"))
        canon = json.dumps(_canonical_args(args))
        if use_bar:
            iterator.set_postfix_str(f"{ns}/{tool}"[:60], refresh=False)
        else:
            print(f"[{i}/{len(specs)}] warm {ns}/{tool} {canon}", flush=True)
        if force:
            key = make_cache_key(
                tool, _canonical_args(args), TIER_FULL, probe_limit=int(online_limit)
            )
            cache._conn.execute("DELETE FROM atomic_rows WHERE cache_key = ?", (key,))
            cache._conn.execute("DELETE FROM atomic_calls WHERE cache_key = ?", (key,))
            cache._conn.commit()
        try:
            rows, meta = _retry_invoke(
                tool, args, online_limit=online_limit, force=force, cache=cache
            )
            item = {"tool": tool, "args": args, "row_count": len(rows), **meta}
            results.append(item)
            if use_bar:
                iterator.set_postfix(
                    ns=ns,
                    tool=tool[:20],
                    rows=len(rows),
                    ms=meta.get("elapsed_ms"),
                    refresh=False,
                )
            else:
                print(f"  -> {len(rows)} rows in {meta.get('elapsed_ms')}ms", flush=True)
        except Exception as exc:
            err_item = {"tool": tool, "args": args, "error": str(exc)[:300]}
            errors.append(err_item)
            if use_bar:
                iterator.set_postfix(ns=ns, err="!", refresh=False)
            else:
                print(f"  !! failed: {exc}", flush=True)
        if i < len(specs) and delay_seconds > 0:
            time.sleep(delay_seconds)

    if use_bar:
        iterator.close()

    stats = cache.stats()
    cache.close()
    return {"warmed": results, "errors": errors, "cache_stats": stats}


def warm_probe_calls(
    specs: List[Dict[str, Any]],
    *,
    online_limit: int = DEFAULT_ONLINE_PROBE_LIMIT,
    force: bool = False,
) -> Dict[str, Any]:
    cache = ChemistryCache()
    results: List[Dict[str, Any]] = []
    for spec in specs:
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
