"""Local label IRI indexes + fuzzy search + carpark geocoding for sg-old."""

from __future__ import annotations

import json
import math
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib import parse, request

from mini_marie.cache_paths import mini_marie_cache_root
from mini_marie.zaha.sg_old.local_store import db_path, ensure_db

SG_OLD_DIR = mini_marie_cache_root() / "sg_old"
CARPARK_LABELS_DB = SG_OLD_DIR / "carpark_labels.sqlite"
KB_LABELS_DB = SG_OLD_DIR / "kb_labels.sqlite"
CARPARK_GEOCODE_DB = SG_OLD_DIR / "carpark_geocode.sqlite"

# Nominatim geocode for CREATE Tower (verified probe)
CREATE_TOWER_REF = {
    "name": "CREATE Tower",
    "lat": 1.3038595,
    "lon": 103.7735267,
    "source": "nominatim:CREATE Tower Singapore",
}

# Probe-verified geocodes (Nominatim); HDB block literals often need variant queries
SEED_CARPARK_GEOCODES: Dict[str, Tuple[float, float]] = {
    "Blk 463 Clementi Avenue 1": (1.3060276, 103.7695026),
    "Blk 101-104 Clementi Street 14": (1.3229887, 103.7686168),
    "Blk 20/22/23 Dover Crescent": (1.308741, 103.785736),
}

NEAR_CREATE_KEYWORDS = (
    "kent ridge",
    "lower kent",
    "kent crescent",
    "science drive",
    "engineering",
    "prince george",
    "one north",
    "one-north",
    "fusionopolis",
    "clementi",
    "dover",
    "pasir panjang",
    "west coast",
    "holland",
    "commonwealth",
    "buona vista",
    "national university",
    "nus",
    "university town",
    "lower kent ridge",
)


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def build_label_indexes(*, force: bool = False) -> Dict[str, Any]:
    """Materialize carpark + kb label SQLite indexes from Blazegraph cache."""
    ensure_db()
    SG_OLD_DIR.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(db_path())

    stats: Dict[str, Any] = {}
    for ns, out_path, table in [
        ("carpark", CARPARK_LABELS_DB, "carpark_label"),
        ("kb", KB_LABELS_DB, "kb_label"),
    ]:
        if out_path.exists() and not force:
            stats[table] = _connect(out_path).execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
            continue
        rows = src.execute(
            "SELECT s, o FROM triples WHERE ns=? AND p LIKE '%label%'",
            (ns,),
        ).fetchall()
        if out_path.exists():
            out_path.unlink()
        conn = _connect(out_path)
        conn.execute(f"CREATE TABLE {table} (iri TEXT PRIMARY KEY, label TEXT, label_lc TEXT)")
        conn.executemany(
            f"INSERT OR REPLACE INTO {table} VALUES (?,?,?)",
            [(s, o, (o or "").lower()) for s, o in rows],
        )
        conn.execute(f"CREATE INDEX idx_{table}_lc ON {table}(label_lc)")
        conn.commit()
        conn.close()
        stats[table] = len(rows)

    src.close()
    return stats


def fuzzy_search_labels(
    query: str,
    *,
    source: str = "all",
    limit: int = 15,
) -> List[Dict[str, Any]]:
    """Fuzzy label search over carpark and/or kb label indexes."""
    build_label_indexes()
    frag = f"%{query.lower().strip()}%"
    out: List[Dict[str, Any]] = []

    if source in ("all", "carpark") and CARPARK_LABELS_DB.exists():
        conn = _connect(CARPARK_LABELS_DB)
        for r in conn.execute(
            "SELECT iri, label FROM carpark_label WHERE label_lc LIKE ? LIMIT ?",
            (frag, int(limit)),
        ):
            out.append({"source": "carpark", "iri": r["iri"], "label": r["label"]})
        conn.close()

    if source in ("all", "kb") and KB_LABELS_DB.exists():
        conn = _connect(KB_LABELS_DB)
        for r in conn.execute(
            "SELECT iri, label FROM kb_label WHERE label_lc LIKE ? LIMIT ?",
            (frag, int(limit)),
        ):
            out.append({"source": "kb", "iri": r["iri"], "label": r["label"]})
        conn.close()

    if source in ("all", "building"):
        from mini_marie.zaha.sg_old.ontop_store import cache_ready, connect

        if cache_ready():
            conn = connect()
            for r in conn.execute(
                "SELECT building_iri, name FROM facet_building_name WHERE name_lc LIKE ? LIMIT ?",
                (frag, int(limit)),
            ):
                out.append({"source": "building", "iri": r["building_iri"], "label": r["name"]})
            conn.close()

    return out[:limit]


def _geocode_query(q: str) -> Optional[Tuple[float, float]]:
    url = "https://nominatim.openstreetmap.org/search?" + parse.urlencode(
        {"q": q, "format": "json", "limit": 1}
    )
    req = request.Request(url, headers={"User-Agent": "sg-old-label-store/1.0"})
    try:
        with request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data:
            return float(data[0]["lat"]), float(data[0]["lon"])
    except Exception:
        return None
    return None


