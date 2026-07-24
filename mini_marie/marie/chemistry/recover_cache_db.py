"""Recover chemistry cache DB by copying readable tables into a fresh file."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

from mini_marie.marie.chemistry.chemistry_cache import ChemistryCache, db_path


def _tables(conn: sqlite3.Connection) -> list[str]:
    return [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]


def _copy_table(src: sqlite3.Connection, dst: sqlite3.Connection, table: str) -> int:
    cols = [r[1] for r in src.execute(f"PRAGMA table_info({table})")]
    if not cols:
        return 0
    col_list = ", ".join(cols)
    placeholders = ", ".join("?" for _ in cols)
    create_sql = src.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if not create_sql or not create_sql[0]:
        return 0
    dst.execute(f"DROP TABLE IF EXISTS {table}")
    dst.execute(create_sql[0])
    n = 0
    cur = src.execute(f"SELECT {col_list} FROM {table}")
    while True:
        batch = cur.fetchmany(5000)
        if not batch:
            break
        dst.executemany(
            f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})",
            batch,
        )
        n += len(batch)
    return n


def main() -> None:
    src_path = Path(db_path())
    bak = src_path.with_suffix(".sqlite.bak2")
    new_path = src_path.with_name("chemistry_cache_recovered.sqlite")
    if not bak.exists():
        shutil.copy2(src_path, bak)
    src = sqlite3.connect(str(src_path))
    dst = sqlite3.connect(str(new_path))
    dst.execute("PRAGMA journal_mode=WAL")
    summary: dict[str, object] = {"copied": {}, "failed": {}}
    for table in _tables(src):
        try:
            n = _copy_table(src, dst, table)
            summary["copied"][table] = n
        except Exception as exc:
            summary["failed"][table] = str(exc)[:200]
    dst.commit()
    src.close()
    dst.close()

    # Re-init schema for any missing tables via ChemistryCache + corpus stores
    ChemistryCache(str(new_path)).close()
    from mini_marie.marie.chemistry.species_corpus import SpeciesCorpusStore

    SpeciesCorpusStore(str(new_path)).close()
    from mini_marie.marie.chemistry.species_pka_corpus import SpeciesPkaCorpusStore

    SpeciesPkaCorpusStore(str(new_path)).close()

    shutil.move(str(src_path), str(src_path.with_suffix(".sqlite.corrupt")))
    shutil.move(str(new_path), str(src_path))
    print(summary)


if __name__ == "__main__":
    main()
