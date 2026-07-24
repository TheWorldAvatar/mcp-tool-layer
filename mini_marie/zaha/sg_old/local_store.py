"""SQLite materialization of cached sg-old NDJSON triples."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from mini_marie.cache_paths import mini_marie_cache_root
from mini_marie.zaha.sg_old.probe_and_cache import WORKING_NAMESPACES

DEFAULT_LIMIT = 25


def cache_dir() -> Path:
    d = mini_marie_cache_root() / "sg_old"
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_path() -> Path:
    return cache_dir() / "sg_cache.sqlite"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    return conn


def ensure_db(force_rebuild: bool = False) -> Path:
    path = db_path()
    if path.exists() and not force_rebuild:
        return path
    if path.exists():
        path.unlink()
    conn = _connect()
    conn.execute(
        "CREATE TABLE triples (ns TEXT NOT NULL, s TEXT, p TEXT, o TEXT)"
    )
    conn.execute("CREATE INDEX idx_ns_s ON triples(ns, s)")
    conn.execute("CREATE INDEX idx_ns_p ON triples(ns, p)")
    conn.execute("CREATE INDEX idx_ns_o ON triples(ns, o)")
    for ns in WORKING_NAMESPACES:
        ndjson = cache_dir() / f"{ns}_triples.ndjson"
        if not ndjson.exists():
            continue
        batch: List[Tuple[str, str, str, str]] = []
        with ndjson.open(encoding="utf-8") as f:
            for line in f:
                row = json.loads(line)
                batch.append((ns, row.get("s", ""), row.get("p", ""), row.get("o", "")))
                if len(batch) >= 5000:
                    conn.executemany("INSERT INTO triples VALUES (?,?,?,?)", batch)
                    batch.clear()
        if batch:
            conn.executemany("INSERT INTO triples VALUES (?,?,?,?)", batch)
    conn.commit()
    conn.close()
    return path


def graph_stats() -> List[Dict[str, Any]]:
    ensure_db()
    conn = _connect()
    rows = conn.execute(
        """
        SELECT ns,
               COUNT(*) AS triples,
               COUNT(DISTINCT s) AS subjects,
               COUNT(DISTINCT p) AS predicates
        FROM triples GROUP BY ns ORDER BY ns
        """
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def query_triples(
    ns: str,
    *,
    s: Optional[str] = None,
    p: Optional[str] = None,
    o: Optional[str] = None,
    p_contains: Optional[str] = None,
    s_type: Optional[str] = None,
    limit: int = DEFAULT_LIMIT,
) -> List[Dict[str, str]]:
    ensure_db()
    clauses = ["ns = ?"]
    params: List[Any] = [ns]
    if s:
        clauses.append("s = ?")
        params.append(s)
    if p:
        clauses.append("p = ?")
        params.append(p)
    if o:
        clauses.append("o = ?")
        params.append(o)
    if p_contains:
        clauses.append("p LIKE ?")
        params.append(f"%{p_contains}%")
    if s_type:
        clauses.append(
            "s IN (SELECT s FROM triples WHERE ns = ? AND p LIKE '%rdf-syntax-ns#type' AND o = ?)"
        )
        params.extend([ns, s_type])
    sql = f"SELECT s, p, o FROM triples WHERE {' AND '.join(clauses)} LIMIT {int(limit)}"
    conn = _connect()
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [{"s": r["s"], "p": r["p"], "o": r["o"]} for r in rows]


def subjects_by_type(ns: str, type_iri: str, limit: int = DEFAULT_LIMIT) -> List[str]:
    rows = query_triples(
        ns,
        p="http://www.w3.org/1999/02/22-rdf-syntax-ns#type",
        o=type_iri,
        limit=limit,
    )
    return [r["s"] for r in rows]


def object_values(ns: str, s: str, p: str, limit: int = DEFAULT_LIMIT) -> List[str]:
    return [r["o"] for r in query_triples(ns, s=s, p=p, limit=limit)]
