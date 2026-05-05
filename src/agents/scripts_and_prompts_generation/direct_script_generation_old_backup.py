#!/usr/bin/env python3
"""
direct_script_generation_old_backup.py

Deprecated backup shim.

The historical backup implementation contained extensive domain-specific prompt
examples and hardcoded ontology assumptions. Keeping that code in-tree would
violate the rule that domain knowledge must come only from the provided T-Box
TTL input.

This module is intentionally disabled. Use `direct_script_generation.py` for
the supported generation path.
"""

from typing import Any


_DEPRECATION_MESSAGE = (
    "direct_script_generation_old_backup.py is intentionally disabled. "
    "Use direct_script_generation.py instead."
)


def _disabled(*args: Any, **kwargs: Any) -> None:
    """Raise a deterministic error for deprecated backup code."""
    raise RuntimeError(_DEPRECATION_MESSAGE)
