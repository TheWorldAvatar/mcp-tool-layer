from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from evaluation.utils.chemical_name_aliases import (
    canonical_chemical_name,
    canonical_field_value,
    chemical_alias_policy_lines,
)
from src.agents.scripts_and_prompts_generation.level1_code_repair import invoke_json


SCHEMA_VERSION = "step-field-equivalence-judge.v1"
PROMPT_POLICY_VERSION = "strict-field-equivalence-v4"
BATCH_SCHEMA_VERSION = "step-field-equivalence-judge.batch.v1"
SUPPORTED_KINDS = {"chemical_name", "quantity", "qualitative", "device", "atmosphere"}
MISSING_MARKERS = {"", "n/a", "na", "none", "null", "missing", "unknown", "-1"}
_NUMBER_RE = re.compile(r"(?<![\w.])[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[-+]?\d+)?", re.I)
_NUMBER_WORD_VALUES = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}
_NUMBER_WORD_TOKENS = set(_NUMBER_WORD_VALUES) | {"and", "hundred", "thousand"}
_NUMBER_WORD_RE = re.compile(
    r"\b(?:"
    + "|".join(sorted(_NUMBER_WORD_TOKENS, key=len, reverse=True))
    + r")(?:[\s-]+(?:"
    + "|".join(sorted(_NUMBER_WORD_TOKENS, key=len, reverse=True))
    + r"))*\b",
    re.I,
)
_DECIMAL_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[-+]?\d+)?"

_DURATION_UNITS = {
    "s": ("duration", Decimal("1")),
    "sec": ("duration", Decimal("1")),
    "second": ("duration", Decimal("1")),
    "seconds": ("duration", Decimal("1")),
    "min": ("duration", Decimal("60")),
    "minute": ("duration", Decimal("60")),
    "minutes": ("duration", Decimal("60")),
    "h": ("duration", Decimal("3600")),
    "hr": ("duration", Decimal("3600")),
    "hour": ("duration", Decimal("3600")),
    "hours": ("duration", Decimal("3600")),
    "d": ("duration", Decimal("86400")),
    "day": ("duration", Decimal("86400")),
    "days": ("duration", Decimal("86400")),
    "wk": ("duration", Decimal("604800")),
    "wks": ("duration", Decimal("604800")),
    "week": ("duration", Decimal("604800")),
    "weeks": ("duration", Decimal("604800")),
    # Calendar months are intentionally not reduced to a fixed number of days.
    "month": ("calendar_month", Decimal("1")),
    "months": ("calendar_month", Decimal("1")),
}
_SI_UNITS = {
    "l": ("volume_litre", Decimal("1")),
    "liter": ("volume_litre", Decimal("1")),
    "liters": ("volume_litre", Decimal("1")),
    "litre": ("volume_litre", Decimal("1")),
    "litres": ("volume_litre", Decimal("1")),
    "ml": ("volume_litre", Decimal("0.001")),
    "milliliter": ("volume_litre", Decimal("0.001")),
    "milliliters": ("volume_litre", Decimal("0.001")),
    "millilitre": ("volume_litre", Decimal("0.001")),
    "millilitres": ("volume_litre", Decimal("0.001")),
    "ul": ("volume_litre", Decimal("0.000001")),
    "mul": ("volume_litre", Decimal("0.000001")),
    "microliter": ("volume_litre", Decimal("0.000001")),
    "microliters": ("volume_litre", Decimal("0.000001")),
    "microlitre": ("volume_litre", Decimal("0.000001")),
    "microlitres": ("volume_litre", Decimal("0.000001")),
    "nl": ("volume_litre", Decimal("0.000000001")),
    "kg": ("mass_gram", Decimal("1000")),
    "kilogram": ("mass_gram", Decimal("1000")),
    "kilograms": ("mass_gram", Decimal("1000")),
    "g": ("mass_gram", Decimal("1")),
    "gram": ("mass_gram", Decimal("1")),
    "grams": ("mass_gram", Decimal("1")),
    "mg": ("mass_gram", Decimal("0.001")),
    "milligram": ("mass_gram", Decimal("0.001")),
    "milligrams": ("mass_gram", Decimal("0.001")),
    "ug": ("mass_gram", Decimal("0.000001")),
    "microgram": ("mass_gram", Decimal("0.000001")),
    "micrograms": ("mass_gram", Decimal("0.000001")),
    "mol": ("amount_mole", Decimal("1")),
    "mole": ("amount_mole", Decimal("1")),
    "moles": ("amount_mole", Decimal("1")),
    "mmol": ("amount_mole", Decimal("0.001")),
    "millimole": ("amount_mole", Decimal("0.001")),
    "millimoles": ("amount_mole", Decimal("0.001")),
    "umol": ("amount_mole", Decimal("0.000001")),
    "micromole": ("amount_mole", Decimal("0.000001")),
    "micromoles": ("amount_mole", Decimal("0.000001")),
    "nmol": ("amount_mole", Decimal("0.000000001")),
    "nanomole": ("amount_mole", Decimal("0.000000001")),
    "nanomoles": ("amount_mole", Decimal("0.000000001")),
}
_ALL_LINEAR_UNITS = {**_DURATION_UNITS, **_SI_UNITS}
_TEMPERATURE_UNITS = {
    "c": "c",
    "degc": "c",
    "degree c": "c",
    "degree celsius": "c",
    "degrees celsius": "c",
    "celsius": "c",
    "k": "k",
    "kelvin": "k",
    "kelvins": "k",
    "f": "f",
    "degf": "f",
    "degree f": "f",
    "degree fahrenheit": "f",
    "degrees fahrenheit": "f",
    "fahrenheit": "f",
}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class StepEquivalenceConfig:
    enabled: bool = False
    model: str = "gpt-4o"
    cache_dir: Path = Path("evaluation/cache/step_equivalence_judge")
    required: bool = False
    timeout_seconds: int = 300
    # Default parallelism tuned for full-GT / multi-paper scoring.
    batch_size: int = 40
    max_workers: int = 16
    fast_match_enabled: bool = False
    fast_match_model: str = "gpt-4o"
    fast_match_cache_dir: Path = Path("evaluation/cache/fast_field_match_judge")
    fast_match_required: bool = False
    fast_match_batch_size: int = 20
    fast_match_max_workers: int = 8
    product_match_enabled: bool = True
    product_match_model: str = "gpt-4o"


