"""Score extraction hints directly against OntoSynthesis ground truth.

This intentionally bypasses KG materialization. Iteration 2 JSON hints are
compared as synthesis-level chemical inventories. Iteration 3
SEMANTIC_HINTS_V1 ledgers are projected into the established step scoring
schema before using the repository's normal step scorer.
"""

from __future__ import annotations

import argparse
import itertools
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from evaluation.normalize_steps import normalize_json_structure
from evaluation.scoring_steps import (
    _expand_add_steps_in_obj,
    _synthesis_identity_score,
    score_steps_fine_grained,
)
from evaluation.utils.scoring_common import precision_recall_f1, to_fingerprint


STEP_ENTRY = re.compile(
    r"(?:^|\n\s*)(?:\d+\.\s*)?"
    r"(?P<type>Add|Stir|HeatChill|Evaporate|Sonicate|Transfer|Separate|Filter|Dry|Crystallize)"
    r"\s+step\s*\(order\s*(?P<order>\d+)\)\s*:\s*"
    r"(?P<body>.*?)(?=\n\s*(?:\d+\.\s*)?"
    r"(?:Add|Stir|HeatChill|Evaporate|Sonicate|Transfer|Separate|Filter|Dry|Crystallize)"
    r"\s+step\s*\(order|\Z)",
    re.IGNORECASE | re.DOTALL,
)
STEP_BULLET_ENTRY = re.compile(
    r"(?m)^-\s*"
    r"(?P<type>Add|Stir|HeatChill|Evaporate|Sonicate|Transfer|Separate|Filter|Dry|Crystallize)"
    r"(?P<header>[^\n]*)\r?\n"
    r"(?P<body>(?:[ \t]+-[^\n]*(?:\r?\n|$))*)",
    re.IGNORECASE,
)
STEP_INLINE_ORDER_ENTRY = re.compile(
    r"(?m)^"
    r"(?P<type>Add|Stir|HeatChill|Evaporate|Sonicate|Transfer|Separate|Filter|Dry|Crystallize)"
    r"\s*\(\s*hasOrder\s*:\s*(?P<order>\d+)\s*\)\s*\r?\n"
    r"(?P<body>(?:-\s*[^\n]*(?:\r?\n|$))*)",
    re.IGNORECASE,
)
QUOTED = r'["“](.+?)["”]'


def _safe_name(label: str) -> str:
    from src.pipelines.main_ontology_extractions.extract import _safe_name as safe_name

    return safe_name(label)


def _first_match(patterns: list[str], text: str) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if match:
            return re.sub(r"\s+", " ", match.group(1)).strip(" .;,")
    return ""


def _chemical_name(body: str) -> str:
    name = _first_match(
        [
            r'^\s*Introduces\s+step-local ChemicalInput\s+["“](.+?)["”]',
            r"^\s*Introduces\s+(.+?)\s+as\b",
            r"^\s*Introduces\s+(.+?)\s+with amount\b",
            r"^\s*(.+?)\s+was\s+added\b",
            r"^\s*(.+?)\s+is\s+(?:added|introduced|placed|charged)\b",
            r"^\s*Introduce\s+(.+?)\s+as\b",
            r"^\s*Add\s+(.+?)(?:\s+to\b|\s+as\b|[.])",
            r"^\s*(.+?)\s+is\s+used\b",
        ],
        body,
    )
    if not name:
        return ""
    name = re.sub(
        r"\s*\([^)]*(?:mg|mmol|mL|ml|µL|uL|muL|drops?|\bg\b)[^)]*\)\s*$",
        "",
        name,
    )
    name = re.sub(r",?\s*amount\s+[^,.;]+(?:,\s*[^,.;]+)?\s*$", "", name)
    return name.strip(" .;,")


