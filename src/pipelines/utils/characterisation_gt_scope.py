"""Keep Characterisation GT only for ChemicalOutputs of Steps Synthesis[]."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def normalize_ccdc(value: Any) -> str:
    try:
        text = str(value or "").strip().lower()
    except Exception:
        return ""
    if text in {"", "n/a", "na"}:
        return ""
    digits = re.sub(r"\D+", "", text)
    return digits


def normalize_product_name(value: Any) -> str:
    try:
        text = str(value or "").strip().lower()
    except Exception:
        return ""
    if text in {"", "n/a", "na"}:
        return ""
    replacements = {
        "₀": "0",
        "₁": "1",
        "₂": "2",
        "₃": "3",
        "₄": "4",
        "₅": "5",
        "₆": "6",
        "₇": "7",
        "₈": "8",
        "₉": "9",
        "α": "alpha",
        "β": "beta",
        "γ": "gamma",
        "δ": "delta",
        "·": ".",
        "•": ".",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    text = re.sub(r"_", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def core_product_name(value: Any) -> str:
    text = normalize_product_name(value)
    if not text:
        return ""
    text = re.sub(r"\s*\([^)]*\)", " ", text)
    text = re.sub(r"\s+from\s+.*$", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" -:,.")


@dataclass(frozen=True)
class SynthesisOutputs:
    ccdcs: frozenset[str]
    names: frozenset[str]
    cores: frozenset[str]


@dataclass
class ScopeDecision:
    keep: bool
    reason: str
    names: list[str] = field(default_factory=list)
    ccdc: str = ""


def synthesis_outputs_from_steps(steps_obj: Any) -> SynthesisOutputs:
    ccdcs: set[str] = set()
    names: set[str] = set()
    cores: set[str] = set()
    for synthesis in (steps_obj or {}).get("Synthesis", []) or []:
        ccdc = normalize_ccdc((synthesis or {}).get("productCCDCNumber"))
        if ccdc:
            ccdcs.add(ccdc)
        for raw_name in (synthesis or {}).get("productNames", []) or []:
            name = normalize_product_name(raw_name)
            core = core_product_name(raw_name)
            if name:
                names.add(name)
            if core:
                cores.add(core)
    return SynthesisOutputs(
        ccdcs=frozenset(ccdcs),
        names=frozenset(names),
        cores=frozenset(cores),
    )


def characterisation_matches_synthesis(
    entry: Any,
    outputs: SynthesisOutputs,
) -> ScopeDecision:
    names = [
        str(value).strip()
        for value in (entry or {}).get("productNames", []) or []
        if str(value).strip()
    ]
    ccdc = normalize_ccdc((entry or {}).get("productCCDCNumber"))
    if ccdc and ccdc in outputs.ccdcs:
        return ScopeDecision(True, "ccdc", names, ccdc)
    normalized_names = {normalize_product_name(name) for name in names}
    if normalized_names & outputs.names:
        return ScopeDecision(True, "name", names, ccdc)
    cores = {core_product_name(name) for name in names}
    if cores & outputs.cores:
        return ScopeDecision(True, "core_name", names, ccdc)
    return ScopeDecision(False, "not_synthesis_output", names, ccdc)


def filter_characterisation_document(
    characterisation_obj: Any,
    steps_obj: Any,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    outputs = synthesis_outputs_from_steps(steps_obj)
    removed: list[dict[str, Any]] = []
    devices: list[dict[str, Any]] = []
    for device in (characterisation_obj or {}).get("Devices", []) or []:
        kept: list[dict[str, Any]] = []
        for entry in (device or {}).get("Characterisation", []) or []:
            decision = characterisation_matches_synthesis(entry, outputs)
            if decision.keep:
                kept.append(entry)
                continue
            removed.append(
                {
                    "productNames": decision.names,
                    "productCCDCNumber": (entry or {}).get("productCCDCNumber"),
                    "reason": decision.reason,
                }
            )
        next_device = dict(device or {})
        next_device["Characterisation"] = kept
        devices.append(next_device)
    filtered = dict(characterisation_obj or {})
    filtered["Devices"] = devices
    return filtered, removed


def revise_characterisation_gt_tree(
    characterisation_dir: Path,
    steps_dir: Path,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "rule": "Keep Characterisation only when it matches a Steps Synthesis product (CCDC or product name).",
        "files": {},
        "kept": 0,
        "removed": 0,
    }
    for char_path in sorted(characterisation_dir.glob("*.json")):
        if char_path.name.startswith("_"):
            continue
        steps_path = steps_dir / char_path.name
        char_obj = json.loads(char_path.read_text(encoding="utf-8"))
        if not steps_path.is_file():
            report["files"][char_path.name] = {
                "status": "missing_steps",
                "kept": 0,
                "removed": 0,
            }
            continue
        steps_obj = json.loads(steps_path.read_text(encoding="utf-8"))
        filtered, removed = filter_characterisation_document(char_obj, steps_obj)
        kept = sum(
            len((device or {}).get("Characterisation", []) or [])
            for device in (filtered.get("Devices") or [])
        )
        if removed:
            char_path.write_text(
                json.dumps(filtered, indent=4, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        report["files"][char_path.name] = {
            "status": "revised" if removed else "unchanged",
            "kept": kept,
            "removed": len(removed),
            "dropped": removed,
        }
        report["kept"] += kept
        report["removed"] += len(removed)
    return report
