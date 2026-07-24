"""Shared helpers for chemistry corpus warm stores."""

from __future__ import annotations

import sqlite3
import time
from typing import Any, Dict, Optional


def migrate_cursor_column(conn: sqlite3.Connection) -> None:
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "corpus_warm_state" not in tables:
        return
    cols = {row[1] for row in conn.execute("PRAGMA table_info(corpus_warm_state)")}
    if "cursor_subject" not in cols:
        conn.execute(
            "ALTER TABLE corpus_warm_state ADD COLUMN cursor_subject TEXT NOT NULL DEFAULT ''"
        )
        conn.commit()


def warm_state_row(conn: sqlite3.Connection, corpus_id: str) -> Dict[str, Any]:
    cur = conn.execute("SELECT * FROM corpus_warm_state WHERE corpus_id = ?", (corpus_id,))
    row = cur.fetchone()
    if row is None:
        return {"corpus_id": corpus_id, "offset_next": 0, "cursor_subject": "", "status": "idle"}
    return dict(row)


def ensure_warm_state(
    conn: sqlite3.Connection,
    *,
    corpus_id: str,
    total: int,
    batch_size: int,
) -> None:
    conn.execute(
        """
        INSERT INTO corpus_warm_state
        (corpus_id, total_species, offset_next, batch_size, species_indexed,
         name_rows_indexed, batches_done, status, updated_at, cursor_subject)
        VALUES (?, ?, 0, ?, 0, 0, 0, 'running', ?, '')
        ON CONFLICT(corpus_id) DO UPDATE SET
          total_species=excluded.total_species,
          batch_size=excluded.batch_size,
          status='running',
          updated_at=excluded.updated_at
        """,
        (corpus_id, total, batch_size, time.time()),
    )
    conn.commit()


def mark_paused(conn: sqlite3.Connection, corpus_id: str, error: str) -> None:
    conn.execute(
        """
        UPDATE corpus_warm_state SET status='paused', error=?, updated_at=?
        WHERE corpus_id = ?
        """,
        (error[:500], time.time(), corpus_id),
    )
    conn.commit()


def advance_warm_state(
    conn: sqlite3.Connection,
    *,
    corpus_id: str,
    offset: int,
    species_added: int,
    rows_added: int,
    batch_size: int,
    total: int,
    done: bool,
    cursor_subject: str = "",
) -> None:
    state = warm_state_row(conn, corpus_id)
    batches = int(state.get("batches_done") or 0) + 1
    conn.execute(
        """
        UPDATE corpus_warm_state SET
          offset_next = ?,
          cursor_subject = ?,
          batch_size = ?,
          species_indexed = species_indexed + ?,
          name_rows_indexed = name_rows_indexed + ?,
          batches_done = ?,
          total_species = ?,
          status = ?,
          updated_at = ?,
          error = ''
        WHERE corpus_id = ?
        """,
        (
            offset + species_added,
            cursor_subject,
            batch_size,
            species_added,
            rows_added,
            batches,
            total,
            "complete" if done else "running",
            time.time(),
            corpus_id,
        ),
    )
    conn.commit()


def resolve_cursor(
    conn: sqlite3.Connection,
    *,
    corpus_id: str,
    offset: int,
    bootstrap_fn,
) -> str:
    state = warm_state_row(conn, corpus_id)
    cursor = str(state.get("cursor_subject") or "").strip()
    if cursor or offset <= 0:
        return cursor
    return bootstrap_fn(offset - 1)