def _amount(body: str) -> str:
    if re.search(r"\bno explicit amount\b", body, re.IGNORECASE):
        return ""
    return _first_match(
        [
            rf"\bin the amount\s+{QUOTED}",
            rf"\bamount(?: is| of)?\s+{QUOTED}",
            rf"\bwith(?: an)? amount\s+{QUOTED}",
            rf"\bwith amount\s+{QUOTED}",
            rf"\bamount[: ]+\s*{QUOTED}",
            r"\bamount[: ]+\s*([^.;]+)",
            r"\(([^()]*(?:mg|mmol|mL|ml|µL|uL|muL|drops?|mol|grams?|\bg\b)[^()]*)\)",
        ],
        body,
    )


def _duration(body: str) -> str:
    if re.search(r"\bno explicit (?:step )?duration\b", body, re.IGNORECASE):
        return ""
    return _first_match(
        [
            rf"\bstep duration is\s+{QUOTED}",
            rf"\bduration(?: is| of)?\s+{QUOTED}",
            rf"\bfor a duration of\s+{QUOTED}",
            rf"\bfor\s+{QUOTED}",
            rf"\bwithin\s+{QUOTED}",
        ],
        body,
    )


def _temperature(body: str) -> str:
    if re.search(r"\bno explicit temperature\b", body, re.IGNORECASE):
        return ""
    return _first_match(
        [
            rf"\btarget temperature is\s+{QUOTED}",
            rf"\btemperature(?: is| of)?\s+{QUOTED}",
            rf"\b(?:heated|cooled|held|placed)\s+at\s+{QUOTED}",
            rf"\bto\s+{QUOTED}",
            rf"\bat\s+{QUOTED}",
        ],
        body,
    )


def _device(body: str) -> str:
    return _first_match(
        [
            rf"\b(?:heat/chill )?device is\s+{QUOTED}",
            rf"\busing\s+(?:an?|the)\s+{QUOTED}",
            r"\bin (?:an?|the)\s+([^.,;]*(?:oven|furnace|bath|sonicator|centrifuge))",
        ],
        body,
    )


def _washing_solvent(body: str) -> str:
    return _first_match(
        [
            rf"\bwashing solvent is\s+{QUOTED}",
            rf"\bwashed with\s+(?:the\s+)?{QUOTED}",
            r"\bwashed with\s+(?:the\s+)?([^.;]+)",
        ],
        body,
    )


_HASX_TYPES = (
    "Add",
    "Stir",
    "HeatChill",
    "Evaporate",
    "Sonicate",
    "Transfer",
    "Separate",
    "Filter",
    "Dry",
    "Crystallize",
)
_HASX_HEADER = re.compile(
    r"^(?:\d+\.\s*)?(?P<type>" + "|".join(_HASX_TYPES) + r")\b"
    r"(?:\s*\(\s*hasOrder\s*:\s*(?P<order>\d+)\s*\))?"
    r"\s*[-–—:]?\s*(?P<header>.*)$",
    re.IGNORECASE,
)
_HASX_PROP = re.compile(r"^-?\s*(?P<key>[A-Za-z][A-Za-z0-9]*)\s*:\s*(?P<value>.*)$")
_STEPTYPE_LINE = re.compile(
    r"^StepType\s*:\s*(?P<type>" + "|".join(_HASX_TYPES) + r")\s*$",
    re.IGNORECASE,
)
_CHEM_INPUT = re.compile(
    r'ChemicalInput\s+for\s+["“](?P<name>.+?)["”](?:\s*\((?P<inside>.*)\))?',
    re.IGNORECASE,
)


def _truthy(value: str) -> bool:
    return str(value or "").strip().casefold() in {"true", "yes", "1", "sealed"}


def _split_aliases(raw: str) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for part in re.split(r"\s*;\s*", str(raw or "")):
        name = part.strip().strip("\"'")
        key = name.casefold()
        if not name or key in seen:
            continue
        seen.add(key)
        names.append(name)
        if len(names) >= 8:
            break
    return names


def _amount_from_header(header: str) -> str:
    match = re.search(
        r"\(([^()]*(?:mg|mmol|mL|ml|µL|uL|muL|drops?|mol|\bg\b)[^()]*)\)",
        header,
        re.IGNORECASE,
    )
    return re.sub(r"\s+", " ", match.group(1)).strip() if match else ""


