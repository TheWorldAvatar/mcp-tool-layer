"""Welded LLM snapshots and sampling. Callers cannot override these knobs.

Generation always uses the dated GPT-5 snapshot. Chemistry extraction uses
the dated GPT-4.1 snapshot. Seed is always 42. Temperature 0 is sent only
when the API accepts a non-default temperature; GPT-5 omits it.
"""

from __future__ import annotations

from typing import Any

LOCKED_LLM_SEED = 42
LOCKED_GENERATION_MODEL = "gpt-5-2025-08-07"
LOCKED_EXTRACTION_MODEL = "gpt-4.1-2025-04-14"
LOCKED_TEMPERATURE = 0.0
LOCKED_N = 1

_UNVERSIONED_MODEL_PINS = {
    "gpt-5": LOCKED_GENERATION_MODEL,
    "gpt-4.1": LOCKED_EXTRACTION_MODEL,
}
_NO_TEMPERATURE_PREFIXES = ("gpt-5", "gpt-5-mini", "gpt-4.1-mini")


def model_leaf(model_name: str) -> str:
    return str(model_name or "").split("/")[-1].strip()


def canonicalize_chat_model(model_name: str) -> str:
    """Map unversioned gpt-5 / gpt-4.1 aliases to the welded dated snapshots."""
    raw = str(model_name or "").strip()
    prefix, separator, leaf = raw.rpartition("/")
    pinned = _UNVERSIONED_MODEL_PINS.get(leaf or raw)
    if not pinned:
        return raw
    return f"{prefix}/{pinned}" if separator else pinned


def model_omits_temperature(model_name: str) -> bool:
    """True when the provider rejects a non-default temperature."""
    name = str(model_name or "")
    if "kimi-k3" in name.lower():
        return True
    leaf = model_leaf(name)
    return any(leaf.startswith(prefix) for prefix in _NO_TEMPERATURE_PREFIXES)


def apply_locked_sampling(
    cfg_kwargs: dict[str, Any] | None,
    model_name: str,
) -> dict[str, Any]:
    """Overwrite seed, n, streaming, and temperature. Env cannot change them."""
    updated = dict(cfg_kwargs or {})
    if model_omits_temperature(model_name):
        updated.pop("temperature", None)
    else:
        updated["temperature"] = LOCKED_TEMPERATURE
    updated.pop("top_p", None)
    updated["n"] = LOCKED_N
    updated["seed"] = LOCKED_LLM_SEED
    updated.pop("stream", None)
    updated["streaming"] = False
    return updated