def _geocode_variants(label: str) -> List[str]:
    base = (label or "").strip()
    variants = [f"{base}, Singapore"]
    low = base.lower()
    if low.startswith("blk "):
        rest = base[4:].strip()
        variants.append(f"{rest}, Singapore")
        # Drop leading block number cluster -> street name
        parts = rest.split(" ", 1)
        if len(parts) == 2:
            variants.append(f"{parts[1]}, Singapore")
    return variants


def _geocode_nominatim(label: str, *, variant_pause_s: float = 0.0) -> Optional[Tuple[float, float]]:
    for i, q in enumerate(_geocode_variants(label)):
        if i and variant_pause_s:
            time.sleep(variant_pause_s)
        coords = _geocode_query(q)
        if coords:
            return coords
    return None


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _init_geocode_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS carpark_geocode (
          iri TEXT PRIMARY KEY,
          label TEXT,
          lat REAL,
          lon REAL,
          geocode_source TEXT,
          geocoded_at TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cg_label ON carpark_geocode(label)")


def warm_carpark_geocodes_near_create(*, sleep_s: float = 2.0, live_geocode: bool = False) -> Dict[str, Any]:
    """Geocode carpark labels matching near-CREATE keywords; cache results locally."""
    build_label_indexes()
    conn_labels = _connect(CARPARK_LABELS_DB)
    candidates = conn_labels.execute("SELECT iri, label FROM carpark_label").fetchall()
    conn_labels.close()

    selected: List[Tuple[str, str]] = []
    for iri, label in candidates:
        lc = (label or "").lower()
        if any(kw in lc for kw in NEAR_CREATE_KEYWORDS):
            selected.append((iri, label))

    SG_OLD_DIR.mkdir(parents=True, exist_ok=True)
    conn = _connect(CARPARK_GEOCODE_DB)
    _init_geocode_db(conn)

    updated = 0
    for iri, label in selected:
        existing = conn.execute("SELECT lat FROM carpark_geocode WHERE iri=?", (iri,)).fetchone()
        if existing and existing["lat"] is not None:
            continue
        if label in SEED_CARPARK_GEOCODES:
            lat, lon = SEED_CARPARK_GEOCODES[label]
            conn.execute(
                """
                INSERT OR REPLACE INTO carpark_geocode (iri, label, lat, lon, geocode_source, geocoded_at)
                VALUES (?,?,?,?,?,datetime('now'))
                """,
                (iri, label, lat, lon, "seed_probe"),
            )
            updated += 1
            continue
        if not live_geocode:
            continue
        time.sleep(sleep_s)
        coords = _geocode_nominatim(label, variant_pause_s=1.5)
        if coords:
            conn.execute(
                """
                INSERT OR REPLACE INTO carpark_geocode (iri, label, lat, lon, geocode_source, geocoded_at)
                VALUES (?,?,?,?,?,datetime('now'))
                """,
                (iri, label, coords[0], coords[1], "nominatim"),
            )
            updated += 1
    conn.commit()
    total = conn.execute("SELECT COUNT(*) AS n FROM carpark_geocode WHERE lat IS NOT NULL").fetchone()["n"]
    conn.close()
    return {
        "candidates_near_create": len(selected),
        "newly_geocoded": updated,
        "cached_with_coords": total,
        "db": str(CARPARK_GEOCODE_DB),
    }


def find_nearest_carpark_to_create(*, limit: int = 5) -> List[Dict[str, Any]]:
    """Nearest geocoded carparks to CREATE Tower reference point."""
    warm = warm_carpark_geocodes_near_create(live_geocode=False)
    if not CARPARK_GEOCODE_DB.exists():
        return [{"error": "geocode cache missing", **warm}]

    ref_lat, ref_lon = CREATE_TOWER_REF["lat"], CREATE_TOWER_REF["lon"]
    conn = _connect(CARPARK_GEOCODE_DB)
    rows = conn.execute(
        "SELECT iri, label, lat, lon FROM carpark_geocode WHERE lat IS NOT NULL"
    ).fetchall()
    conn.close()

    if not rows:
        return [
            {
                "reference": CREATE_TOWER_REF,
                "note": "No geocoded carparks yet; run warm_carpark_geocodes_near_create",
                **warm,
            }
        ]

    ranked = []
    for r in rows:
        d = _haversine_m(ref_lat, ref_lon, r["lat"], r["lon"])
        ranked.append(
            {
                "carpark_iri": r["iri"],
                "label": r["label"],
                "lat": r["lat"],
                "lon": r["lon"],
                "distance_m": round(d),
                "reference": CREATE_TOWER_REF["name"],
                "reference_lat": ref_lat,
                "reference_lon": ref_lon,
            }
        )
    ranked.sort(key=lambda x: x["distance_m"])
    return ranked[:limit]