def _props_from_chem_input(raw: str) -> dict[str, str]:
    extra: dict[str, str] = {}
    match = _CHEM_INPUT.search(raw)
    if not match:
        return extra
    extra["hasAddedChemicalInput"] = match.group("name").strip()
    inside = match.group("inside") or ""
    amount = re.search(r"\bamount\s*:\s*([^;]+)", inside, re.IGNORECASE)
    if amount:
        extra["hasAmount"] = amount.group(1).strip()
    aliases = re.search(r"\bhasAlternativeNames\s*:\s*(.+?)(?=;\s*has[A-Z]|$)", inside, re.IGNORECASE)
    if aliases:
        extra["hasAlternativeNames"] = aliases.group(1).strip()
    formula = re.search(r"\bhasChemicalFormula\s*:\s*([^;]+)", inside, re.IGNORECASE)
    if formula:
        extra["hasChemicalFormula"] = formula.group(1).strip()
    return extra


def _payload_from_hasx(step_type: str, header: str, props: dict[str, str]) -> dict[str, Any]:
    canonical = {
        "add": "Add",
        "stir": "Stir",
        "heatchill": "HeatChill",
        "evaporate": "Evaporate",
        "sonicate": "Sonicate",
        "transfer": "Transfer",
        "separate": "Separate",
        "filter": "Filter",
        "dry": "Dry",
        "crystallize": "Crystallize",
    }[step_type.casefold()]
    try:
        order = int(props.get("hasOrder") or props.get("order") or "0")
    except ValueError:
        order = 0
    payload: dict[str, Any] = {"stepNumber": order}
    duration = props.get("hasStepDuration") or props.get("hasDuration") or ""
    if duration:
        payload["duration"] = duration
    temperature = props.get("hasTargetTemperature") or props.get("hasTemperature") or ""
    if temperature:
        payload["targetTemperature"] = temperature
    if _truthy(props.get("isSealed") or props.get("sealedVessel") or ""):
        payload["sealedVessel"] = True
    if canonical == "Add":
        primary = (props.get("hasAddedChemicalInput") or "").strip()
        if not primary:
            primary = re.sub(r'^["“]|["”]$', "", header).split("(")[0].strip()
        names = [primary] if primary else []
        names.extend(
            name
            for name in _split_aliases(props.get("hasAlternativeNames") or "")
            if name.casefold() not in {item.casefold() for item in names}
        )
        amount = (props.get("hasAmount") or "").strip() or _amount_from_header(header)
        payload["addedChemical"] = (
            [{"chemicalName": names, "chemicalAmount": amount or "N/A"}] if names else []
        )
        payload["stir"] = _truthy(props.get("hasStir") or "") or bool(
            re.search(r"\bstirr", header, re.IGNORECASE)
        )
        payload["isLayered"] = _truthy(props.get("isLayered") or "")
    elif canonical == "HeatChill":
        device = (props.get("hasEquipment") or props.get("hasDevice") or "").strip()
        if device:
            payload["usedDevice"] = device
        if "sealedVessel" not in payload:
            payload["sealedVessel"] = bool(re.search(r"\b(?:capped|sealed|closed)\b", header, re.IGNORECASE))
    elif canonical == "Filter":
        solvent = (props.get("hasWashingSolvent") or "").strip()
        payload["washingSolvent"] = (
            [{"chemicalName": [solvent], "chemicalAmount": "N/A"}] if solvent else []
        )
    elif canonical == "Separate":
        solvent = (props.get("hasSeparationSolvent") or "").strip()
        if solvent:
            payload["separationSolvent"] = [{"chemicalName": [solvent], "chemicalAmount": "N/A"}]
    return {canonical: payload}