@dataclass(frozen=True)
class StepEquivalenceJudgement:
    equivalent: bool
    confidence: float
    relation: str
    reason: str
    source: str
    status: str


def _normalize_text(value: str, kind: str) -> str:
    text = unicodedata.normalize("NFKC", value).casefold().strip()
    text = (
        text.replace("\u2018", "'")
        .replace("\u2019", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2212", "-")
        .replace("\u00b0", " degree ")
        .replace("\u00b5", "u")
        .replace("\u03bc", "u")
    )
    text = re.sub(r"\s+", " ", text)
    if kind == "chemical_name":
        text = text.replace("·", "-").replace("•", "-").replace("⋅", "-")
        text = re.sub(r"\s*([,;:()\[\]{}+])\s*", r"\1", text)
        text = re.sub(r"\s*[-\u2013\u2014]\s*", "-", text)
        text = text.replace("''", '"').replace('""', '"')
        # Treat N,N'-DMF/DMA typography as N,N- (common OCR / GT inconsistency).
        text = re.sub(r"\bn,\s*n'\s*-", "n,n-", text)
        text = re.sub(r"\bn,\s*n\s*-", "n,n-", text)
        text = re.sub(r"\s*\((?:dmf|dma|meoh|h2o|tea|et2o|h2bdc|h2btb)\)\s*$", "", text).strip()
        text = canonical_chemical_name(text)
    elif kind == "atmosphere":
        text = re.sub(r"\s*[-\u2013\u2014]\s*", "-", text)
        text = re.sub(r"[.]+$", "", text)
        text = canonical_field_value("atmosphere", text)
    elif kind == "quantity":
        # ASCII "muL" / "mug" is the same micro-prefix as Unicode µ already mapped to "u".
        text = re.sub(r"\bmu\s*l\b", "ul", text)
        text = re.sub(r"\bmul\b", "ul", text)
        text = re.sub(r"\bmu\s*g\b", "ug", text)
        text = re.sub(r"\bmug\b", "ug", text)
        text = re.sub(r"\bmu\s*mol\b", "umol", text)
        text = re.sub(r"\bmumol\b", "umol", text)
        text = re.sub(r"\s*([,;/])\s*", r"\1", text)
        text = re.sub(r"\bdegrees?\b", "degree", text)
        text = re.sub(r"(?<!degree )\bcelsius\b", "degree celsius", text)
        text = re.sub(r"\bhrs?\b|\bhours?\b", "hour", text)
        text = re.sub(r"\bmins?\b|\bminutes?\b", "minute", text)
        text = re.sub(r"\bsecs?\b|\bseconds?\b", "second", text)
        text = re.sub(r"\s+", " ", text)
    else:
        text = re.sub(r"\s*[-\u2013\u2014]\s*", "-", text)
        text = re.sub(r"[.]+$", "", text)
    return text.strip()


def _parse_number_words(value: str) -> Decimal | None:
    tokens = value.casefold().replace("-", " ").split()
    if not tokens or any(token not in _NUMBER_WORD_TOKENS for token in tokens):
        return None
    if not any(token in _NUMBER_WORD_VALUES for token in tokens):
        return None
    total = 0
    current = 0
    for token in tokens:
        if token == "and":
            continue
        if token in _NUMBER_WORD_VALUES:
            current += _NUMBER_WORD_VALUES[token]
        elif token == "hundred":
            current = max(current, 1) * 100
        elif token == "thousand":
            total += max(current, 1) * 1000
            current = 0
    return Decimal(total + current)


def _replace_number_words(value: str) -> str:
    def _replace(match: re.Match[str]) -> str:
        parsed = _parse_number_words(match.group(0))
        return str(parsed) if parsed is not None else match.group(0)

    return _NUMBER_WORD_RE.sub(_replace, value)


def _decimal(value: str) -> Decimal | None:
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def _canonical_decimal(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _temperature_in_celsius(value: Decimal, unit: str) -> Decimal:
    if unit == "c":
        return value
    if unit == "k":
        return value - Decimal("273.15")
    with localcontext() as context:
        context.prec = 50
        return (value - Decimal("32")) * Decimal("5") / Decimal("9")


def _quantity_components(value: str) -> tuple[tuple[str, str], ...] | None:
    """Safely parse a complete quantity expression into canonical components."""
    text = _replace_number_words(_normalize_text(value, "quantity"))
    temperature_units = "|".join(
        re.escape(unit) for unit in sorted(_TEMPERATURE_UNITS, key=len, reverse=True)
    )
    duration_units = "|".join(
        re.escape(unit) for unit in sorted(_DURATION_UNITS, key=len, reverse=True)
    )

    temperature_match = re.fullmatch(
        rf"\s*({_DECIMAL_NUMBER})\s*({temperature_units})\s*",
        text,
    )
    if temperature_match:
        magnitude = _decimal(temperature_match.group(1))
        if magnitude is None:
            return None
        unit = _TEMPERATURE_UNITS[temperature_match.group(2)]
        canonical = _temperature_in_celsius(magnitude, unit)
        return (("temperature_celsius", _canonical_decimal(canonical)),)

    rate_match = re.fullmatch(
        rf"\s*({_DECIMAL_NUMBER})\s*({temperature_units})\s*(?:/|per)\s*({duration_units})\s*",
        text,
    )
    if rate_match:
        magnitude = _decimal(rate_match.group(1))
        duration_dimension, duration_factor = _DURATION_UNITS[rate_match.group(3)]
        if magnitude is None or duration_dimension != "duration":
            return None
        temperature_unit = _TEMPERATURE_UNITS[rate_match.group(2)]
        with localcontext() as context:
            context.prec = 50
            temperature_delta = (
                magnitude * Decimal("5") / Decimal("9")
                if temperature_unit == "f"
                else magnitude
            )
            canonical = temperature_delta / duration_factor
        return (("temperature_rate_celsius_per_second", _canonical_decimal(canonical)),)

    linear_units = "|".join(
        re.escape(unit) for unit in sorted(_ALL_LINEAR_UNITS, key=len, reverse=True)
    )
    quantity_re = re.compile(
        rf"({_DECIMAL_NUMBER})\s*({linear_units})(?=$|[\s,;()])"
    )
    matches = list(quantity_re.finditer(text))
    if not matches:
        return None

    remainder_parts = []
    position = 0
    for match in matches:
        remainder_parts.append(text[position : match.start()])
        position = match.end()
    remainder_parts.append(text[position:])
    remainder = " ".join(remainder_parts)
    remainder = re.sub(r"[\s,;()]+", "", remainder)
    remainder = remainder.replace("and", "")
    if remainder:
        return None

    totals: dict[str, Decimal] = {}
    for match in matches:
        magnitude = _decimal(match.group(1))
        if magnitude is None:
            return None
        dimension, factor = _ALL_LINEAR_UNITS[match.group(2)]
        # Repeated values of one dimension may denote alternatives, repetitions, or
        # separate additions rather than an arithmetic total. Leave those expressions
        # to the strict fallback instead of manufacturing equivalence by summation.
        if dimension in totals:
            return None
        totals[dimension] = magnitude * factor
    return tuple(
        sorted(
            (dimension, _canonical_decimal(magnitude))
            for dimension, magnitude in totals.items()
        )
    )


def _quantity_tokens(value: str) -> list[tuple[str, str | None]]:
    """Split a quantity string into (canonical_number, unit_or_None) tokens."""
    text = _replace_number_words(_normalize_text(value, "quantity"))
    units = "|".join(
        re.escape(unit) for unit in sorted(_ALL_LINEAR_UNITS, key=len, reverse=True)
    )
    token_re = re.compile(
        rf"({_DECIMAL_NUMBER})(?:\s*({units})(?=$|[\s,;()/]))?"
    )
    tokens: list[tuple[str, str | None]] = []
    for match in token_re.finditer(text):
        magnitude = _decimal(match.group(1))
        if magnitude is None:
            continue
        tokens.append((_canonical_decimal(magnitude), match.group(2)))
    return tokens


def _fill_bare_number_units(left: str, right: str) -> tuple[str, str] | None:
    """Copy a peer unit onto a bare number when the same magnitude is fully unit-tagged.

    Example: "0.023, 0.108 mmol" + "0.023 g, 0.108 mmol" -> both become mass+amount.
    Returns None when no unit was inherited, so temperature/rate strings stay intact.
    """
    left_tokens = _quantity_tokens(left)
    right_tokens = _quantity_tokens(right)
    inherited = False

    def _fill(source: list[tuple[str, str | None]], peer: list[tuple[str, str | None]]) -> str:
        nonlocal inherited
        peer_units: dict[str, set[str]] = {}
        for number, unit in peer:
            if unit:
                peer_units.setdefault(number, set()).add(unit)
        used_dims = {
            _ALL_LINEAR_UNITS[unit][0]
            for _, unit in source
            if unit and unit in _ALL_LINEAR_UNITS
        }
        filled: list[str] = []
        for number, unit in source:
            if unit is None:
                candidates = peer_units.get(number, set())
                if len(candidates) == 1:
                    candidate = next(iter(candidates))
                    dimension = _ALL_LINEAR_UNITS.get(candidate, (None,))[0]
                    if dimension and dimension not in used_dims:
                        unit = candidate
                        used_dims.add(dimension)
                        inherited = True
            filled.append(f"{number} {unit}" if unit else number)
        return ", ".join(filled)

    filled_left = _fill(left_tokens, right_tokens)
    filled_right = _fill(right_tokens, left_tokens)
    if not inherited:
        return None
    return filled_left, filled_right


def _quantity_decision(
    ground_truth_value: str,
    prediction_value: str,
) -> bool | None:
    """Return deterministic result, or None when strict LLM fallback is safe."""
    filled = _fill_bare_number_units(ground_truth_value, prediction_value)
    if filled is not None:
        gt_components = _quantity_components(filled[0])
        pred_components = _quantity_components(filled[1])
        if gt_components is not None and pred_components is not None:
            return gt_components == pred_components
    gt_components = _quantity_components(ground_truth_value)
    pred_components = _quantity_components(prediction_value)
    if gt_components is not None and pred_components is not None:
        return gt_components == pred_components

    gt_with_words = _replace_number_words(
        _normalize_text(ground_truth_value, "quantity")
    )
    pred_with_words = _replace_number_words(
        _normalize_text(prediction_value, "quantity")
    )
    gt_numbers = _numbers(gt_with_words)
    pred_numbers = _numbers(pred_with_words)
    if gt_numbers != pred_numbers and (gt_numbers or pred_numbers):
        return False
    return None


def _numbers(value: str) -> tuple[str, ...]:
    numbers = []
    for match in _NUMBER_RE.findall(value):
        try:
            number = float(match)
            numbers.append(format(number, ".15g"))
        except ValueError:
            numbers.append(match.casefold())
    return tuple(numbers)


def _is_missing(value: Any) -> bool:
    return value is None or (
        isinstance(value, str) and value.strip().casefold() in MISSING_MARKERS
    )


def _cache_key(
    config: StepEquivalenceConfig,
    field_kind: str,
    field_name: str,
    ground_truth_value: str,
    prediction_value: str,
) -> str:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "policy_version": PROMPT_POLICY_VERSION,
        "model": config.model,
        "field_kind": field_kind,
        "field_name": field_name,
        "ground_truth_value": ground_truth_value,
        "prediction_value": prediction_value,
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


_POLICY = [
    "Return equivalent=true only if both values mean exactly the same thing in this synthesis-step field.",
    "Chemical aliases must denote exactly the same species; different hydrate, stoichiometry, charge, counterion, stereochemistry, or composition is not equivalent.",
    *chemical_alias_policy_lines(),
    "Quantities must denote exactly the same magnitude and dimension; qualitative quantities such as room temperature or overnight may match exact paraphrases only.",
    "Device, atmosphere, and qualitative strings may match only when their field-specific meanings are interchangeable, not merely related.",
    "Missing values and placeholders, booleans, step types, and values containing different numbers are never eligible for equivalence.",
    "If exact equivalence cannot be established from the two values alone, return equivalent=false.",
]


def _prompt(
    field_kind: str,
    field_name: str,
    ground_truth_value: str,
    prediction_value: str,
) -> str:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "task": "strict_step_field_equivalence",
        "field_kind": field_kind,
        "field_name": field_name,
        "ground_truth_value": ground_truth_value,
        "prediction_value": prediction_value,
        "policy": _POLICY,
    }
    return (
        "Return only one JSON object with exactly these keys: schema_version, "
        "equivalent, confidence, relation, reason. relation must be exactly one "
        "of equivalent, different, uncertain. Set equivalent=true only with "
        "confidence >= 0.9; otherwise fail closed with equivalent=false. Do not "
        "use document-specific context.\n\n"
        + json.dumps(payload, ensure_ascii=False, sort_keys=True)
    )


def _prompt_batch(pairs: list[dict[str, str]]) -> str:
    payload = {
        "schema_version": BATCH_SCHEMA_VERSION,
        "task": "strict_step_field_equivalence_batch",
        "policy": _POLICY,
        "pairs": pairs,
    }
    return (
        "Return only one JSON object with exactly keys schema_version and judgements. "
        f"The top-level schema_version must be exactly {BATCH_SCHEMA_VERSION}. "
        "Each judgement must have exactly pair_id, schema_version, equivalent, "
        f"confidence, relation, reason; each judgement schema_version must be exactly "
        f"{SCHEMA_VERSION}. relation must be exactly one of equivalent, "
        "different, uncertain. Set equivalent=true only with confidence >= 0.9; "
        "otherwise fail closed with equivalent=false. Do not use document-specific "
        "context.\n\n"
        + json.dumps(payload, ensure_ascii=False, sort_keys=True)
    )


def _validate(data: dict[str, Any]) -> dict[str, Any]:
    required = {"schema_version", "equivalent", "confidence", "relation", "reason"}
    if set(data) != required or data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("step equivalence judgement schema mismatch")
    if not isinstance(data.get("equivalent"), bool):
        raise ValueError("step equivalence must be boolean")
    confidence = data.get("confidence")
    if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        raise ValueError("step equivalence confidence must be between zero and one")
    if data.get("relation") not in {"equivalent", "different", "uncertain"}:
        raise ValueError("invalid step equivalence relation")
    if not str(data.get("reason") or "").strip():
        raise ValueError("step equivalence reason is required")
    if data["equivalent"] and (
        confidence < 0.9 or data["relation"] != "equivalent"
    ):
        raise ValueError("positive step equivalence is not sufficiently certain")
    return data


def _validate_batch_payload(
    data: dict[str, Any],
    pairs: list[dict[str, str]],
) -> dict[str, dict[str, Any]]:
    if set(data) != {"schema_version", "judgements"}:
        raise ValueError("step batch judgement has unexpected top-level keys")
    if data.get("schema_version") != BATCH_SCHEMA_VERSION:
        raise ValueError("step batch judgement schema version mismatch")
    rows = data.get("judgements")
    if not isinstance(rows, list):
        raise ValueError("step batch judgements must be a list")
    expected_ids = {pair["pair_id"] for pair in pairs}
    validated: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("step batch judgement row must be an object")
        pair_id = str(row.get("pair_id") or "")
        if pair_id not in expected_ids or pair_id in validated:
            raise ValueError("step batch judgement pair ids do not match request")
        body = {k: v for k, v in row.items() if k != "pair_id"}
        # Allow either SCHEMA_VERSION or omitting and injecting.
        if "schema_version" not in body:
            body = {**body, "schema_version": SCHEMA_VERSION}
        validated[pair_id] = _validate(body)
    if set(validated) != expected_ids:
        raise ValueError("step batch judgement omitted requested pairs")
    return validated


class StepEquivalenceJudge:
    """Strict deterministic-first field equivalence with persistent LLM fallback."""

    def __init__(self, config: StepEquivalenceConfig = StepEquivalenceConfig()):
        from evaluation.utils.fast_field_match_judge import (
            FastFieldMatchConfig,
            FastFieldMatchJudge,
        )
        from evaluation.utils.product_identity_judge import (
            ProductIdentityConfig,
            ProductIdentityJudge,
        )

        self.config = config
        self._memory: dict[tuple[str, str, str, str], StepEquivalenceJudgement] = {}
        self._fast_match = FastFieldMatchJudge(
            FastFieldMatchConfig(
                enabled=config.fast_match_enabled,
                model=config.fast_match_model,
                cache_dir=config.fast_match_cache_dir,
                required=config.fast_match_required,
                batch_size=config.fast_match_batch_size,
                max_workers=config.fast_match_max_workers,
            )
        )
        self._product_match = ProductIdentityJudge(
            ProductIdentityConfig(
                enabled=config.product_match_enabled,
                model=config.product_match_model,
                cache_dir=Path("evaluation/cache/product_identity_judge"),
                required=config.fast_match_required,
                batch_size=config.fast_match_batch_size,
                max_workers=config.fast_match_max_workers,
            )
        )

    def equivalent(
        self,
        field_kind: str,
        field_name: str,
        ground_truth_value: Any,
        prediction_value: Any,
    ) -> bool:
        if field_kind not in SUPPORTED_KINDS:
            return ground_truth_value == prediction_value

        # Booleans and numeric JSON values are deliberately never sent to the LLM.
        if isinstance(ground_truth_value, bool) or isinstance(prediction_value, bool):
            return (
                isinstance(ground_truth_value, bool)
                and isinstance(prediction_value, bool)
                and ground_truth_value == prediction_value
            )
        if isinstance(ground_truth_value, (int, float)) or isinstance(
            prediction_value, (int, float)
        ):
            return (
                type(ground_truth_value) is type(prediction_value)
                and ground_truth_value == prediction_value
            )
        if not isinstance(ground_truth_value, str) or not isinstance(
            prediction_value, str
        ):
            return ground_truth_value == prediction_value

        gt_normalized = _normalize_text(ground_truth_value, field_kind)
        pred_normalized = _normalize_text(prediction_value, field_kind)
        if gt_normalized == pred_normalized:
            return True

        # Placeholder/missing mismatches and differing numeric content fail closed.
        if _is_missing(ground_truth_value) or _is_missing(prediction_value):
            return False
        if field_kind == "quantity":
            quantity_decision = _quantity_decision(
                ground_truth_value, prediction_value
            )
            if quantity_decision is not None:
                return quantity_decision
        else:
            gt_numbers = _numbers(gt_normalized)
            pred_numbers = _numbers(pred_normalized)
            if gt_numbers != pred_numbers and (gt_numbers or pred_numbers):
                if field_kind == "chemical_name" and self.config.fast_match_enabled:
                    return self._fast_match.equivalent(
                        ground_truth_value, prediction_value
                    )
                return False
        if field_kind == "chemical_name" and self.config.fast_match_enabled:
            if self._fast_match.equivalent(ground_truth_value, prediction_value):
                return True
            if not self.config.enabled:
                return False
        if not self.config.enabled:
            return False

        memory_key = (
            field_kind,
            field_name,
            gt_normalized,
            pred_normalized,
        )
        cached_memory = self._memory.get(memory_key)
        if cached_memory is not None:
            return cached_memory.equivalent

        cache_path = self.config.cache_dir / (
            _cache_key(
                self.config,
                field_kind,
                field_name,
                gt_normalized,
                pred_normalized,
            )
            + ".json"
        )
        judgement = self._read_cache(cache_path)
        if judgement is None:
            judgement = self._invoke(
                cache_path,
                field_kind,
                field_name,
                gt_normalized,
                pred_normalized,
            )
        self._memory[memory_key] = judgement
        return judgement.equivalent and judgement.status == "ok"

    def same_product(
        self,
        ground_truth_names: Iterable[str],
        prediction_names: Iterable[str],
    ) -> bool:
        return self._product_match.equivalent(ground_truth_names, prediction_names)

    def cached_equivalent(
        self,
        field_kind: str,
        field_name: str,
        ground_truth_value: str,
        prediction_value: str,
    ) -> bool | None:
        """Return a cached decision without invoking any model."""
        gt = _normalize_text(ground_truth_value, field_kind)
        pred = _normalize_text(prediction_value, field_kind)
        memory_key = (field_kind, field_name, gt, pred)
        judgement = self._memory.get(memory_key)
        if judgement is None:
            cache_path = self.config.cache_dir / (
                _cache_key(self.config, field_kind, field_name, gt, pred) + ".json"
            )
            judgement = self._read_cache(cache_path)
            if judgement is not None:
                self._memory[memory_key] = judgement
        if judgement is None:
            return None
        return judgement.equivalent and judgement.status == "ok"

    def prefetch_products(
        self,
        pairs: Iterable[tuple[Iterable[str], Iterable[str]]],
    ) -> None:
        self._product_match.prefetch(pairs)

    def prefetch(
        self,
        pairs: Iterable[tuple[str, str, str, str]],
    ) -> int:
        """Warm cache for many (field_kind, field_name, gt, pred) pairs in parallel batches."""
        pending: list[dict[str, str]] = []
        pending_meta: dict[str, tuple[str, str, str, str, Path]] = {}
        seen: set[tuple[str, str, str, str]] = set()
        fast_pairs: list[tuple[str, str]] = []
        for index, (field_kind, field_name, ground_truth_value, prediction_value) in enumerate(
            pairs, start=1
        ):
            if field_kind not in SUPPORTED_KINDS:
                continue
            if not isinstance(ground_truth_value, str) or not isinstance(
                prediction_value, str
            ):
                continue
            gt_normalized = _normalize_text(ground_truth_value, field_kind)
            pred_normalized = _normalize_text(prediction_value, field_kind)
            if gt_normalized == pred_normalized:
                continue
            if _is_missing(ground_truth_value) or _is_missing(prediction_value):
                continue
            if field_kind != "quantity":
                gt_numbers = _numbers(gt_normalized)
                pred_numbers = _numbers(pred_normalized)
                if gt_numbers != pred_numbers and (gt_numbers or pred_numbers):
                    if field_kind == "chemical_name" and self.config.fast_match_enabled:
                        fast_pairs.append((ground_truth_value, prediction_value))
                    continue
            if field_kind == "chemical_name" and self.config.fast_match_enabled:
                fast_pairs.append((ground_truth_value, prediction_value))
            if not self.config.enabled:
                continue
            memory_key = (field_kind, field_name, gt_normalized, pred_normalized)
            if memory_key in seen or memory_key in self._memory:
                continue
            seen.add(memory_key)
            cache_path = self.config.cache_dir / (
                _cache_key(
                    self.config,
                    field_kind,
                    field_name,
                    gt_normalized,
                    pred_normalized,
                )
                + ".json"
            )
            cached = self._read_cache(cache_path)
            if cached is not None:
                self._memory[memory_key] = cached
                continue
            pair_id = f"s{index:04d}"
            pending.append(
                {
                    "pair_id": pair_id,
                    "field_kind": field_kind,
                    "field_name": field_name,
                    "ground_truth_value": gt_normalized,
                    "prediction_value": pred_normalized,
                }
            )
            pending_meta[pair_id] = (
                field_kind,
                field_name,
                gt_normalized,
                pred_normalized,
                cache_path,
            )

        if self.config.fast_match_enabled and fast_pairs:
            self._fast_match.prefetch(fast_pairs)
        if not pending:
            return 0

        batch_size = max(
            1, int(self.config.batch_size or _env_int("STEP_EQUIVALENCE_BATCH_SIZE", 40))
        )
        max_workers = max(
            1, int(self.config.max_workers or _env_int("STEP_EQUIVALENCE_MAX_WORKERS", 16))
        )
        chunks = [pending[i : i + batch_size] for i in range(0, len(pending), batch_size)]
        print(
            f"[step-equivalence-judge] prefetch pending={len(pending)} "
            f"chunks={len(chunks)} batch_size={batch_size} workers={max_workers}",
            flush=True,
        )

        def _run_chunk(chunk: list[dict[str, str]]) -> None:
            try:
                response = invoke_json(
                    self.config.model,
                    _prompt_batch(chunk),
                    timeout_seconds=self.config.timeout_seconds,
                    max_attempts=2,
                )
                validated = _validate_batch_payload(response.data, chunk)
                for pair_id, row in validated.items():
                    field_kind, field_name, gt, pred, cache_path = pending_meta[pair_id]
                    judgement = self._persist(
                        cache_path,
                        field_kind,
                        field_name,
                        gt,
                        pred,
                        row,
                    )
                    self._memory[(field_kind, field_name, gt, pred)] = judgement
            except Exception as exc:
                print(
                    "[step-equivalence-judge] batch failed; "
                    f"falling back to singles: {type(exc).__name__}: {exc}",
                    flush=True,
                )
                # Fall back to single-pair invokes for this chunk.
                for item in chunk:
                    field_kind, field_name, gt, pred, cache_path = pending_meta[
                        item["pair_id"]
                    ]
                    judgement = self._invoke(cache_path, field_kind, field_name, gt, pred)
                    self._memory[(field_kind, field_name, gt, pred)] = judgement

        workers = min(max_workers, len(chunks))
        if workers == 1:
            for chunk in chunks:
                _run_chunk(chunk)
        else:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = [executor.submit(_run_chunk, chunk) for chunk in chunks]
                for future in as_completed(futures):
                    future.result()
        return len(pending)

    def _read_cache(self, path: Path) -> StepEquivalenceJudgement | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            row = _validate(payload["judgement"])
        except (OSError, KeyError, TypeError, json.JSONDecodeError, ValueError):
            return None
        return StepEquivalenceJudgement(
            equivalent=bool(row["equivalent"]),
            confidence=float(row["confidence"]),
            relation=str(row["relation"]),
            reason=str(row["reason"]),
            source="cache",
            status="ok",
        )

    def _persist(
        self,
        cache_path: Path,
        field_kind: str,
        field_name: str,
        ground_truth_value: str,
        prediction_value: str,
        row: dict[str, Any],
    ) -> StepEquivalenceJudgement:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "policy_version": PROMPT_POLICY_VERSION,
            "model": self.config.model,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "field_kind": field_kind,
            "field_name": field_name,
            "ground_truth_value": ground_truth_value,
            "prediction_value": prediction_value,
            "judgement": row,
        }
        temp_path = cache_path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temp_path.replace(cache_path)
        return StepEquivalenceJudgement(
            equivalent=bool(row["equivalent"]),
            confidence=float(row["confidence"]),
            relation=str(row["relation"]),
            reason=str(row["reason"]),
            source="llm",
            status="ok",
        )

    def _invoke(
        self,
        cache_path: Path,
        field_kind: str,
        field_name: str,
        ground_truth_value: str,
        prediction_value: str,
    ) -> StepEquivalenceJudgement:
        try:
            response = invoke_json(
                self.config.model,
                _prompt(
                    field_kind,
                    field_name,
                    ground_truth_value,
                    prediction_value,
                ),
                timeout_seconds=self.config.timeout_seconds,
                max_attempts=2,
            )
            row = _validate(response.data)
            return self._persist(
                cache_path,
                field_kind,
                field_name,
                ground_truth_value,
                prediction_value,
                row,
            )
        except Exception as exc:
            if self.config.required:
                raise RuntimeError("step field equivalence judge failed") from exc
            return StepEquivalenceJudgement(
                equivalent=False,
                confidence=0.0,
                relation="uncertain",
                reason=f"judge failed closed: {type(exc).__name__}",
                source="error",
                status="error",
            )
