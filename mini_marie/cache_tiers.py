"""
Cache tiers for atomic tool results.

- probe: online workflow validation (small LIMIT, may hit remote endpoint)
- full: complete atomic result (no row cap); used by offline replay (cache-only)
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional

TIER_PROBE = "probe"
TIER_FULL = "full"


class CacheMissError(LookupError):
    """Raised when offline replay requires a full-tier cache entry that is missing."""


def row_limit_for_tier(tier: str, *, probe_limit: int = 10) -> Optional[int]:
    if tier == TIER_PROBE:
        return int(probe_limit)
    if tier == TIER_FULL:
        return None
    raise ValueError(f"Unknown cache tier: {tier}")


def make_cache_key(tool: str, args: Dict[str, Any], tier: str, *, probe_limit: int = 10) -> str:
    row_limit = row_limit_for_tier(tier, probe_limit=probe_limit)
    payload = {
        "tool": tool,
        "args": args,
        "tier": tier,
        "row_limit": row_limit,
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return digest[:32]