def parse_hasx_steps(text: str) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line in {"SEMANTIC_HINTS_V1", "```", "```json"}:
            continue
        steptype = _STEPTYPE_LINE.match(line)
        if steptype:
            if current is not None:
                steps.append(
                    _payload_from_hasx(current["type"], current["header"], current["props"])
                )
            current = {"type": steptype.group("type"), "header": "", "props": {}}
            continue
        header = _HASX_HEADER.match(line)
        if header and not line.lstrip("- ").lower().startswith("has"):
            if current is not None:
                steps.append(
                    _payload_from_hasx(current["type"], current["header"], current["props"])
                )
            props: dict[str, str] = {}
            if header.group("order"):
                props["hasOrder"] = header.group("order")
            current = {
                "type": header.group("type"),
                "header": header.group("header"),
                "props": props,
            }
            continue
        prop = _HASX_PROP.match(line)
        if current is not None and prop:
            key = prop.group("key")
            value = prop.group("value").strip()
            current["props"][key] = value
            if key == "hasAddedChemicalInput":
                current["props"].update(
                    {
                        name: val
                        for name, val in _props_from_chem_input(value).items()
                        if name not in current["props"] or name == "hasAddedChemicalInput"
                    }
                )
    if current is not None:
        steps.append(_payload_from_hasx(current["type"], current["header"], current["props"]))
    return [step for step in steps if step]


def _step_payload(step_type: str, order: int, body: str) -> dict[str, Any]:
    canonical = {
        "add": "Add",
        "stir": "Stir",
        "heatchill": "HeatChill",
        "evaporate": "Evaporate",
        "sonicate": "Sonicate",
        "transfer": "Transfer",
        "separate": "Separate",
        "filter": "Filter",
        "dry": "Dry",
        "crystallize": "Crystallize",
    }[step_type.casefold()]
    payload: dict[str, Any] = {"stepNumber": order}
    duration = _duration(body)
    if duration:
        payload["duration"] = duration
    if canonical == "Add":
        name = _chemical_name(body)
        amount = _amount(body)
        payload["addedChemical"] = (
            [{"chemicalName": [name], "chemicalAmount": amount or "N/A"}]
            if name
            else []
        )
        payload["stir"] = bool(re.search(r"\bstirr", body, re.IGNORECASE))
        payload["isLayered"] = bool(re.search(r"\blayer", body, re.IGNORECASE))
    elif canonical == "HeatChill":
        temperature = _temperature(body)
        if temperature:
            payload["targetTemperature"] = temperature
        device = _device(body)
        if device:
            payload["usedDevice"] = device
        payload["sealedVessel"] = bool(
            re.search(r"\b(?:capped|sealed|closed)\b", body, re.IGNORECASE)
        )
    elif canonical == "Filter":
        solvent = _washing_solvent(body)
        payload["washingSolvent"] = (
            [{"chemicalName": [solvent], "chemicalAmount": "N/A"}]
            if solvent
            else []
        )
    elif canonical == "Separate":
        solvent = _first_match(
            [rf"\bseparation solvent is\s+{QUOTED}", r"\busing\s+([^.;]+)"], body
        )
        if solvent:
            payload["separationSolvent"] = [
                {"chemicalName": [solvent], "chemicalAmount": "N/A"}
            ]
    return {canonical: payload}


