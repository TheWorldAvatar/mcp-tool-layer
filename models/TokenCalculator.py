# token_counter.py
# Standalone cost + token aggregator for LLM calls (OpenAI-style metadata).
from __future__ import annotations

import re
from typing import Optional, Callable, Any, Dict, List

try:
    from langchain.callbacks.base import BaseCallbackHandler  # type: ignore
except Exception:
    class BaseCallbackHandler:  # minimal stub for standalone use
        pass


class TokenCounter(BaseCallbackHandler):
    """
    Aggregates tokens across all LLM calls, records per-call details,
    and estimates USD cost using published per-1M token prices.

    Sources inspected:
      - response.llm_output["token_usage"], ["model_name"] or ["model"]
      - generation.message.response_metadata["token_usage"] / ["model_name"]
      - generation.generation_info["token_usage"] / ["model_name"]

    Notes:
      - If cached input tokens are present (e.g., prompt_tokens_details.cached_tokens),
        they are billed at the cached-input rate.
      - Model name normalization strips provider prefixes, date suffices, and maps families.
      - Prices are USD per 1 token (converted from USD per 1M).
      - Update PRICING as needed.
    """

    # USD per 1 token = USD_per_1M / 1e6
    PRICING: Dict[str, Dict[str, float]] = {
        # flagship (keep if you use them)
        "gpt-5":        {"in": 1.25e-6, "in_cached": 0.125e-6, "out": 10.0e-6},
        "gpt-5-mini":   {"in": 0.25e-6, "in_cached": 0.025e-6, "out": 2.0e-6},
        # gpt-4.1 family
        "gpt-4.1":      {"in": 2.00e-6, "in_cached": 0.50e-6,  "out": 8.0e-6},
        "gpt-4.1-mini": {"in": 0.40e-6, "in_cached": 0.10e-6,  "out": 1.6e-6},
        # gpt-4o family
        "gpt-4o":       {"in": 2.50e-6, "in_cached": 2.50e-6,  "out": 10.0e-6},  # no special cached rate
        "gpt-4o-mini":  {"in": 0.60e-6, "in_cached": 0.30e-6,  "out": 2.40e-6},
    }

    # Loose aliases → canonical pricing keys
    ALIASES = {
        "chatgpt-4o": "gpt-4o",
        "chatgpt-4o-latest": "gpt-4o",
    }

    def __init__(self, log_fn: Optional[Callable[[str], None]] = None) -> None:
        # token aggregates
        self.prompt_tokens = 0
        self.cached_prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.calls = 0

        # per-call detail
        self.calls_detail: List[Dict[str, Any]] = []

        # cost aggregates (USD)
        self.input_cost_usd = 0.0
        self.output_cost_usd = 0.0

        self._log = log_fn or (lambda s: None)

    # ----------------------------- helpers -----------------------------

    @staticmethod
    def _int(x: Any) -> int:
        try:
            return int(x or 0)
        except Exception:
            return 0

    @classmethod
    def _normalize_model(cls, name: str) -> str:
        """
        Normalize provider/model variants into pricing keys.
        Examples:
          "openai/gpt-4.1-mini-2025-04-14" → "gpt-4.1-mini"
          "openrouter/openai/gpt-4o"       → "gpt-4o"
        """
        if not name:
            return ""

        n = name.strip().lower()

        # strip provider prefixes
        if "/" in n:
            n = n.split("/")[-1]

        # strip colon suffixes (e.g., ":beta")
        if ":" in n:
            n = n.split(":")[0]

        # drop date suffixes like "-2025-04-14"
        n = re.sub(r"-20\d{2}-\d{2}-\d{2}$", "", n)

        # alias table first
        if n in cls.ALIASES:
            n = cls.ALIASES[n]

        # exact keys
        if n in cls.PRICING:
            return n

        # family rules
        if n.startswith("gpt-4.1-mini"):
            return "gpt-4.1-mini"
        if n.startswith("gpt-4.1"):
            return "gpt-4.1"
        if n.startswith("gpt-4o-mini"):
            return "gpt-4o-mini"
        if n.startswith("gpt-4o"):
            return "gpt-4o"
        if n.startswith("gpt-5-mini"):
            return "gpt-5-mini"
        if n.startswith("gpt-5"):
            return "gpt-5"

        return n  # unknown → zero pricing

    @classmethod
    def _prices(cls, model: str) -> Dict[str, float]:
        m = cls._normalize_model(model)
        return cls.PRICING.get(m, {"in": 0.0, "in_cached": 0.0, "out": 0.0})

    @staticmethod
    def _extract_usage(usage: Dict[str, Any]) -> Dict[str, int]:
        """
        Normalize usage dicts from various providers/wrappers.
        Returns dict with keys: prompt_tokens, cached_prompt_tokens, completion_tokens, total_tokens.
        """
        pt = (
            usage.get("prompt_tokens")
            or usage.get("input_tokens")
            or (usage.get("usage") or {}).get("input_tokens")
        )
        ct = (
            usage.get("completion_tokens")
            or usage.get("output_tokens")
            or (usage.get("usage") or {}).get("output_tokens")
        )
        # cached tokens fields seen in the wild
        cpt = (
            usage.get("cached_prompt_tokens")
            or usage.get("cache_read_input_tokens")
            or ((usage.get("prompt_tokens_details") or {}).get("cached_tokens"))
        )
        tt = usage.get("total_tokens") or (pt or 0) + (ct or 0)

        return {
            "prompt_tokens": int(pt or 0),
            "cached_prompt_tokens": int(cpt or 0),
            "completion_tokens": int(ct or 0),
            "total_tokens": int(tt or 0),
        }

    # ------------------------------ core -------------------------------

    def _record_call(self, usage: Dict[str, Any], meta: Dict[str, Any]) -> None:
        u = self._extract_usage(usage)

        p = self._int(u.get("prompt_tokens"))
        cp = self._int(u.get("cached_prompt_tokens"))
        c = self._int(u.get("completion_tokens"))
        t = self._int(u.get("total_tokens")) or p + c

        model_raw = (
            meta.get("model_name")
            or usage.get("model_name")
            or meta.get("model")
            or usage.get("model")
            or ""
        )

        model_key = self._normalize_model(model_raw)
        prices = self._prices(model_raw)

        billable_cached = min(cp, p)
        billable_warm = max(0, p - billable_cached)

        input_cost = billable_warm * prices["in"] + billable_cached * prices["in_cached"]
        output_cost = c * prices["out"]
        total_cost = input_cost + output_cost

        # aggregates
        self.calls += 1
        self.prompt_tokens += p
        self.cached_prompt_tokens += billable_cached
        self.completion_tokens += c
        self.total_tokens += t
        self.input_cost_usd += input_cost
        self.output_cost_usd += output_cost

        self.calls_detail.append({
            "call_index": self.calls,
            "model_name": model_raw or "",
            "model_pricing_key": model_key if model_key in self.PRICING else "unknown",
            "prompt_tokens": p,
            "cached_prompt_tokens": billable_cached,
            "completion_tokens": c,
            "total_tokens": t,
            "input_cost_usd": round(input_cost, 8),
            "output_cost_usd": round(output_cost, 8),
            "total_cost_usd": round(total_cost, 8),
        })

        self._log(
            f"[LLM call {self.calls}] model={model_raw or '?'} "
            f"prompt={p} (cached={billable_cached}), completion={c}, total={t}, "
            f"cost=${total_cost:.6f}"
        )

    # ----------------------------- hooks -------------------------------

    def on_llm_end(self, response, **kwargs) -> None:
        # Primary: provider-reported usage
        try:
            llm_out = getattr(response, "llm_output", None) or {}
            usage = llm_out.get("token_usage", {}) or {}
            meta = {
                "model_name": llm_out.get("model_name") or llm_out.get("model")
            }
            if any(k in usage for k in ("prompt_tokens", "completion_tokens", "total_tokens", "input_tokens", "output_tokens")):
                self._record_call(usage, meta)
        except Exception:
            pass

        # Fallback: inspect generations for per-call metadata
        try:
            gens = getattr(response, "generations", []) or []
            for gen_list in gens:
                for gen in gen_list:
                    gmeta = {}
                    if hasattr(gen, "message") and hasattr(gen.message, "response_metadata"):
                        gmeta = gen.message.response_metadata or {}
                    gmeta = {**(getattr(gen, "generation_info", {}) or {}), **gmeta}
                    usage2 = gmeta.get("token_usage", {}) or {}
                    meta2 = {"model_name": gmeta.get("model_name") or gmeta.get("model")}
                    if any(k in usage2 for k in ("prompt_tokens", "completion_tokens", "total_tokens", "input_tokens", "output_tokens")):
                        self._record_call(usage2, meta2)
        except Exception:
            pass

    # ----------------------------- output ------------------------------

    def aggregated_usage(self) -> Dict[str, Any]:
        total_cost = self.input_cost_usd + self.output_cost_usd
        return {
            "totals": {
                "prompt_tokens": self.prompt_tokens,
                "cached_prompt_tokens": self.cached_prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "total_tokens": self.total_tokens,
                "input_cost_usd": round(self.input_cost_usd, 6),
                "output_cost_usd": round(self.output_cost_usd, 6),
                "total_cost_usd": round(total_cost, 6),
                "calls": self.calls,
            },
            "aggregated_total_cost_usd": round(total_cost, 6),
            "calls_detail": self.calls_detail,
            "pricing_version": "2025-10-01",
        }
