"""SPARQL helpers for residual workflow steps."""

from __future__ import annotations

import hashlib
import re
from typing import Optional

RESIDUAL_TOOL = "residual_sparql"


def normalize_sparql(query: str) -> str:
    return query.strip()


def sparql_query_hash(query: str) -> str:
    return hashlib.sha256(normalize_sparql(query).encode()).hexdigest()[:16]


def apply_sparql_limit(query: str, limit: Optional[int]) -> str:
    """Append LIMIT if missing and limit is set."""
    if limit is None:
        return query
    if re.search(r"\bLIMIT\s+\d+", query, flags=re.IGNORECASE):
        return query
    q = query.rstrip().rstrip(";")
    return f"{q}\nLIMIT {int(limit)}\n"