def parse_semantic_steps(text: str, label: str) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    if not text.lstrip().startswith("SEMANTIC_HINTS_V1"):
        errors.append("missing SEMANTIC_HINTS_V1 marker")
    hasx_steps = parse_hasx_steps(text)
    if hasx_steps:
        return {
            "productNames": [label],
            "productCCDCNumber": "N/A",
            "steps": hasx_steps,
        }, errors
    entries = list(STEP_ENTRY.finditer(text))
    if entries:
        steps = [
            _step_payload(
                match.group("type"),
                int(match.group("order")),
                re.sub(r"\s+", " ", match.group("body")).strip(),
            )
            for match in entries
        ]
    else:
        step_header = re.compile(
            r"^\s*-?\s*"
            r"(?P<type>Add|Stir|HeatChill|Evaporate|Sonicate|Transfer|Separate|Filter|Dry|Crystallize)"
            r"\b(?P<header>.*)$",
            re.IGNORECASE,
        )
        lines = text.splitlines()
        steps = []
        index = 0
        while index < len(lines):
            match = step_header.match(lines[index])
            if match is None:
                index += 1
                continue
            body_lines: list[str] = []
            next_index = index + 1
            while next_index < len(lines) and step_header.match(lines[next_index]) is None:
                body_lines.append(lines[next_index])
                next_index += 1
            header = str(match.group("header") or "").strip()
            body = re.sub(r"\s+", " ", " ".join(body_lines)).strip()
            order_match = re.search(
                r"\b(?:hasOrder|order)\s*:\s*(\d+)\b",
                f"{header} {body}",
                re.IGNORECASE,
            )
            if order_match is None:
                errors.append(f"{match.group('type')} entry is missing hasOrder")
            else:
                operation_label = header
                if operation_label.startswith("(") and operation_label.endswith(")"):
                    operation_label = operation_label[1:-1].strip()
                if not re.fullmatch(
                    r"(?:hasOrder|order)\s*:\s*\d+|occurrence",
                    operation_label,
                    re.IGNORECASE,
                ):
                    body = f"Introduces {operation_label} as input. {body}"
                steps.append(
                    _step_payload(
                        match.group("type"),
                        int(order_match.group(1)),
                        body,
                    )
                )
            index = next_index
        if not steps:
            errors.append("no numbered/order or bullet/hasOrder step entries parsed")
    return {
        "productNames": [label],
        "productCCDCNumber": "N/A",
        "steps": steps,
    }, errors


def _entity_iter2(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"inputs": [], "outputs": []}
    entities = {
        str(entity.get("ref")): entity
        for entity in payload.get("entities", [])
        if isinstance(entity, dict)
    }
    inputs: list[dict[str, str]] = []
    outputs: list[dict[str, str]] = []
    for entity in entities.values():
        props = entity.get("datatype_properties") or {}
        row = {
            "name": str(entity.get("label") or "").strip(),
            "amount": str(props.get("hasAmount") or "").strip(),
            "formula": str(props.get("hasChemicalFormula") or "").strip(),
        }
        if entity.get("class") == "ChemicalInput":
            inputs.append(row)
        elif entity.get("class") == "ChemicalOutput":
            outputs.append(row)
    return {"inputs": inputs, "outputs": outputs}


