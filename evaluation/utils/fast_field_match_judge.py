"""Fast gpt-4o rescue for chemical-name labels that embed a quantity.

The strict step-equivalence judge fails closed when the two strings contain
different numbers. That blocks the common extraction pattern ``DEF`` vs
``5 mL DEF``. This helper first strips quantity annotations deterministically,
then asks gpt-4o only for leftover same-species questions.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from evaluation.utils.chemical_name_aliases import chemical_alias_policy_lines
from src.agents.scripts_and_prompts_generation.level1_code_repair import invoke_json

SCHEMA_VERSION = "fast-field-match-judge.v1"
POLICY_VERSION = "qty-hydrate-punct-alias-v5"
BATCH_SCHEMA_VERSION = "fast-field-match-judge.batch.v1"
_QTY_UNIT = r"(?:mg|mmol|mol|mL|ml|µL|uL|L|g|drops?|drop)"
_PAREN_QTY = re.compile(rf"\((?=[^)]*\b{_QTY_UNIT}\b)[^)]*\)", re.I)
_LEAD_QTY = re.compile(rf"^\s*[-+]?\d+(?:\.\d+)?\s*{_QTY_UNIT}\b[\s,;:]*", re.I)
_TRAIL_QTY = re.compile(rf"[\s,;:]+[-+]?\d+(?:\.\d+)?(?:\s*,\s*[-+]?\d+(?:\.\d+)?)*\s*{_QTY_UNIT}\b\s*$", re.I)

_UNSPECIFIED_HYDRATE = re.compile(r"[·.•⋅.\-]*x+h2o\s*$", re.I)
_TRAILING_PAREN = re.compile(r"\(([^)]+)\)\s*$")
_HYDRATE_WORD_TRAIL = re.compile(
    r"(?:^|[\s,;:])((?:hemi|mono|di|tri|tetra|penta|hexa|hepta|octa|nona|deca)hydrate)\s*$",
    re.I,
)
_HYDRATE_WORD_COUNTS = {
    "monohydrate": "1",
    "dihydrate": "2",
    "trihydrate": "3",
    "tetrahydrate": "4",
    "pentahydrate": "5",
    "hexahydrate": "6",
    "heptahydrate": "7",
    "octahydrate": "8",
    "nonahydrate": "9",
    "decahydrate": "10",
}
_PAREN_FORMULA_HYDRATE = re.compile(
    r"\(([^()]+)\)\s*(" + "|".join(_HYDRATE_WORD_COUNTS) + r")\s*$",
    re.I,
)
_FORMULA_HYDRATE = re.compile(r"^(.+?)[·.•⋅.\-\ufffd]+(\d*)h2o$", re.I)
_POLICY = [
    "Decide only whether the two chemical-name strings denote the same species.",
    "equivalent=true if one string is the same species with an amount, volume, or parenthetical quantity attached, e.g. DEF vs '5 mL DEF' or H2TEI vs 'H2TEI (100 mg)'.",
    "equivalent=true if the only difference is unspecified hydrate notation, e.g. VOSO4xxH2O vs VOSO4-xH2O vs VOSO4·xH2O.",
    *chemical_alias_policy_lines(),
    "equivalent=false if the extra text changes identity: a different solvent, a mixture of two solvents, a different ligand, a definite hydrate count such as anhydrous vs ·2H2O, or unrelated names.",
    "Do not require the quantities themselves to match; this judge compares species identity only.",
]


@dataclass(frozen=True)
class FastFieldMatchConfig:
    enabled: bool = False
    model: str = "gpt-4o"
    cache_dir: Path = Path("evaluation/cache/fast_field_match_judge")
    required: bool = False
    timeout_seconds: int = 120
    batch_size: int = 20
    max_workers: int = 8


@dataclass(frozen=True)
class FastFieldMatchJudgement:
    equivalent: bool
    confidence: float
    relation: str
    reason: str
    source: str
    status: str


def strip_quantity_annotation(name: str) -> str:
    """Remove leading, trailing, or parenthetical quantity annotations."""
    text = _PAREN_QTY.sub(" ", name or "")
    text = _LEAD_QTY.sub("", text)
    text = _TRAIL_QTY.sub("", text)
    return re.sub(r"\s+", " ", text).strip(" .;,-")


def equate_hydrate_counts() -> bool:
    """Experimental: treat definite hydrate counts as the same salt."""
    return os.getenv("ONTOSYN_EQUATE_HYDRATE_COUNTS", "").strip().casefold() in {
        "1",
        "true",
        "yes",
    }


def strip_definite_hydrate_label(name: str) -> str:
    """Drop a trailing definite hydrate word or ·NH2O, leaving the salt core."""
    text = strip_quantity_annotation(name or "").strip()
    if not text:
        return ""
    word_stripped = _HYDRATE_WORD_TRAIL.sub("", text).strip(" ,;:")
    compact = re.sub(r"\s+", "", word_stripped)
    formula_match = _FORMULA_HYDRATE.fullmatch(compact)
    core = formula_match.group(1) if formula_match is not None else word_stripped
    if not core or core.casefold() in {"h2o", "water"}:
        return ""
    return core


def strip_unspecified_hydrate(name: str) -> str:
    """Remove trailing unspecified hydrate markers such as xxH2O or ·xH2O."""
    text = re.sub(r"\s+", "", name or "")
    stripped = _UNSPECIFIED_HYDRATE.sub("", text)
    return stripped if stripped != text else ""


def definite_hydrate_key(name: str) -> str:
    """Return a strict ``formula-Nh2o`` key for a definite hydrate.

    This rescues equivalent aliases such as ``CuCl2�2H2O`` (OCR replacement
    character in place of a middle dot) and ``copper chloride (CuCl2)
    dihydrate`` without collapsing anhydrous ``CuCl2`` into the hydrate.
    """
    from evaluation.utils.step_equivalence_judge import _normalize_text

    raw = re.sub(r"\s+", "", name or "")
    word_match = _PAREN_FORMULA_HYDRATE.search(raw)
    if word_match is not None:
        formula = _normalize_text(word_match.group(1), "chemical_name")
        count = _HYDRATE_WORD_COUNTS[word_match.group(2).casefold()]
        return f"{formula}-{count}h2o" if formula else ""

    formula_match = _FORMULA_HYDRATE.fullmatch(raw)
    if formula_match is None:
        return ""
    formula = _normalize_text(formula_match.group(1), "chemical_name")
    count = formula_match.group(2) or "1"
    return f"{formula}-{count}h2o" if formula else ""


def _identity_keys(name: str) -> set[str]:
    """Normalized cores plus trailing parenthetical aliases such as ``name (H2BTB)``."""
    from evaluation.utils.step_equivalence_judge import _normalize_text

    keys: set[str] = set()
    for raw in (name or "", strip_quantity_annotation(name or "")):
        text = _normalize_text(raw, "chemical_name")
        if text:
            keys.add(text)
        match = _TRAILING_PAREN.search(raw.strip())
        if match is None:
            continue
        inner = _normalize_text(match.group(1), "chemical_name")
        outer = _normalize_text(raw[: match.start()], "chemical_name")
        if inner:
            keys.add(inner)
        if outer:
            keys.add(outer)
    hydrate = definite_hydrate_key(name)
    if hydrate:
        keys.add(hydrate)
    return keys


def deterministic_species_keys(name: str) -> set[str]:
    """Return safe lookup keys for indexed deterministic species matching."""
    from evaluation.utils.step_equivalence_judge import _normalize_text

    keys = _identity_keys(name)
    full = _normalize_text(name, "chemical_name")
    unspecified_core = strip_unspecified_hydrate(full)
    if unspecified_core:
        keys.add(f"unspecified-hydrate:{unspecified_core}")
    if equate_hydrate_counts():
        salt = strip_definite_hydrate_label(name)
        if salt:
            keys.add(f"hydrate-core:{_normalize_text(salt, 'chemical_name')}")
    return keys


def deterministic_species_match(ground_truth: str, prediction: str) -> bool:
    from evaluation.utils.step_equivalence_judge import _normalize_text

    gt_core = _normalize_text(strip_quantity_annotation(ground_truth), "chemical_name")
    pred_core = _normalize_text(strip_quantity_annotation(prediction), "chemical_name")
    if not gt_core or not pred_core:
        return False
    if gt_core == pred_core:
        return True
    gt_full = _normalize_text(ground_truth, "chemical_name")
    pred_full = _normalize_text(prediction, "chemical_name")
    if gt_core == pred_full or pred_core == gt_full:
        return True
    if deterministic_species_keys(ground_truth) & deterministic_species_keys(prediction):
        return True
    gt_hydrate = strip_unspecified_hydrate(gt_full)
    pred_hydrate = strip_unspecified_hydrate(pred_full)
    return bool(gt_hydrate and pred_hydrate and gt_hydrate == pred_hydrate)


def _cache_key(config: FastFieldMatchConfig, gt: str, pred: str) -> str:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "policy_version": POLICY_VERSION,
        "model": config.model,
        "ground_truth_value": gt,
        "prediction_value": pred,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _prompt(gt: str, pred: str) -> str:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "task": "fast_chemical_name_species_match",
        "ground_truth_value": gt,
        "prediction_value": pred,
        "policy": _POLICY,
    }
    return (
        "Return only one JSON object with exactly these keys: schema_version, "
        "equivalent, confidence, relation, reason. relation must be exactly one "
        "of equivalent, different, uncertain. Set equivalent=true only with "
        "confidence >= 0.9; otherwise fail closed with equivalent=false.\n\n"
        + json.dumps(payload, ensure_ascii=False, sort_keys=True)
    )


def _prompt_batch(pairs: list[dict[str, str]]) -> str:
    payload = {
        "schema_version": BATCH_SCHEMA_VERSION,
        "task": "fast_chemical_name_species_match_batch",
        "policy": _POLICY,
        "pairs": pairs,
    }
    return (
        "Return only one JSON object with exactly keys schema_version and judgements. "
        "Each judgement must have exactly pair_id, schema_version, equivalent, "
        "confidence, relation, reason. relation must be exactly one of equivalent, "
        "different, uncertain. Set equivalent=true only with confidence >= 0.9.\n\n"
        + json.dumps(payload, ensure_ascii=False, sort_keys=True)
    )


def _validate(data: dict[str, Any]) -> dict[str, Any]:
    required = {"schema_version", "equivalent", "confidence", "relation", "reason"}
    if set(data) != required or data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("fast field match schema mismatch")
    if not isinstance(data.get("equivalent"), bool):
        raise ValueError("fast field match equivalent must be boolean")
    confidence = data.get("confidence")
    if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        raise ValueError("fast field match confidence out of range")
    if data.get("relation") not in {"equivalent", "different", "uncertain"}:
        raise ValueError("invalid fast field match relation")
    if not str(data.get("reason") or "").strip():
        raise ValueError("fast field match reason is required")
    if data["equivalent"] and (confidence < 0.9 or data["relation"] != "equivalent"):
        raise ValueError("positive fast field match is not sufficiently certain")
    return data


def _validate_batch(data: dict[str, Any], pairs: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
    if set(data) != {"schema_version", "judgements"}:
        raise ValueError("fast field match batch has unexpected keys")
    if data.get("schema_version") != BATCH_SCHEMA_VERSION:
        raise ValueError("fast field match batch schema mismatch")
    rows = data.get("judgements")
    if not isinstance(rows, list):
        raise ValueError("fast field match judgements must be a list")
    expected = {pair["pair_id"] for pair in pairs}
    validated: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("fast field match row must be an object")
        pair_id = str(row.get("pair_id") or "")
        if pair_id not in expected or pair_id in validated:
            raise ValueError("fast field match pair ids do not match request")
        body = {k: v for k, v in row.items() if k != "pair_id"}
        if "schema_version" not in body:
            body = {**body, "schema_version": SCHEMA_VERSION}
        validated[pair_id] = _validate(body)
    if set(validated) != expected:
        raise ValueError("fast field match omitted requested pairs")
    return validated


class FastFieldMatchJudge:
    def __init__(self, config: FastFieldMatchConfig = FastFieldMatchConfig()):
        self.config = config
        self._memory: dict[tuple[str, str], FastFieldMatchJudgement] = {}

    def equivalent(self, ground_truth_value: str, prediction_value: str) -> bool:
        if not self.config.enabled:
            return False
        if deterministic_species_match(ground_truth_value, prediction_value):
            return True
        gt = str(ground_truth_value)
        pred = str(prediction_value)
        memory_key = (gt, pred)
        cached = self._memory.get(memory_key)
        if cached is not None:
            return cached.equivalent and cached.status == "ok"
        cache_path = self.config.cache_dir / (_cache_key(self.config, gt, pred) + ".json")
        judgement = self._read_cache(cache_path)
        if judgement is None:
            judgement = self._invoke(cache_path, gt, pred)
        self._memory[memory_key] = judgement
        return judgement.equivalent and judgement.status == "ok"

    def prefetch(self, pairs: Iterable[tuple[str, str]]) -> None:
        if not self.config.enabled:
            return
        pending: list[dict[str, str]] = []
        pending_meta: dict[str, tuple[str, str, Path]] = {}
        seen: set[tuple[str, str]] = set()
        for index, (gt, pred) in enumerate(pairs, start=1):
            if deterministic_species_match(gt, pred):
                self._memory[(gt, pred)] = FastFieldMatchJudgement(
                    equivalent=True,
                    confidence=1.0,
                    relation="equivalent",
                    reason="quantity annotation stripped to the same species",
                    source="deterministic",
                    status="ok",
                )
                continue
            key = (str(gt), str(pred))
            if key in seen or key in self._memory:
                continue
            seen.add(key)
            cache_path = self.config.cache_dir / (_cache_key(self.config, key[0], key[1]) + ".json")
            cached = self._read_cache(cache_path)
            if cached is not None:
                self._memory[key] = cached
                continue
            pair_id = f"f{index:04d}"
            pending.append(
                {
                    "pair_id": pair_id,
                    "ground_truth_value": key[0],
                    "prediction_value": key[1],
                }
            )
            pending_meta[pair_id] = (key[0], key[1], cache_path)
        if not pending:
            return
        chunks = [
            pending[i : i + self.config.batch_size]
            for i in range(0, len(pending), self.config.batch_size)
        ]
        print(
            f"[fast-field-match] prefetch pending={len(pending)} "
            f"chunks={len(chunks)} model={self.config.model}",
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
                validated = _validate_batch(response.data, chunk)
                for pair_id, row in validated.items():
                    gt, pred, cache_path = pending_meta[pair_id]
                    self._memory[(gt, pred)] = self._persist(cache_path, gt, pred, row)
            except Exception:
                for item in chunk:
                    gt, pred, cache_path = pending_meta[item["pair_id"]]
                    self._memory[(gt, pred)] = self._invoke(cache_path, gt, pred)

        workers = min(self.config.max_workers, len(chunks))
        if workers == 1:
            for chunk in chunks:
                _run_chunk(chunk)
        else:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = [executor.submit(_run_chunk, chunk) for chunk in chunks]
                for future in as_completed(futures):
                    future.result()

    def _read_cache(self, path: Path) -> FastFieldMatchJudgement | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            row = _validate(payload["judgement"])
        except (OSError, KeyError, TypeError, json.JSONDecodeError, ValueError):
            return None
        return FastFieldMatchJudgement(
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
        gt: str,
        pred: str,
        row: dict[str, Any],
    ) -> FastFieldMatchJudgement:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "policy_version": POLICY_VERSION,
            "model": self.config.model,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "ground_truth_value": gt,
            "prediction_value": pred,
            "judgement": row,
        }
        temp_path = cache_path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temp_path.replace(cache_path)
        return FastFieldMatchJudgement(
            equivalent=bool(row["equivalent"]),
            confidence=float(row["confidence"]),
            relation=str(row["relation"]),
            reason=str(row["reason"]),
            source="llm",
            status="ok",
        )

    def _invoke(self, cache_path: Path, gt: str, pred: str) -> FastFieldMatchJudgement:
        try:
            response = invoke_json(
                self.config.model,
                _prompt(gt, pred),
                timeout_seconds=self.config.timeout_seconds,
                max_attempts=2,
            )
            row = _validate(response.data)
            return self._persist(cache_path, gt, pred, row)
        except Exception as exc:
            if self.config.required:
                raise RuntimeError("fast field match judge failed") from exc
            return FastFieldMatchJudgement(
                equivalent=False,
                confidence=0.0,
                relation="uncertain",
                reason=f"judge failed closed: {type(exc).__name__}",
                source="error",
                status="error",
            )