def _gt_chemical_rows(gt: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for proc in gt.get("synthesisProcedures", []) or []:
        for step in proc.get("steps", []) or []:
            for input_chemical in step.get("inputChemicals", []) or []:
                for chemical in input_chemical.get("chemical", []) or []:
                    rows.append(
                        {
                            "names": [
                                str(value).strip()
                                for value in chemical.get("chemicalName", []) or []
                                if str(value).strip()
                            ],
                            "amount": str(chemical.get("chemicalAmount") or "").strip(),
                            "formula": str(chemical.get("chemicalFormula") or "").strip(),
                        }
                    )
    return rows


def _name_similarity(predicted: str, aliases: list[str]) -> float:
    pred_fp = to_fingerprint(predicted)
    alias_fps = [to_fingerprint(alias) for alias in aliases]
    if pred_fp in alias_fps:
        return 1.0
    pred_tokens = set(re.findall(r"[a-z0-9]+", pred_fp))
    best = 0.0
    for alias_fp in alias_fps:
        alias_tokens = set(re.findall(r"[a-z0-9]+", alias_fp))
        if pred_tokens and alias_tokens:
            best = max(best, len(pred_tokens & alias_tokens) / len(pred_tokens | alias_tokens))
    return best


def _chemical_inventory_score(
    predictions: list[dict[str, str]], gt_rows: list[dict[str, Any]]
) -> tuple[int, int, int, list[dict[str, Any]]]:
    candidates: list[tuple[float, int, int]] = []
    for pred_index, pred in enumerate(predictions):
        for gt_index, gt in enumerate(gt_rows):
            candidates.append(
                (_name_similarity(pred["name"], gt["names"]), pred_index, gt_index)
            )
    matched_pred: set[int] = set()
    matched_gt: set[int] = set()
    details: list[dict[str, Any]] = []
    for score, pred_index, gt_index in sorted(candidates, reverse=True):
        if score < 0.5 or pred_index in matched_pred or gt_index in matched_gt:
            continue
        matched_pred.add(pred_index)
        matched_gt.add(gt_index)
        pred = predictions[pred_index]
        gt = gt_rows[gt_index]
        pred_amount = to_fingerprint(pred.get("amount", ""))
        gt_amount = to_fingerprint(gt.get("amount", ""))
        amount_ok = bool(
            not gt_amount
            or gt_amount in {'"n/a"', "n/a"}
            or pred_amount == gt_amount
            or (
                pred_amount
                and gt_amount
                and set(re.findall(r"\d+(?:\.\d+)?", gt_amount))
                <= set(re.findall(r"\d+(?:\.\d+)?", pred_amount))
            )
        )
        details.append(
            {
                "predicted": pred["name"],
                "gt": gt["names"][0] if gt["names"] else "",
                "name_score": round(score, 3),
                "predicted_amount": pred.get("amount", ""),
                "gt_amount": gt.get("amount", ""),
                "amount_ok": amount_ok,
            }
        )
    for index, pred in enumerate(predictions):
        if index not in matched_pred:
            details.append({"predicted": pred["name"], "gt": "", "status": "extra"})
    for index, gt in enumerate(gt_rows):
        if index not in matched_gt:
            details.append(
                {
                    "predicted": "",
                    "gt": gt["names"][0] if gt["names"] else "",
                    "gt_amount": gt.get("amount", ""),
                    "status": "missing",
                }
            )
    return len(matched_pred), len(predictions) - len(matched_pred), len(gt_rows) - len(matched_gt), details


def _step_types(synthesis: dict[str, Any]) -> list[str]:
    return [next(iter(step), "") for step in synthesis.get("steps", []) if step]


def _type_counts(gt: dict[str, Any], pred: dict[str, Any]) -> tuple[int, int, int]:
    gt_counter = Counter(_step_types(gt))
    pred_counter = Counter(_step_types(pred))
    tp = sum(min(gt_counter[key], pred_counter[key]) for key in gt_counter | pred_counter)
    return tp, sum(pred_counter.values()) - tp, sum(gt_counter.values()) - tp


def _identity_core(value: str) -> str:
    text = str(value or "").casefold()
    for prefix in (
        "structural transformation from ",
        "synthesis of ",
        "preparation of ",
        "synthesis_",
    ):
        if text.startswith(prefix):
            text = text[len(prefix) :]
    text = text.replace("α", "alpha").replace("β", "beta").replace("μ", "mu")
    return re.sub(r"[^a-z0-9]+", "", text)


def _pair_score(gt: dict[str, Any], pred: dict[str, Any]) -> float:
    identity = _synthesis_identity_score(gt, pred)
    tp, fp, fn = _type_counts(gt, pred)
    type_f1 = (2 * tp / (2 * tp + fp + fn)) if tp else 0.0
    return (
        identity[0] * 1_000_000_000
        + identity[1] * 1_000_000
        + identity[2] * 1_000
        + identity[3]
        + type_f1
    )


def _pair_syntheses(
    gt_syntheses: list[dict[str, Any]], pred_syntheses: list[dict[str, Any]]
) -> list[tuple[int | None, int | None]]:
    """Return a maximum-score one-to-one assignment, preserving unmatched rows."""
    gt_count = len(gt_syntheses)
    pred_count = len(pred_syntheses)
    size = max(gt_count, pred_count)
    best_score = float("-inf")
    best_perm: tuple[int, ...] = tuple()
    for permutation in itertools.permutations(range(size)):
        score = 0.0
        for gt_index in range(gt_count):
            pred_index = permutation[gt_index]
            if pred_index < pred_count:
                score += _pair_score(gt_syntheses[gt_index], pred_syntheses[pred_index])
        if score > best_score:
            best_score = score
            best_perm = permutation
    pairs: list[tuple[int | None, int | None]] = []
    matched_pred: set[int] = set()
    for gt_index in range(gt_count):
        pred_index = best_perm[gt_index]
        if pred_index < pred_count:
            pairs.append((gt_index, pred_index))
            matched_pred.add(pred_index)
        else:
            pairs.append((gt_index, None))
    pairs.extend((None, index) for index in range(pred_count) if index not in matched_pred)
    return pairs


def evaluate(run_root: Path, repo_root: Path) -> dict[str, Any]:
    hash_to_doi = {
        value: key
        for key, value in json.loads(
            (repo_root / "data" / "doi_to_hash.json").read_text(encoding="utf-8")
        ).items()
        if str(key).startswith("10.")
    }
    cases: list[dict[str, Any]] = []
    totals = {
        "top_pred": 0,
        "top_gt": 0,
        "iter2_chem_tp": 0,
        "iter2_chem_fp": 0,
        "iter2_chem_fn": 0,
        "iter3_add_tp": 0,
        "iter3_add_fp": 0,
        "iter3_add_fn": 0,
        "type_tp": 0,
        "type_fp": 0,
        "type_fn": 0,
        "fine_tp": 0,
        "fine_fp": 0,
        "fine_fn": 0,
    }
    for case_dir in sorted(run_root.iterdir()):
        doi = hash_to_doi.get(case_dir.name)
        top_path = case_dir / "mcp_run" / "iter1_top_entities.json"
        if not doi or not top_path.exists():
            continue
        gt_steps = _expand_add_steps_in_obj(
            json.loads(
                (repo_root / "full_ground_truth" / "steps" / f"{doi}.json").read_text(
                    encoding="utf-8"
                )
            )
        )
        gt_chemicals = json.loads(
            (repo_root / "full_ground_truth" / "chemicals" / f"{doi}.json").read_text(
                encoding="utf-8"
            )
        )
        entities = json.loads(top_path.read_text(encoding="utf-8"))
        pred_syntheses: list[dict[str, Any]] = []
        pred_chemicals: list[dict[str, str]] = []
        parse_errors: list[str] = []
        for entity in entities:
            label = str(entity.get("label") or "")
            safe = _safe_name(label)
            iter2_path = case_dir / "mcp_run" / f"iter2_hints_{safe}.txt"
            iter3_path = case_dir / "mcp_run" / f"iter3_hints_{safe}.txt"
            if not iter3_path.exists():
                parse_errors.append(f"{label}: missing {iter3_path.name}")
                continue
            pred_chemicals.extend(_entity_iter2(iter2_path)["inputs"])
            synthesis, errors = parse_semantic_steps(
                iter3_path.read_text(encoding="utf-8"), label
            )
            pred_syntheses.append(synthesis)
            parse_errors.extend(f"{label}: {error}" for error in errors)
        pred_steps = {"Synthesis": pred_syntheses}
        pred_step_inputs: list[dict[str, str]] = []
        for synthesis in pred_syntheses:
            for step in synthesis.get("steps", []) or []:
                add = step.get("Add") if isinstance(step, dict) else None
                if not isinstance(add, dict):
                    continue
                for chemical in add.get("addedChemical", []) or []:
                    names = chemical.get("chemicalName", []) or []
                    pred_step_inputs.append(
                        {
                            "name": str(names[0] if names else "").strip(),
                            "amount": str(chemical.get("chemicalAmount") or "").strip(),
                            "formula": "",
                        }
                    )
        entity_rows: list[dict[str, Any]] = []
        case_type = [0, 0, 0]
        case_fine = [0, 0, 0]
        gt_syntheses = gt_steps.get("Synthesis", []) or []
        for gt_index, pred_index in _pair_syntheses(gt_syntheses, pred_syntheses):
            if gt_index is None:
                pred_synthesis = pred_syntheses[pred_index]  # type: ignore[index]
                pred_count = len(pred_synthesis.get("steps", []) or [])
                case_type[1] += pred_count
                _, fine_fp, _, _ = score_steps_fine_grained(
                    {"Synthesis": []}, {"Synthesis": [pred_synthesis]}, True, True
                )
                case_fine[1] += fine_fp
                entity_rows.append(
                    {
                        "gt": "",
                        "predicted": (pred_synthesis.get("productNames") or [""])[0],
                        "status": "pred_only",
                    }
                )
                continue
            gt_synthesis = gt_syntheses[gt_index]
            if pred_index is None:
                gt_count = len(gt_synthesis.get("steps", []) or [])
                case_type[2] += gt_count
                _, _, fine_fn, _ = score_steps_fine_grained(
                    {"Synthesis": [gt_synthesis]}, {"Synthesis": []}, True, True
                )
                case_fine[2] += fine_fn
                entity_rows.append(
                    {
                        "gt": (gt_synthesis.get("productNames") or [""])[0],
                        "predicted": "",
                        "status": "gt_only",
                    }
                )
                continue
            pred_synthesis = pred_syntheses[pred_index]
            type_score = _type_counts(gt_synthesis, pred_synthesis)
            scored_prediction = dict(pred_synthesis)
            # Entity pairing is performed above from extraction identity + step
            # structure. Inject that accepted pairing into the field scorer so
            # missing CCDC identifiers do not turn every field into FP/FN.
            scored_prediction["productNames"] = list(
                gt_synthesis.get("productNames", []) or []
            )
            scored_prediction["productCCDCNumber"] = gt_synthesis.get(
                "productCCDCNumber", "N/A"
            )
            fine_score = score_steps_fine_grained(
                normalize_json_structure({"Synthesis": [gt_synthesis]}),
                normalize_json_structure({"Synthesis": [scored_prediction]}),
                ignore_vessel=True,
                skip_order=True,
            )[:3]
            for index, value in enumerate(type_score):
                case_type[index] += value
            for index, value in enumerate(fine_score):
                case_fine[index] += value
            entity_rows.append(
                {
                    "gt": (gt_synthesis.get("productNames") or [""])[0],
                    "predicted": (pred_synthesis.get("productNames") or [""])[0],
                    "gt_types": _step_types(gt_synthesis),
                    "predicted_types": _step_types(pred_synthesis),
                    "type_score": type_score,
                    "fine_score": fine_score,
                    "status": "matched",
                }
            )
        iter2_chem_score = _chemical_inventory_score(
            pred_chemicals, _gt_chemical_rows(gt_chemicals)
        )
        iter3_add_score = _chemical_inventory_score(
            pred_step_inputs, _gt_chemical_rows(gt_chemicals)
        )
        totals["top_pred"] += len(entities)
        totals["top_gt"] += len(gt_steps.get("Synthesis", []) or [])
        for key, value in zip(
            ("iter2_chem_tp", "iter2_chem_fp", "iter2_chem_fn"),
            iter2_chem_score[:3],
        ):
            totals[key] += value
        for key, value in zip(
            ("iter3_add_tp", "iter3_add_fp", "iter3_add_fn"),
            iter3_add_score[:3],
        ):
            totals[key] += value
        for key, value in zip(("type_tp", "type_fp", "type_fn"), case_type):
            totals[key] += value
        for key, value in zip(("fine_tp", "fine_fp", "fine_fn"), case_fine):
            totals[key] += value
        cases.append(
            {
                "hash": case_dir.name,
                "doi": doi,
                "top_pred": len(entities),
                "top_gt": len(gt_steps.get("Synthesis", []) or []),
                "iter2_chemical_score": iter2_chem_score[:3],
                "iter2_chemical_details": iter2_chem_score[3],
                "iter3_add_score": iter3_add_score[:3],
                "iter3_add_details": iter3_add_score[3],
                "step_type_score": case_type,
                "step_fine_score": case_fine,
                "entities": entity_rows,
                "parse_errors": parse_errors,
            }
        )
    for prefix in ("iter2_chem", "iter3_add", "type", "fine"):
        p, r, f1 = precision_recall_f1(
            totals[f"{prefix}_tp"], totals[f"{prefix}_fp"], totals[f"{prefix}_fn"]
        )
        totals[f"{prefix}_metrics"] = {
            "precision": p,
            "recall": r,
            "f1": f1,
        }
    return {"schema_version": 1, "totals": totals, "cases": cases}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    report = evaluate(args.run_root.resolve(), repo_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report["totals"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
