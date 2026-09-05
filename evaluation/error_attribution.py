"""Attribute official step-score atoms to extraction vs KG building.

Atoms come from the same pairing and field comparison as
``evaluation.scoring_steps.score_steps_fine_grained``. Unstructured hints are
checked first with deterministic string support, then with a closed-evidence
LLM judge. Gold-hint KG reruns are intentionally out of scope.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from evaluation.normalize_steps import normalize_json_structure
from evaluation.scoring_steps import (
    VESSEL_FIELDS,
    _compare_step_fields,
    _convert_air_to_na,
    _expand_add_steps,
    _expand_add_steps_in_obj,
    _filter_out_product,
    _find_best_add_match,
    _find_best_synthesis_match,
    _is_valid,
    _match_equivalent_values,
    _normalize,
    _prefetch_score_equivalence,
    _synthesis_ccdc,
    _values_equivalent,
    score_steps_fine_grained,
)
from evaluation.utils.scoring_common import hash_map_reverse, precision_recall_f1
from evaluation.utils.step_equivalence_judge import (
    MISSING_MARKERS,
    StepEquivalenceConfig,
    StepEquivalenceJudge,
    _normalize_text,
)
from src.agents.scripts_and_prompts_generation.level1_code_repair import invoke_json


SCHEMA_VERSION = "extraction-vs-kg-attribution.v2"
VACUOUS_FALSE_FIELDS = {
    "stir",
    "isLayered",
    "sealedVessel",
    "underVacuum",
    "wait",
    "vacuumFiltration",
}
JUDGE_LABELS = {"yes", "no", "partial", "conflict", "vacuous", "n/a"}
ATTR_EXTRACTION = {
    "extraction_miss",
    "extraction_wrong",
    "extraction_hallucination",
}
ATTR_KG = {"kg_drop", "kg_corrupt", "kg_invent"}
ATTR_OTHER = {
    "both",
    "extraction_ambiguous",
    "pairing",
    "scorer_pairing",
    "representation",
    "unknown",
}
STAGE_EXTRACTION = {"extraction"}
STAGE_KG = {"kg_building"}
STAGE_OTHER = {"scorer_pairing", "representation", "unknown"}

_ALT_NAMES = re.compile(
    r"(alternative names(?: including)?[:\s]+)(.+?)(?=\.\s+The chemical |\.\s+The amount |\.\s+The supplier |\n\n|\Z)",
    re.IGNORECASE | re.DOTALL,
)
_HINT_FILE = re.compile(
    r"^(?:iter(?P<iter>[2-4])_hints_|full_hints_)(?P<entity>.+?)(?:\.pre_size_dedup)?\.txt$",
    re.IGNORECASE,
)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _norm_ph(value: Any) -> str:
    text = str(value if value is not None else "").strip().lower()
    if text in {"-1", "-1.0", "n/a", "na", ""}:
        return "n/a"
    return text


def is_vacuous_value(field: str, value: Any) -> bool:
    if value is None:
        return True
    if field == "targetPH":
        return _norm_ph(value) == "n/a"
    if isinstance(value, bool):
        return (not value) and (
            field in VACUOUS_FALSE_FIELDS or field.endswith("Filtration") or field == "wait"
        )
    text = str(value).strip().casefold()
    if text in MISSING_MARKERS or text in {"false", "0", "-1.0"}:
        return True
    return False


def _atom(
    *,
    hash_value: str,
    doi: str,
    synth_key: str,
    product_names: List[str],
    step_idx: int,
    step_type: str,
    field: str,
    gt_value: Any,
    pred_value: Any,
    status: str,
    pairing: str,
    matched: bool,
) -> Dict[str, Any]:
    return {
        "atom_id": "|".join(
            [
                hash_value,
                synth_key,
                str(step_idx),
                step_type,
                field,
                status,
                _compact(gt_value),
                _compact(pred_value),
            ]
        ),
        "hash": hash_value,
        "doi": doi,
        "synth_key": synth_key,
        "product_names": product_names,
        "step_idx": step_idx,
        "step_type": step_type,
        "field": field,
        "gt_value": gt_value,
        "pred_value": pred_value,
        "status": status,
        "pairing": pairing,
        "matched_step": matched,
        "informative": (not is_vacuous_value(field, gt_value))
        if status in {"tp", "fn"}
        else (not is_vacuous_value(field, pred_value)),
    }


def _compact(value: Any) -> str:
    text = str(value if value is not None else "")
    return re.sub(r"\s+", " ", text)[:80]


def _emit_field_atoms(
    gt_data: Dict[str, Any],
    pr_data: Dict[str, Any],
    *,
    hash_value: str,
    doi: str,
    synth_key: str,
    product_names: List[str],
    step_idx: int,
    step_type: str,
    ignore_vessel: bool,
    pairing: str,
    matched: bool,
) -> List[Dict[str, Any]]:
    atoms: List[Dict[str, Any]] = []
    common = dict(
        hash_value=hash_value,
        doi=doi,
        synth_key=synth_key,
        product_names=product_names,
        step_idx=step_idx,
        step_type=step_type,
        pairing=pairing,
        matched=matched,
    )
    all_keys = set(gt_data.keys()) | set(pr_data.keys())
    for key in all_keys:
        if key in {"comment", "stepNumber"}:
            continue
        if ignore_vessel and key in VESSEL_FIELDS:
            continue
        gt_val = gt_data.get(key)
        pr_val = pr_data.get(key)
        if key in {"addedChemical", "solvent", "washingSolvent"} and (
            isinstance(gt_val, list) or isinstance(pr_val, list)
        ):
            gt_chems = gt_val if isinstance(gt_val, list) else []
            pr_chems = pr_val if isinstance(pr_val, list) else []
            gt_names: set[str] = set()
            pr_names: set[str] = set()
            gt_amounts: set[str] = set()
            pr_amounts: set[str] = set()
            for chem in gt_chems:
                if not isinstance(chem, dict):
                    continue
                names = chem.get("chemicalName", []) or []
                if not isinstance(names, list):
                    names = [names]
                for name in names:
                    if _is_valid(name):
                        gt_names.add(_normalize(name))
                amount = chem.get("chemicalAmount")
                if _is_valid(amount):
                    gt_amounts.add(_normalize(amount))
            for chem in pr_chems:
                if not isinstance(chem, dict):
                    continue
                names = chem.get("chemicalName") or chem.get("names") or []
                if not isinstance(names, list):
                    names = [names]
                for name in names:
                    if _is_valid(name):
                        pr_names.add(_normalize(name))
                amount = chem.get("chemicalAmount") or chem.get("amount")
                if _is_valid(amount):
                    pr_amounts.add(_normalize(amount))
            if not gt_names and not pr_names:
                atoms.append(
                    _atom(
                        field=f"{key}.names",
                        gt_value="",
                        pred_value="",
                        status="tp",
                        **common,
                    )
                )
            else:
                unmatched_gt, _, _ = _match_equivalent_values(
                    gt_names, pr_names, f"{key}.names"
                )
                for name in sorted(gt_names - unmatched_gt):
                    atoms.append(
                        _atom(
                            field=f"{key}.names",
                            gt_value=name,
                            pred_value=name,
                            status="tp",
                            **common,
                        )
                    )
                for name in sorted(unmatched_gt):
                    atoms.append(
                        _atom(
                            field=f"{key}.names",
                            gt_value=name,
                            pred_value=sorted(pr_names),
                            status="fn",
                            **common,
                        )
                    )
            if not gt_amounts and not pr_amounts:
                atoms.append(
                    _atom(
                        field=f"{key}.amounts",
                        gt_value="",
                        pred_value="",
                        status="tp",
                        **common,
                    )
                )
            else:
                missing, extra, _ = _match_equivalent_values(
                    gt_amounts, pr_amounts, f"{key}.amounts"
                )
                for amount in sorted(gt_amounts - missing):
                    atoms.append(
                        _atom(
                            field=f"{key}.amounts",
                            gt_value=amount,
                            pred_value=amount,
                            status="tp",
                            **common,
                        )
                    )
                for amount in sorted(missing):
                    atoms.append(
                        _atom(
                            field=f"{key}.amounts",
                            gt_value=amount,
                            pred_value=sorted(pr_amounts),
                            status="fn",
                            **common,
                        )
                    )
                for amount in sorted(extra):
                    atoms.append(
                        _atom(
                            field=f"{key}.amounts",
                            gt_value=sorted(gt_amounts),
                            pred_value=amount,
                            status="fp",
                            **common,
                        )
                    )
            continue
        if key == "targetPH":
            gt_norm = _norm_ph(gt_val)
            pr_norm = _norm_ph(pr_val)
            if gt_norm == pr_norm:
                atoms.append(
                    _atom(
                        field=key,
                        gt_value=gt_norm,
                        pred_value=pr_norm,
                        status="tp",
                        **common,
                    )
                )
            else:
                if gt_norm != "n/a":
                    atoms.append(
                        _atom(
                            field=key,
                            gt_value=gt_val,
                            pred_value=pr_val,
                            status="fn",
                            **common,
                        )
                    )
                if pr_norm != "n/a":
                    atoms.append(
                        _atom(
                            field=key,
                            gt_value=gt_val,
                            pred_value=pr_val,
                            status="fp",
                            **common,
                        )
                    )
            continue
        if isinstance(gt_val, (bool, int, float)) or isinstance(pr_val, (bool, int, float)):
            if gt_val is not None and pr_val is not None:
                mismatch = isinstance(gt_val, bool) != isinstance(pr_val, bool) or gt_val != pr_val
                if not mismatch:
                    atoms.append(
                        _atom(
                            field=key,
                            gt_value=gt_val,
                            pred_value=pr_val,
                            status="tp",
                            **common,
                        )
                    )
                else:
                    atoms.append(
                        _atom(
                            field=key,
                            gt_value=gt_val,
                            pred_value=pr_val,
                            status="fn",
                            **common,
                        )
                    )
                    atoms.append(
                        _atom(
                            field=key,
                            gt_value=gt_val,
                            pred_value=pr_val,
                            status="fp",
                            **common,
                        )
                    )
            elif gt_val is None and pr_val is None:
                atoms.append(
                    _atom(
                        field=key,
                        gt_value=gt_val,
                        pred_value=pr_val,
                        status="tp",
                        **common,
                    )
                )
            elif gt_val is not None:
                atoms.append(
                    _atom(
                        field=key,
                        gt_value=gt_val,
                        pred_value=pr_val,
                        status="fn",
                        **common,
                    )
                )
            else:
                atoms.append(
                    _atom(
                        field=key,
                        gt_value=gt_val,
                        pred_value=pr_val,
                        status="fp",
                        **common,
                    )
                )
            continue
        if gt_val is not None and pr_val is not None:
            if _values_equivalent(key, gt_val, pr_val):
                atoms.append(
                    _atom(
                        field=key,
                        gt_value=gt_val,
                        pred_value=pr_val,
                        status="tp",
                        **common,
                    )
                )
            else:
                atoms.append(
                    _atom(
                        field=key,
                        gt_value=gt_val,
                        pred_value=pr_val,
                        status="fn",
                        **common,
                    )
                )
                atoms.append(
                    _atom(
                        field=key,
                        gt_value=gt_val,
                        pred_value=pr_val,
                        status="fp",
                        **common,
                    )
                )
        elif gt_val is None and pr_val is None:
            atoms.append(
                _atom(
                    field=key,
                    gt_value=gt_val,
                    pred_value=pr_val,
                    status="tp",
                    **common,
                )
            )
        elif gt_val is not None:
            atoms.append(
                _atom(
                    field=key,
                    gt_value=gt_val,
                    pred_value=pr_val,
                    status="fn",
                    **common,
                )
            )
        else:
            atoms.append(
                _atom(
                    field=key,
                    gt_value=gt_val,
                    pred_value=pr_val,
                    status="fp",
                    **common,
                )
            )
    return atoms


def _compare_and_collect(
    gt_data: Dict[str, Any],
    pr_data: Dict[str, Any],
    step_type: str,
    ignore_vessel: bool,
    *,
    keep: str = "all",
    **meta: Any,
) -> Tuple[int, int, int, List[Dict[str, Any]]]:
    atoms = _emit_field_atoms(
        gt_data,
        pr_data,
        step_type=step_type,
        ignore_vessel=ignore_vessel,
        **meta,
    )
    official_tp, official_fp, official_fn = _compare_step_fields(
        gt_data, pr_data, step_type, ignore_vessel
    )
    if keep == "fn":
        atoms = [atom for atom in atoms if atom["status"] == "fn"]
        official = (0, 0, official_fn)
    elif keep == "fp":
        atoms = [atom for atom in atoms if atom["status"] == "fp"]
        official = (0, official_fp, 0)
    elif keep == "all":
        official = (official_tp, official_fp, official_fn)
    else:
        raise ValueError(keep)
    tp = sum(1 for atom in atoms if atom["status"] == "tp")
    fp = sum(1 for atom in atoms if atom["status"] == "fp")
    fn = sum(1 for atom in atoms if atom["status"] == "fn")
    if (tp, fp, fn) != official:
        raise AssertionError(
            f"atom dump drifted from official scorer for {step_type}: "
            f"{(tp, fp, fn)} != {official}"
        )
    return tp, fp, fn, atoms


def collect_step_atoms(
    gt_obj: Dict[str, Any],
    pred_obj: Dict[str, Any],
    *,
    hash_value: str,
    doi: str,
    ignore_vessel: bool,
    skip_order: bool,
) -> List[Dict[str, Any]]:
    atoms: List[Dict[str, Any]] = []
    gt_synths = (gt_obj or {}).get("Synthesis", []) or []
    pr_synths = (pred_obj or {}).get("Synthesis", []) or []
    matched_pr: set[int] = set()

    def _names(synth: Dict[str, Any]) -> List[str]:
        return [str(name) for name in (synth or {}).get("productNames", []) or [] if name]

    def _key(synth: Dict[str, Any]) -> str:
        ccdc = _synthesis_ccdc(synth)
        names = _names(synth)
        return ccdc or (f"NAME:{names[0]}" if names else "NAME:<unnamed>")

    for gt_synth in gt_synths:
        product_names = _names(gt_synth)
        synth_key = _key(gt_synth)
        gt_steps = _expand_add_steps((gt_synth or {}).get("steps", []) or [])
        best_idx, best_pr = _find_best_synthesis_match(
            gt_synth,
            pr_synths,
            matched_pr,
            gt_synths=gt_synths,
        )
        pairing = "matched" if best_pr is not None else "unmatched_synthesis"
        pr_steps: List[Tuple[str, Dict[str, Any]]] = []
        if best_pr is not None:
            matched_pr.add(best_idx)
            pr_steps = _expand_add_steps((best_pr or {}).get("steps", []) or [])
        pr_matched: set[int] = set()
        meta = dict(
            hash_value=hash_value,
            doi=doi,
            synth_key=synth_key,
            product_names=product_names,
            pairing=pairing,
        )
        if skip_order:
            for index, (gt_type, gt_data) in enumerate(gt_steps):
                step_idx = index + 1
                if gt_type == "Add":
                    candidates = [
                        (idx, pr_steps[idx][1])
                        for idx in range(len(pr_steps))
                        if pr_steps[idx][0] == "Add" and idx not in pr_matched
                    ]
                    if candidates:
                        best_match, _overlap = _find_best_add_match(gt_data, candidates)
                        if best_match >= 0:
                            pr_global, pr_data = candidates[best_match]
                            pr_matched.add(pr_global)
                            _, _, _, step_atoms = _compare_and_collect(
                                gt_data,
                                pr_data,
                                "Add",
                                ignore_vessel,
                                step_idx=step_idx,
                                matched=True,
                                **meta,
                            )
                            atoms.extend(step_atoms)
                            continue
                    _, _, _, step_atoms = _compare_and_collect(
                        gt_data,
                        {},
                        "Add",
                        ignore_vessel,
                        keep="fn",
                        step_idx=step_idx,
                        matched=False,
                        **meta,
                    )
                    atoms.extend(step_atoms)
                    continue
                same_type = [
                    (idx, pr_steps[idx][1])
                    for idx in range(len(pr_steps))
                    if _normalize(pr_steps[idx][0]) == _normalize(gt_type)
                    and idx not in pr_matched
                ]
                if same_type:
                    best_local = -1
                    best_tp = -1
                    best_data = None
                    for idx, pr_data in same_type:
                        step_tp, _, _ = _compare_step_fields(
                            gt_data, pr_data, gt_type, ignore_vessel
                        )
                        if step_tp > best_tp:
                            best_tp = step_tp
                            best_local = idx
                            best_data = pr_data
                    if best_local >= 0 and best_data is not None:
                        pr_matched.add(best_local)
                        _, _, _, step_atoms = _compare_and_collect(
                            gt_data,
                            best_data,
                            gt_type,
                            ignore_vessel,
                            step_idx=step_idx,
                            matched=True,
                            **meta,
                        )
                        atoms.extend(step_atoms)
                        continue
                _, _, _, step_atoms = _compare_and_collect(
                    gt_data,
                    {},
                    gt_type,
                    ignore_vessel,
                    keep="fn",
                    step_idx=step_idx,
                    matched=False,
                    **meta,
                )
                atoms.extend(step_atoms)
            extra_offset = len(gt_steps)
            for pr_idx, (pr_type, pr_data) in enumerate(pr_steps):
                if pr_idx in pr_matched:
                    continue
                extra_offset += 1
                _, _, _, step_atoms = _compare_and_collect(
                    {},
                    pr_data,
                    pr_type,
                    ignore_vessel,
                    keep="fp",
                    step_idx=extra_offset,
                    matched=False,
                    **meta,
                )
                atoms.extend(step_atoms)
            continue
        raise NotImplementedError("positional matching is not used by the official suite")

    for idx, pr_synth in enumerate(pr_synths):
        if idx in matched_pr:
            continue
        product_names = _names(pr_synth)
        synth_key = _key(pr_synth)
        pr_steps = _expand_add_steps((pr_synth or {}).get("steps", []) or [])
        for step_idx, (pr_type, pr_data) in enumerate(pr_steps, start=1):
            _, _, _, step_atoms = _compare_and_collect(
                {},
                pr_data,
                pr_type,
                ignore_vessel,
                keep="fp",
                hash_value=hash_value,
                doi=doi,
                synth_key=synth_key,
                product_names=product_names,
                step_idx=step_idx,
                pairing="unmatched_prediction_synthesis",
                matched=False,
            )
            atoms.extend(step_atoms)
    return atoms


def merge_error_events(atoms: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups: Dict[Tuple[Any, ...], List[Dict[str, Any]]] = defaultdict(list)
    tps: List[Dict[str, Any]] = []
    for atom in atoms:
        if atom["status"] == "tp":
            tps.append(
                {
                    **atom,
                    "e2e": "tp",
                    "atom_ids": [atom["atom_id"]],
                    "event_id": f"tp:{atom['atom_id']}",
                }
            )
            continue
        groups[
            (
                atom["hash"],
                atom["synth_key"],
                atom["step_idx"],
                atom["step_type"],
                atom["field"],
            )
        ].append(atom)

    events: List[Dict[str, Any]] = []
    for key, group in groups.items():
        fns = [item for item in group if item["status"] == "fn"]
        fps = [item for item in group if item["status"] == "fp"]
        paired = min(len(fns), len(fps))
        for index in range(paired):
            base = dict(fns[index])
            base["e2e"] = "substitution"
            base["status"] = "substitution"
            base["pred_value"] = fps[index]["pred_value"]
            base["atom_ids"] = [fns[index]["atom_id"], fps[index]["atom_id"]]
            base["event_id"] = f"ev:sub:{index}:" + "|".join(str(part) for part in key)
            events.append(base)
        for item in fns[paired:]:
            base = dict(item)
            base["e2e"] = "fn"
            base["atom_ids"] = [item["atom_id"]]
            base["event_id"] = f"ev:{item['atom_id']}"
            events.append(base)
        for item in fps[paired:]:
            base = dict(item)
            base["e2e"] = "fp"
            base["atom_ids"] = [item["atom_id"]]
            base["event_id"] = f"ev:{item['atom_id']}"
            events.append(base)
    events.extend(tps)
    events.sort(key=lambda item: (item["hash"], item["synth_key"], item["step_idx"], item["field"], item["e2e"]))
    return events


def prepare_score_objects(
    gt_obj: Dict[str, Any],
    pred_obj: Dict[str, Any],
    *,
    hash_value: str,
    ignore_mode: bool,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if ignore_mode:
        gt_obj = _filter_out_product(gt_obj, "H4PBPTA")
        pred_obj = _filter_out_product(pred_obj, "H4PBPTA")
        gt_obj = _convert_air_to_na(gt_obj, hash_value)
    gt_obj = _expand_add_steps_in_obj(gt_obj)
    pred_obj = _expand_add_steps_in_obj(pred_obj)
    return normalize_json_structure(gt_obj), normalize_json_structure(pred_obj)


def _shorten_alias_wall(match: re.Match[str]) -> str:
    names = [part.strip() for part in re.split(r"\s*;\s*", match.group(2)) if part.strip()]
    kept = names[:8]
    extra = len(names) - len(kept)
    suffix = f"; … (+{extra} aliases omitted)" if extra > 0 else ""
    return match.group(1) + "; ".join(kept) + suffix


def sanitize_hints(text: str, *, max_chars: int = 14000) -> str:
    cleaned = _ALT_NAMES.sub(_shorten_alias_wall, text or "")
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[:max_chars] + "\n\n[hints truncated after sanitizing PubChem alias walls]\n"


def _entity_slug(value: str) -> str:
    return _NON_ALNUM.sub("", str(value or "").casefold())


def _read_text(path: Path) -> str:
    raw = str(path.resolve())
    if os.name == "nt" and not raw.startswith("\\\\?\\"):
        raw = "\\\\?\\" + raw
    with open(raw, encoding="utf-8", errors="replace") as handle:
        return handle.read()


def load_entity_hints(runtime_dir: Path) -> Dict[str, str]:
    mcp_run = runtime_dir / "mcp_run"
    if not mcp_run.is_dir():
        return {}
    grouped: Dict[str, Dict[str, str]] = defaultdict(dict)
    try:
        names = os.listdir(str(mcp_run))
    except OSError:
        names = [path.name for path in mcp_run.glob("*hints*.txt")]
    for name in sorted(names):
        if not name.endswith(".txt") or "hints" not in name:
            continue
        if name.endswith(".pre_size_dedup.txt"):
            continue
        match = _HINT_FILE.match(name)
        if not match:
            continue
        entity = match.group("entity")
        iteration = match.group("iter") or "full"
        try:
            grouped[entity][iteration] = _read_text(mcp_run / name)
        except OSError:
            continue
    out: Dict[str, str] = {}
    for entity, parts in grouped.items():
        chunks = []
        for key in ("2", "3", "4", "full"):
            if key not in parts:
                continue
            label = "full_hints" if key == "full" else f"iter{key}"
            chunks.append(f"===== {label} | {entity} =====\n{sanitize_hints(parts[key])}")
        out[entity] = "\n\n".join(chunks)
    return out


def select_hints_for_synthesis(
    hints_by_entity: Dict[str, str],
    product_names: Sequence[str],
    synth_key: str,
) -> Tuple[str, str]:
    if not hints_by_entity:
        return "", "missing"
    if len(hints_by_entity) == 1:
        entity, text = next(iter(hints_by_entity.items()))
        return text, f"only:{entity}"
    candidates = [_entity_slug(name) for name in product_names if name]
    if synth_key:
        candidates.append(_entity_slug(synth_key.replace("NAME:", "")))
    best_entity = ""
    best_score = 0
    for entity, _text in hints_by_entity.items():
        slug = _entity_slug(entity)
        score = 0
        for candidate in candidates:
            if not candidate:
                continue
            if candidate == slug or candidate in slug or slug in candidate:
                score = max(score, min(len(candidate), len(slug)))
        if score > best_score:
            best_score = score
            best_entity = entity
    if best_entity and best_score >= 4:
        return hints_by_entity[best_entity], f"matched:{best_entity}"
    joined = "\n\n".join(
        f"===== entity {entity} =====\n{text}" for entity, text in hints_by_entity.items()
    )
    return joined, "union_fallback"


def _search_haystack(text: str) -> str:
    return _NON_ALNUM.sub("", (text or "").casefold())


def deterministic_support(value: Any, hints: str, *, field: str) -> Optional[str]:
    if value is None or value == "" or value == []:
        return "vacuous" if is_vacuous_value(field, value) else None
    if is_vacuous_value(field, value):
        return "vacuous"
    raw = value if not isinstance(value, list) else " ".join(str(item) for item in value)
    text = str(raw).strip()
    if not text or not hints:
        return None
    haystack = _search_haystack(hints)
    needle = _search_haystack(text)
    if len(needle) >= 3 and needle in haystack:
        return "yes"
    kind = "quantity" if "amount" in field or field in {
        "duration",
        "targetTemperature",
        "heatingCoolingRate",
        "chemicalAmount",
    } else "chemical_name" if "name" in field else "qualitative"
    normalized = _search_haystack(_normalize_text(text, kind))
    if len(normalized) >= 3 and normalized in haystack:
        return "yes"
    tokens = [tok for tok in re.findall(r"[a-z0-9.]{3,}", text.casefold())]
    if tokens and all(_search_haystack(tok) in haystack for tok in tokens):
        return "yes"
    return None


def _step_mentioned(step_type: str, hints: str) -> bool:
    if not hints:
        return False
    aliases = {
        "Add": ["add step", "addition of", "was added", "is added", "chemicalinput"],
        "HeatChill": ["heatchill", "heated", "heating", "cooled", "cooling"],
        "Sonicate": ["sonicat"],
        "Stir": ["stir"],
        "Filter": ["filter"],
        "Wash": ["wash"],
        "Dry": ["dried", "drying"],
        "Evaporate": ["evaporat"],
        "Transfer": ["transfer"],
        "Crystallize": ["crystall"],
    }
    blob = hints.casefold()
    if step_type.casefold() in blob:
        return True
    return any(alias in blob for alias in aliases.get(step_type, []))


def attribute_event(
    event: Dict[str, Any],
    gt_in_hints: str,
    pred_in_hints: str,
) -> str:
    if event.get("pairing") == "unmatched_synthesis":
        if event.get("e2e") in {"fn", "substitution"} and gt_in_hints == "yes":
            return "pairing"
        if event.get("e2e") == "fn" and gt_in_hints != "yes":
            return "extraction_miss"
    if event.get("pairing") == "unmatched_prediction_synthesis":
        if pred_in_hints == "yes":
            return "extraction_hallucination"
        return "kg_invent"
    e2e = event.get("e2e")
    if e2e == "tp":
        return "ok"
    if e2e == "fn":
        if gt_in_hints == "yes":
            return "kg_drop"
        if gt_in_hints == "partial":
            return "extraction_wrong"
        if gt_in_hints == "conflict":
            return "extraction_ambiguous"
        if gt_in_hints == "unknown":
            return "unknown"
        return "extraction_miss"
    if e2e == "fp":
        if pred_in_hints in {"yes", "partial"}:
            return "extraction_hallucination"
        if pred_in_hints == "unknown":
            return "unknown"
        return "kg_invent"
    if e2e == "substitution":
        if gt_in_hints == "conflict" or pred_in_hints == "conflict":
            return "extraction_ambiguous"
        if gt_in_hints == "yes" and pred_in_hints == "yes":
            return "extraction_ambiguous"
        if gt_in_hints == "yes" and pred_in_hints != "yes":
            return "kg_corrupt"
        if gt_in_hints != "yes" and pred_in_hints == "yes":
            return "extraction_wrong"
        if gt_in_hints == "unknown" or pred_in_hints == "unknown":
            return "unknown"
        return "both"
    return "unknown"


def _quote_in_hints(quote: str, hints: str) -> bool:
    if not quote or not hints:
        return False
    compact_quote = re.sub(r"\s+", "", quote.casefold())
    compact_hints = re.sub(r"\s+", "", hints.casefold())
    return len(compact_quote) >= 8 and compact_quote in compact_hints


def _judge_prompt(events: Sequence[Dict[str, Any]], hints: str) -> str:
    payload = []
    for event in events:
        payload.append(
            {
                "atom_id": event["event_id"],
                "step_type": event["step_type"],
                "step_idx": event["step_idx"],
                "field": event["field"],
                "gt_value": event.get("gt_value"),
                "pred_value": event.get("pred_value"),
                "e2e": event.get("e2e"),
            }
        )
    return f"""You are scoring whether unstructured extraction hints already contain official scorer atoms.

CLOSED EVIDENCE: use ONLY the hints below. Do not use the paper, chemistry knowledge, or the knowledge graph. If the hints do not state a fact, answer no.

For each atom:
- gt_in_hints: does the hints support the GT value?
- pred_in_hints: does the hints support the prediction value?
Allowed labels: yes, no, partial, conflict, vacuous, n/a
- yes: semantically equivalent value is explicitly present. Must include a verbatim evidence_quote from the hints.
- partial: related but incomplete (e.g. mass without mmol when GT has both).
- conflict: hints contain two incompatible values for this field.
- vacuous: the value is an empty/default marker (n/a, -1, false) and no positive mention is required.
- n/a: use pred_in_hints=n/a when there is no prediction value to check.
- no: not supported.

Equivalence: unit spelling (degC / degree celsius), 17.5 mg vs 17.50 mg, four drops vs 4 drops, common chemical synonyms (DMF = N,N-dimethylformamide, Cp2ZrCl2 = zirconocene dichloride) count as yes only if the hints actually say that synonym.
Do not credit PubChem CID dumps unless they name the queried species.
Do not invent quotes.

Return one JSON object:
{{"judgements": [{{"atom_id": "...", "gt_in_hints": "yes|no|partial|conflict|vacuous|n/a", "pred_in_hints": "yes|no|partial|conflict|vacuous|n/a", "hint_value": "", "evidence_quote": "", "confidence": 0.0}}]}}

HINTS:
{hints}

ATOMS:
{json.dumps(payload, ensure_ascii=True, indent=2)}
"""


def judge_hint_support(
    events: Sequence[Dict[str, Any]],
    hints: str,
    *,
    model: str,
    timeout_seconds: int = 300,
    chunk_size: int = 16,
) -> Dict[str, Dict[str, Any]]:
    if not events:
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    pending = list(events)
    for start in range(0, len(pending), max(1, chunk_size)):
        chunk = pending[start : start + max(1, chunk_size)]
        result = invoke_json(
            model,
            _judge_prompt(chunk, hints),
            timeout_seconds=timeout_seconds,
            max_attempts=3,
            temperature=0.0,
        )
        rows = result.data.get("judgements")
        if not isinstance(rows, list):
            raise RuntimeError("hint-support judge did not return judgements[]")
        out.update(_parse_judgements(rows, hints, model))
    return out


def _parse_judgements(
    rows: Sequence[Any],
    hints: str,
    model: str,
) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        atom_id = str(row.get("atom_id") or "")
        if not atom_id:
            continue
        gt_label = str(row.get("gt_in_hints") or "unknown").strip().casefold()
        pred_label = str(row.get("pred_in_hints") or "unknown").strip().casefold()
        quote = str(row.get("evidence_quote") or "").strip()
        if gt_label == "yes" and not _quote_in_hints(quote, hints):
            gt_label = "unknown"
        if pred_label == "yes" and not _quote_in_hints(quote, hints):
            pred_label = "unknown"
        if gt_label not in JUDGE_LABELS:
            gt_label = "unknown"
        if pred_label not in JUDGE_LABELS:
            pred_label = "unknown"
        out[atom_id] = {
            "gt_in_hints": gt_label,
            "pred_in_hints": pred_label,
            "hint_value": row.get("hint_value"),
            "evidence_quote": quote,
            "confidence": row.get("confidence"),
            "source": "llm",
            "model": model,
        }
    return out


def apply_support_and_attribute(
    events: List[Dict[str, Any]],
    hints: str,
    judgements: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    by_step: Dict[Tuple[Any, ...], List[Dict[str, Any]]] = defaultdict(list)
    for event in events:
        by_step[(event["hash"], event["synth_key"], event["step_idx"], event["step_type"])].append(event)

    for event in events:
        det_gt = deterministic_support(event.get("gt_value"), hints, field=event["field"])
        det_pred = deterministic_support(event.get("pred_value"), hints, field=event["field"])
        judged = judgements.get(event["event_id"], {})
        gt_label = judged.get("gt_in_hints") or det_gt or "unknown"
        pred_label = judged.get("pred_in_hints") or det_pred or "unknown"
        if det_gt == "yes" and gt_label == "no":
            gt_label = "yes"
            event["support_override"] = "deterministic_yes"
        if det_pred == "yes" and pred_label == "no":
            pred_label = "yes"
            event["support_override"] = "deterministic_yes"
        if event.get("e2e") == "tp" and det_gt == "yes":
            gt_label = "yes"
            pred_label = "yes" if det_pred in {None, "yes", "vacuous"} else pred_label
        if event.get("e2e") == "fn" and not event.get("informative"):
            present = _step_mentioned(event["step_type"], hints)
            gt_label = "yes" if present else "no"
            event["support_inherited"] = "step_presence"
        event["gt_in_hints"] = gt_label
        event["pred_in_hints"] = pred_label
        event["support_source"] = judged.get("source") or (
            "deterministic" if det_gt or det_pred else "none"
        )
        if judged.get("evidence_quote"):
            event["evidence_quote"] = judged["evidence_quote"]
        event["attribution"] = attribute_event(event, gt_label, pred_label)
    return events


def events_needing_llm(events: Sequence[Dict[str, Any]], hints: str) -> List[Dict[str, Any]]:
    needed: List[Dict[str, Any]] = []
    for event in events:
        if event.get("e2e") == "tp" and not event.get("informative"):
            continue
        if event.get("e2e") == "fn" and not event.get("informative"):
            continue
        det_gt = deterministic_support(event.get("gt_value"), hints, field=event["field"])
        if event.get("e2e") == "tp" and det_gt == "yes":
            continue
        if event.get("e2e") in {"fn", "fp", "substitution"}:
            needed.append(event)
            continue
        if event.get("e2e") == "tp" and event.get("informative") and det_gt != "yes":
            needed.append(event)
    return needed


def summarize(events: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    errors = [event for event in events if event.get("e2e") != "tp"]
    counts = Counter(event.get("attribution") for event in errors)
    n_err = len(errors)
    extraction_n = sum(counts[label] for label in ATTR_EXTRACTION)
    kg_n = sum(counts[label] for label in ATTR_KG)
    other_n = n_err - extraction_n - kg_n
    gt_atoms = [
        event
        for event in events
        if event.get("e2e") in {"tp", "fn", "substitution"}
    ]
    informative = [event for event in gt_atoms if event.get("informative")]
    supported = [event for event in informative if event.get("gt_in_hints") == "yes"]
    hints_ok = [
        event
        for event in informative
        if event.get("gt_in_hints") == "yes" and event.get("e2e") in {"tp", "fn", "substitution"}
    ]
    kg_kept = [event for event in hints_ok if event.get("e2e") == "tp"]
    informative_errors = [event for event in errors if event.get("informative")]
    informative_counts = Counter(event.get("attribution") for event in informative_errors)
    n_info_err = len(informative_errors)
    official_tp = sum(1 for event in events if event["e2e"] == "tp")
    official_fp = sum(1 for event in events if event["e2e"] == "fp")
    official_fn = sum(1 for event in events if event["e2e"] == "fn")
    official_sub = sum(1 for event in events if event["e2e"] == "substitution")
    # substitution is 1 FN + 1 FP in the official scorer
    official_fp += official_sub
    official_fn += official_sub
    precision, recall, f1 = precision_recall_f1(official_tp, official_fp, official_fn)
    return {
        "official": {
            "tp": official_tp,
            "fp": official_fp,
            "fn": official_fn,
            "substitutions": official_sub,
            "precision": precision,
            "recall": recall,
            "f1": f1,
        },
        "error_events": n_err,
        "attribution_counts": dict(counts),
        "share": {
            "extraction": extraction_n / n_err if n_err else 0.0,
            "kg": kg_n / n_err if n_err else 0.0,
            "other": other_n / n_err if n_err else 0.0,
        },
        "informative_error_events": n_info_err,
        "informative_attribution_counts": dict(informative_counts),
        "informative_share": {
            "extraction": sum(informative_counts[label] for label in ATTR_EXTRACTION) / n_info_err
            if n_info_err
            else 0.0,
            "kg": sum(informative_counts[label] for label in ATTR_KG) / n_info_err
            if n_info_err
            else 0.0,
        },
        "extraction_informative_recall": (
            len(supported) / len(informative) if informative else 0.0
        ),
        "informative_gt_atoms": len(informative),
        "informative_supported": len(supported),
        "kg_fidelity_given_hints_ok": (
            len(kg_kept) / len(hints_ok) if hints_ok else None
        ),
        "by_field": _by_field(errors),
    }


def _by_field(errors: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, int]]:
    table: Dict[str, Counter[str]] = defaultdict(Counter)
    for event in errors:
        table[str(event.get("field"))][str(event.get("attribution"))] += 1
    return {field: dict(counter) for field, counter in sorted(table.items())}


def load_trimmed_paper(hash_value: str, paper_root: Path | None, runtime_dir: Path) -> Tuple[str, str]:
    """Load the shortened paper MD (eval30_md style: text + one SI copy)."""
    candidates: List[Path] = []
    if paper_root is not None:
        candidates.append(Path(paper_root) / hash_value)
    candidates.append(runtime_dir)
    for folder in candidates:
        if not folder.is_dir():
            continue
        text_path = folder / f"{hash_value}_text.md"
        body_path = folder / f"{hash_value}.md"
        si_path = folder / f"{hash_value}_si.md"
        si_text = folder / f"{hash_value}_si_text.md"
        chunks: List[str] = []
        source_bits: List[str] = []
        main = text_path if text_path.is_file() else body_path
        if main.is_file():
            chunks.append(_read_text(main))
            source_bits.append(main.name)
        si = si_text if si_text.is_file() else si_path
        if si.is_file():
            chunks.append(_read_text(si))
            source_bits.append(si.name)
        if chunks:
            return "\n\n".join(chunks), "+".join(source_bits)
    return "", "missing"


def excerpt_paper(paper: str, queries: Sequence[Any], *, window: int = 280) -> str:
    if not paper:
        return ""
    hits: List[str] = []
    seen: set[str] = set()
    for raw in queries:
        if raw is None or raw == "" or raw == []:
            continue
        text = str(raw).strip()
        if len(text) < 3:
            continue
        needles = [text]
        needles.extend(re.findall(r"[A-Za-z0-9][A-Za-z0-9'./-]{2,}", text))
        for needle in needles:
            if len(needle) < 3:
                continue
            index = paper.casefold().find(needle.casefold())
            if index < 0:
                continue
            start = max(0, index - window)
            end = min(len(paper), index + len(needle) + window)
            snippet = re.sub(r"\s+", " ", paper[start:end]).strip()
            if snippet and snippet not in seen:
                seen.add(snippet)
                hits.append(snippet)
            if len(hits) >= 3:
                return "\n---\n".join(hits)
    return "\n---\n".join(hits)


def _value_bag(value: Any) -> set[str]:
    if value is None or value == "" or value == []:
        return set()
    if isinstance(value, list):
        return {str(item).strip() for item in value if str(item).strip()}
    return {str(value).strip()}


def _equivalent_bags(left: Any, right: Any, field: str) -> bool:
    left_bag = _value_bag(left)
    right_bag = _value_bag(right)
    if not left_bag or not right_bag:
        return False
    if left_bag & right_bag:
        return True
    left_cf = {item.casefold() for item in left_bag}
    right_cf = {item.casefold() for item in right_bag}
    if left_cf & right_cf:
        return True
    for left_item in left_bag:
        for right_item in right_bag:
            if _values_equivalent(field, left_item, right_item):
                return True
    return False


def _distinctive_amount(value: Any) -> bool:
    text = f" {str(value or '').casefold()} "
    return any(token in text for token in (" mmol", " mol", " mg", " g ", "gram"))


def _linkable_pairing(fn_event: Dict[str, Any], fp_event: Dict[str, Any]) -> bool:
    if fn_event.get("synth_key") != fp_event.get("synth_key"):
        return False
    if fn_event.get("step_type") != fp_event.get("step_type"):
        return False
    fn_field = str(fn_event.get("field") or "")
    fp_field = str(fp_event.get("field") or "")
    if fn_field.endswith(".amounts") and fp_field.endswith(".amounts"):
        if not _distinctive_amount(fn_event.get("gt_value")) and not _distinctive_amount(
            fp_event.get("pred_value")
        ):
            return False
        return _equivalent_bags(fn_event.get("gt_value"), fp_event.get("pred_value"), fn_field)
    if fn_field.endswith(".names") and fp_field.endswith(".names"):
        return _equivalent_bags(fn_event.get("gt_value"), fp_event.get("pred_value"), fn_field)
    return False


def cluster_incidents(events: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Collapse official atoms into diagnostic incidents.

    Vacuous companions of an unmatched step are not separate errors.
    An unmatched GT Add and unmatched pred Add that share an equivalent
    amount or name become one scorer_pairing incident.
    """
    errors = [event for event in events if event.get("e2e") != "tp"]
    used: set[str] = set()
    incidents: List[Dict[str, Any]] = []

    def _eid(event: Dict[str, Any]) -> str:
        return str(event.get("event_id") or event.get("atom_id"))

    informative = [event for event in errors if event.get("informative")]
    fns = [
        event
        for event in informative
        if event.get("e2e") == "fn" and not event.get("matched_step")
    ]
    fps = [
        event
        for event in informative
        if event.get("e2e") == "fp" and not event.get("matched_step")
    ]
    for fn_event in fns:
        if _eid(fn_event) in used:
            continue
        for fp_event in fps:
            if _eid(fp_event) in used:
                continue
            if not _linkable_pairing(fn_event, fp_event):
                continue
            members: List[Dict[str, Any]] = []
            for event in errors:
                same_fn_step = (
                    event.get("synth_key") == fn_event.get("synth_key")
                    and event.get("step_idx") == fn_event.get("step_idx")
                    and event.get("step_type") == fn_event.get("step_type")
                )
                same_fp_step = (
                    event.get("synth_key") == fp_event.get("synth_key")
                    and event.get("step_idx") == fp_event.get("step_idx")
                    and event.get("step_type") == fp_event.get("step_type")
                )
                if (same_fn_step or same_fp_step) and _eid(event) not in used:
                    members.append(event)
            if fn_event not in members:
                members.insert(0, fn_event)
            if fp_event not in members:
                members.append(fp_event)
            for member in members:
                used.add(_eid(member))
            incidents.append(
                _incident(
                    "scorer_pairing",
                    members,
                    title=f"{fn_event.get('step_type')} GT#{fn_event.get('step_idx')} vs pred#{fp_event.get('step_idx')}",
                    should_have=fn_event.get("gt_value"),
                )
            )
            break

    leftover = [event for event in errors if _eid(event) not in used]
    grouped: Dict[Tuple[Any, ...], List[Dict[str, Any]]] = defaultdict(list)
    for event in leftover:
        grouped[
            (
                event.get("synth_key"),
                event.get("step_idx"),
                event.get("step_type"),
                bool(event.get("matched_step")),
            )
        ].append(event)

    for (synth, step_idx, step_type, matched), group in grouped.items():
        info = [event for event in group if event.get("informative")]
        if not info:
            continue
        if not matched:
            stage = _majority_stage(info)
            should = next(
                (event.get("gt_value") for event in info if event.get("e2e") == "fn"),
                next((event.get("pred_value") for event in info if event.get("e2e") == "fp"), None),
            )
            incidents.append(
                _incident(
                    stage,
                    group,
                    title=f"{step_type} #{step_idx} unmatched",
                    should_have=should,
                )
            )
            continue
        for event in info:
            incidents.append(
                _incident(
                    _majority_stage([event]),
                    [event],
                    title=f"{step_type} #{step_idx} {event.get('field')}",
                    should_have=event.get("gt_value")
                    if event.get("e2e") in {"fn", "substitution"}
                    else event.get("pred_value"),
                )
            )
    incidents = _merge_workup_incidents(incidents)
    incidents.sort(key=lambda item: (item.get("synth_key"), item.get("step_idx"), item.get("title")))
    return incidents


_WORKUP_TYPES = {
    "Stir",
    "Filter",
    "Wash",
    "Dry",
    "Evaporate",
    "Transfer",
    "Sonicate",
    "Crystallize",
}


def _merge_workup_incidents(incidents: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[Tuple[Any, ...], List[Dict[str, Any]]] = defaultdict(list)
    leftover: List[Dict[str, Any]] = []
    for item in incidents:
        title = str(item.get("title") or "")
        if item.get("step_type") in _WORKUP_TYPES and "unmatched" in title:
            grouped[(item.get("synth_key"),)].append(item)
        else:
            leftover.append(item)
    merged = list(leftover)
    for _key, group in grouped.items():
        if len(group) == 1:
            merged.append(group[0])
            continue
        ordered = sorted(group, key=lambda item: item.get("step_idx") or 0)
        first = dict(ordered[0])
        first["title"] = "workup unmatched"
        first["step_type"] = "Workup"
        labels = [
            label
            for item in ordered
            for label in item.get("attributions") or [item.get("stage")]
        ]
        first["stage"] = (
            "extraction"
            if any(label in ATTR_EXTRACTION for label in labels)
            else _majority_stage(
                [{"attribution": label, "informative": True} for label in labels]
            )
        )
        first["cluster_stage"] = first["stage"]
        first["step_idx"] = ordered[0].get("step_idx")
        first["should_have_been"] = "; ".join(
            f"{item.get('step_type')} #{item.get('step_idx')}: {item.get('should_have_been')}"
            for item in ordered
        )
        first["member_ids"] = [
            mid for item in ordered for mid in (item.get("member_ids") or [])
        ]
        first["n_atoms"] = sum(int(item.get("n_atoms") or 0) for item in ordered)
        first["n_informative"] = sum(int(item.get("n_informative") or 0) for item in ordered)
        first["fields"] = sorted({field for item in ordered for field in item.get("fields") or []})
        first["attributions"] = sorted(
            {label for item in ordered for label in item.get("attributions") or []}
        )
        first["gt_values"] = [value for item in ordered for value in (item.get("gt_values") or [])]
        first["pred_values"] = [
            value for item in ordered for value in (item.get("pred_values") or [])
        ]
        first["gt_in_hints"] = sorted(
            {label for item in ordered for label in item.get("gt_in_hints") or []}
        )
        first["pred_in_hints"] = sorted(
            {label for item in ordered for label in item.get("pred_in_hints") or []}
        )
        first["incident_id"] = "inc:workup:" + "|".join(
            _compact(item.get("incident_id")) for item in ordered
        )[:160]
        merged.append(first)
    return merged


def _majority_stage(events: Sequence[Dict[str, Any]]) -> str:
    labels = [str(event.get("attribution") or "unknown") for event in events]
    if any(label in ATTR_EXTRACTION for label in labels) and not any(
        label in ATTR_KG for label in labels
    ):
        return "extraction"
    if any(label in ATTR_KG for label in labels) and not any(
        label in ATTR_EXTRACTION for label in labels
    ):
        return "kg_building"
    if any(label == "pairing" or label == "scorer_pairing" for label in labels):
        return "scorer_pairing"
    if any(label in ATTR_EXTRACTION for label in labels) and any(
        label in ATTR_KG for label in labels
    ):
        return "both"
    return labels[0] if labels else "unknown"


def _incident(
    stage: str,
    members: Sequence[Dict[str, Any]],
    *,
    title: str,
    should_have: Any,
) -> Dict[str, Any]:
    first = members[0]
    return {
        "incident_id": "inc:" + "|".join(_compact(member.get("event_id")) for member in members)[:180],
        "hash": first.get("hash"),
        "synth_key": first.get("synth_key"),
        "product_names": first.get("product_names") or [],
        "step_idx": first.get("step_idx"),
        "step_type": first.get("step_type"),
        "title": title,
        "stage": stage,
        "should_have_been": should_have,
        "member_ids": [member.get("event_id") for member in members],
        "n_atoms": len(members),
        "n_informative": sum(1 for member in members if member.get("informative")),
        "fields": sorted({str(member.get("field")) for member in members}),
        "cluster_stage": stage,
        "attributions": sorted({str(member.get("attribution")) for member in members}),
        "gt_values": [member.get("gt_value") for member in members if member.get("informative")],
        "pred_values": [member.get("pred_value") for member in members if member.get("informative")],
        "gt_in_hints": sorted({str(member.get("gt_in_hints")) for member in members}),
        "pred_in_hints": sorted({str(member.get("pred_in_hints")) for member in members}),
    }


def _diagnosis_prompt(incidents: Sequence[Dict[str, Any]], hints: str) -> str:
    payload = []
    for item in incidents:
        payload.append(
            {
                "incident_id": item["incident_id"],
                "title": item.get("title"),
                "provisional_stage": item.get("stage"),
                "should_have_been": item.get("should_have_been"),
                "gt_values": item.get("gt_values"),
                "pred_values": item.get("pred_values"),
                "fields": item.get("fields"),
                "gt_in_hints": item.get("gt_in_hints"),
                "pred_in_hints": item.get("pred_in_hints"),
                "paper_excerpt": item.get("paper_excerpt") or "",
                "abox_wrote": item.get("abox_wrote") or "",
                "diagnosis_note": item.get("diagnosis_note") or "",
            }
        )
    return f"""You diagnose official step-score errors for a paper → extraction hints → KG A-Box pipeline.

The user needs four things per incident:
1. Where did the official error come from?
2. What specifically went wrong?
3. What should the structured answer have been?
4. A short paper quote that supports that correct answer.

Definitions:
- PAPER excerpt + GT value = what is correct in the source. Prefer the paper wording; GT is the official eval target.
- HINTS = what extraction already wrote. Never credit extraction from the paper or from chemistry knowledge.
- PRED = what the KG / converted A-Box wrote.

Stages (pick one):
- extraction: hints omit a paper/GT fact, or invent a fact the paper does not state.
- kg_building: hints already have the fact, but the A-Box dropped, corrupted, or invented it.
- scorer_pairing: same chemical/step is in GT and A-Box (possibly via a paper alias) but the official scorer split it into unmatched FN+FP. If diagnosis_note or abox_wrote is present, trust that over pred_values. Do not claim the amount was written into atmosphere or duration: those are unmatched-step defaults.
- representation: same fact, different schema slot (Add vs washingSolvent, [Cu2] vs CuCl2·2H2O as the Cu source).
- gt_gap: paper states the predicted fact (e.g. an "or 1 day on a nutator" alternative) but GT omitted it. Official score then punishes a paper-true extra step.
- both: extraction and KG each failed in different ways.

Alias rule: if the PAPER excerpt equates two names (H2SDB = 4,4'-sulfonyldibenzoic acid; TMTAH3 = trimesoyltri(L-alanine)), that is not an extraction miss.

should_have_been must be the concrete structured fact (step type, chemical, amount, time), not a process comment.
paper_quote must be a short span copied from the paper excerpt when available.
extraction_had / kg_wrote: what that layer actually contained, or "absent".

Return JSON:
{{"diagnoses": [{{"incident_id": "...", "stage": "extraction|kg_building|scorer_pairing|representation|gt_gap|both", "problem": "one sentence", "should_have_been": "the correct structured fact", "paper_quote": "", "extraction_had": "", "kg_wrote": "", "fix": "what to change"}}]}}

HINTS:
{hints}

INCIDENTS:
{json.dumps(payload, ensure_ascii=True, indent=2)}
"""


def _same_mmol(left: Any, right: Any) -> bool:
    def _mmol(value: Any) -> float | None:
        match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*mmol", str(value or "").casefold())
        return float(match.group(1)) if match else None

    left_n = _mmol(left)
    right_n = _mmol(right)
    return left_n is not None and right_n is not None and abs(left_n - right_n) < 1e-9


def describe_pred_add_with_amount(pred_obj: Dict[str, Any], amount: Any) -> str:
    """Find the A-Box Add that carries this amount, including names the scorer ignores."""
    if amount is None or amount == "" or amount == []:
        return ""
    target = str(amount)
    for synth in (pred_obj or {}).get("Synthesis", []) or []:
        for index, (step_type, data) in enumerate(
            _expand_add_steps((synth or {}).get("steps", []) or []),
            start=1,
        ):
            if step_type != "Add" or not isinstance(data, dict):
                continue
            chems = data.get("addedChemical") or []
            if not isinstance(chems, list):
                continue
            for chem in chems:
                if not isinstance(chem, dict):
                    continue
                raw_amount = chem.get("chemicalAmount") or chem.get("amount")
                if not _is_valid(raw_amount):
                    continue
                if not (
                    _equivalent_bags(target, raw_amount, "addedChemical.amounts")
                    or _search_haystack(raw_amount) == _search_haystack(target)
                    or _same_mmol(target, raw_amount)
                ):
                    continue
                names = chem.get("chemicalName") or chem.get("names") or []
                if not isinstance(names, list):
                    names = [names]
                kept = [str(name) for name in names if _is_valid(name)][:4]
                return (
                    f"Add #{index}: names={kept or ['(none)']}; amount={raw_amount}"
                )
    return ""


def annotate_pairing_incidents(
    incidents: List[Dict[str, Any]], pred_obj: Dict[str, Any]
) -> None:
    for item in incidents:
        if item.get("cluster_stage") != "scorer_pairing" and "vs pred#" not in str(
            item.get("title") or ""
        ):
            continue
        item["cluster_stage"] = "scorer_pairing"
        item["stage"] = "scorer_pairing"
        amount = next(
            (
                value
                for value in (item.get("gt_values") or [])
                if _distinctive_amount(value)
            ),
            item.get("should_have_been"),
        )
        abox = describe_pred_add_with_amount(pred_obj, amount)
        item["abox_wrote"] = abox
        item["diagnosis_note"] = (
            "Official scoring does not emit extra predicted chemical names as FP "
            "(PubChem alias walls). Vacuous atmosphere/duration/stir on an unmatched "
            "Add are schema defaults, not evidence that the amount was written into "
            "those fields. The A-Box already has this Add: "
            + (abox or "same distinctive amount as GT.")
        )


def diagnose_incidents(
    incidents: List[Dict[str, Any]],
    hints: str,
    paper: str,
    *,
    model: str,
    timeout_seconds: int = 300,
) -> List[Dict[str, Any]]:
    if not incidents:
        return []
    for item in incidents:
        queries = [
            item.get("should_have_been"),
            *(item.get("gt_values") or []),
            *(item.get("pred_values") or []),
            *(item.get("product_names") or []),
        ]
        item["paper_excerpt"] = excerpt_paper(paper, queries)
    result = invoke_json(
        model,
        _diagnosis_prompt(incidents, hints),
        timeout_seconds=timeout_seconds,
        max_attempts=3,
        temperature=0.0,
    )
    rows = result.data.get("diagnoses")
    if not isinstance(rows, list):
        raise RuntimeError("diagnosis judge did not return diagnoses[]")
    by_id = {str(row.get("incident_id")): row for row in rows if isinstance(row, dict)}
    allowed = {
        "extraction",
        "kg_building",
        "scorer_pairing",
        "representation",
        "gt_gap",
        "both",
    }
    for item in incidents:
        row = by_id.get(str(item["incident_id"]), {})
        stage = str(row.get("stage") or item.get("stage") or "unknown").strip()
        if item.get("cluster_stage") == "scorer_pairing":
            stage = "scorer_pairing"
        elif stage not in allowed:
            stage = item.get("stage") or "unknown"
        item["stage"] = stage
        if item.get("cluster_stage") == "scorer_pairing" and item.get("abox_wrote"):
            if not str(row.get("kg_wrote") or "").strip() or any(
                token in str(row.get("kg_wrote") or "").casefold()
                for token in ("atmosphere", "duration field", "written as duration")
            ):
                row = dict(row)
                row["kg_wrote"] = item["abox_wrote"]
        item["problem"] = str(row.get("problem") or "").strip()
        item["should_have_been"] = row.get("should_have_been") or item.get("should_have_been")
        item["paper_quote"] = str(row.get("paper_quote") or "").strip()
        item["extraction_had"] = str(row.get("extraction_had") or "").strip()
        item["kg_wrote"] = str(row.get("kg_wrote") or "").strip()
        item["fix"] = str(row.get("fix") or "").strip()
        if item.get("cluster_stage") == "scorer_pairing":
            messy = item["problem"].casefold()
            if any(
                token in messy
                for token in (
                    "atmosphere",
                    "duration field",
                    "written as duration",
                    "dropped the chemical",
                    "omitted the addedchemical",
                )
            ):
                item["problem"] = (
                    "GT and the A-Box both contain this addition (paper alias / same amount), "
                    "but the official scorer did not pair the steps, so it reports FN+FP."
                )
            if item.get("abox_wrote") and (
                not item["kg_wrote"]
                or any(
                    token in item["kg_wrote"].casefold()
                    for token in ("atmosphere", "duration field", "written as duration")
                )
            ):
                item["kg_wrote"] = item["abox_wrote"]
            if not item.get("fix"):
                item["fix"] = (
                    "Teach the official synonym/pairing judge the paper alias "
                    "(or emit the GT name as an additional chemicalName)."
                )
    return incidents


def summarize_incidents(incidents: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    counts = Counter(str(item.get("stage") or "unknown") for item in incidents)
    n = len(incidents)
    return {
        "n_incidents": n,
        "stage_counts": dict(counts),
        "share": {
            "extraction": counts["extraction"] / n if n else 0.0,
            "kg_building": counts["kg_building"] / n if n else 0.0,
            "scorer_pairing": counts["scorer_pairing"] / n if n else 0.0,
            "representation": counts["representation"] / n if n else 0.0,
            "gt_gap": counts["gt_gap"] / n if n else 0.0,
            "both": counts["both"] / n if n else 0.0,
        },
    }


def render_report(payload: Dict[str, Any]) -> str:
    lines = [
        "# Extraction vs KG error diagnosis",
        "",
        f"Schema: `{payload.get('schema_version')}`",
        f"Judge model: `{payload.get('judge_model')}`",
        f"Paper source: `{payload.get('paper_root')}`",
        f"Run: `{payload.get('pred_root')}`",
        "",
        "Shares below are **diagnostic incidents**, not raw official FP/FN atoms. "
        "Default `n/a`/`false` companions of an unmatched step are folded in. "
        "An unmatched GT Add and unmatched pred Add that carry the same amount/name are one `scorer_pairing` incident.",
        "",
        "## Overall",
        "",
    ]
    overall = payload.get("overall") or {}
    official = overall.get("official") or {}
    incident_summary = payload.get("incident_summary") or {}
    share = incident_summary.get("share") or {}
    lines.extend(
        [
            f"- Official steps: TP={official.get('tp')} FP={official.get('fp')} FN={official.get('fn')} F1={official.get('f1')}",
            f"- Diagnostic incidents: {incident_summary.get('n_incidents')}",
            f"- Extraction: {share.get('extraction', 0):.3f}",
            f"- KG building: {share.get('kg_building', 0):.3f}",
            f"- Scorer pairing: {share.get('scorer_pairing', 0):.3f}",
            f"- Representation: {share.get('representation', 0):.3f}",
            f"- GT gap (paper-true, gold omitted): {share.get('gt_gap', 0):.3f}",
            f"- Both: {share.get('both', 0):.3f}",
            "",
            "### Incident stages",
            "",
            "| Stage | N |",
            "| --- | ---: |",
        ]
    )
    for label, count in sorted((incident_summary.get("stage_counts") or {}).items()):
        lines.append(f"| {label} | {count} |")
    lines.extend(["", "## Diagnoses", ""])
    for paper in payload.get("papers") or []:
        inc_sum = paper.get("incident_summary") or {}
        inc_share = inc_sum.get("share") or {}
        lines.extend(
            [
                f"### `{paper.get('hash')}`",
                "",
                f"Official F1 {((paper.get('summary') or {}).get('official') or {}).get('f1')}; "
                f"paper `{paper.get('paper_source')}`; "
                f"incidents {inc_sum.get('n_incidents')} "
                f"(extraction {inc_share.get('extraction', 0):.2f}, "
                f"KG {inc_share.get('kg_building', 0):.2f}, "
                f"pairing {inc_share.get('scorer_pairing', 0):.2f}).",
                "",
            ]
        )
        for item in paper.get("incidents") or []:
            lines.extend(
                [
                    f"#### {item.get('title')}",
                    "",
                    f"- **Stage**: `{item.get('stage')}`",
                    f"- **Problem**: {item.get('problem') or '(undetected)'}",
                    f"- **Should have been**: {item.get('should_have_been')}",
                    f"- **Paper**: {item.get('paper_quote') or item.get('paper_excerpt') or '—'}",
                    f"- **Extraction had**: {item.get('extraction_had') or '—'}",
                    f"- **KG wrote**: {item.get('kg_wrote') or '—'}",
                    f"- **Fix**: {item.get('fix') or '—'}",
                    f"- **Official atoms folded**: {item.get('n_atoms')} ({', '.join(item.get('fields') or [])})",
                    "",
                ]
            )
    return "\n".join(lines) + "\n"


def attribute_papers(
    hashes: Sequence[str],
    *,
    pred_root: Path,
    runtime_root: Path,
    gt_root: Path,
    doi_map: Path,
    out_root: Path,
    judge_model: str,
    synonym_model: str = "gpt-4o",
    skip_order: bool = True,
    ignore_mode: bool = True,
    no_vessel: bool = True,
    dry_run: bool = False,
    paper_root: Path | None = None,
) -> Dict[str, Any]:
    hash_to_doi = hash_map_reverse(doi_map)
    ignore_vessel = bool(no_vessel or ignore_mode)
    equivalence = StepEquivalenceConfig(
        enabled=True,
        model=synonym_model,
        cache_dir=Path("evaluation/cache/step_equivalence_judge"),
        required=False,
        batch_size=40,
        max_workers=8,
        product_match_enabled=True,
        product_match_model=synonym_model,
    )
    from evaluation import scoring_steps as scoring_mod

    scoring_mod._ACTIVE_STEP_EQUIVALENCE = StepEquivalenceJudge(equivalence)

    papers: List[Dict[str, Any]] = []
    all_events: List[Dict[str, Any]] = []
    out_root.mkdir(parents=True, exist_ok=True)

    for hash_value in hashes:
        doi = hash_to_doi.get(hash_value)
        if not doi:
            raise FileNotFoundError(f"no DOI for hash {hash_value}")
        gt_path = gt_root / f"{doi}.json"
        pred_path = pred_root / hash_value / "steps.json"
        if not gt_path.exists():
            raise FileNotFoundError(gt_path)
        if not pred_path.exists():
            raise FileNotFoundError(pred_path)
        gt_raw = json.loads(gt_path.read_text(encoding="utf-8"))
        pred_raw = json.loads(pred_path.read_text(encoding="utf-8"))
        gt_obj, pred_obj = prepare_score_objects(
            gt_raw, pred_raw, hash_value=hash_value, ignore_mode=ignore_mode
        )
        _prefetch_score_equivalence(
            gt_obj,
            pred_obj,
            ignore_vessel=ignore_vessel,
            skip_order=skip_order,
        )
        official = score_steps_fine_grained(
            gt_obj, pred_obj, ignore_vessel, skip_order
        )
        atoms = collect_step_atoms(
            gt_obj,
            pred_obj,
            hash_value=hash_value,
            doi=doi,
            ignore_vessel=ignore_vessel,
            skip_order=skip_order,
        )
        tp = sum(1 for atom in atoms if atom["status"] == "tp")
        fp = sum(1 for atom in atoms if atom["status"] == "fp")
        fn = sum(1 for atom in atoms if atom["status"] == "fn")
        if (tp, fp, fn) != official[:3]:
            raise AssertionError(
                f"{hash_value} atom totals {(tp, fp, fn)} != official {official[:3]}"
            )
        events = merge_error_events(atoms)
        hints_by_entity = load_entity_hints(runtime_root / hash_value)
        routing: Dict[str, str] = {}
        judged: Dict[str, Dict[str, Any]] = {}
        by_synth: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for event in events:
            by_synth[event["synth_key"]].append(event)
        paper_hints_parts: List[str] = []
        for synth_key, synth_events in by_synth.items():
            names = synth_events[0].get("product_names") or []
            hints, route = select_hints_for_synthesis(hints_by_entity, names, synth_key)
            routing[synth_key] = route
            paper_hints_parts.append(f"{synth_key}: {route}")
            pending = events_needing_llm(synth_events, hints)
            print(
                f"[{hash_value} {synth_key}] events={len(synth_events)} "
                f"pending_llm={len(pending)} hints={len(hints)} route={route}",
                flush=True,
            )
            if pending and not dry_run:
                judged.update(
                    judge_hint_support(pending, hints, model=judge_model)
                )
            apply_support_and_attribute(synth_events, hints, judged)
        paper_text, paper_source = load_trimmed_paper(
            hash_value, paper_root, runtime_root / hash_value
        )
        incidents = cluster_incidents(events)
        annotate_pairing_incidents(incidents, pred_obj)
        for item in incidents:
            queries = [
                item.get("should_have_been"),
                *(item.get("gt_values") or []),
                *(item.get("pred_values") or []),
                *(item.get("product_names") or []),
            ]
            item["paper_excerpt"] = excerpt_paper(paper_text, queries)
        union_hints = "\n\n".join(
            select_hints_for_synthesis(
                hints_by_entity,
                synth_events[0].get("product_names") or [],
                synth_key,
            )[0]
            for synth_key, synth_events in by_synth.items()
        )
        if incidents and not dry_run:
            print(
                f"[{hash_value}] diagnosing {len(incidents)} incidents "
                f"paper={paper_source} paper_chars={len(paper_text)}",
                flush=True,
            )
            diagnose_incidents(
                incidents, union_hints, paper_text, model=judge_model
            )
        incident_summary = summarize_incidents(incidents)
        summary = summarize(events)
        paper = {
            "hash": hash_value,
            "doi": doi,
            "official_scorer": {
                "tp": official[0],
                "fp": official[1],
                "fn": official[2],
            },
            "hint_routing": routing,
            "paper_source": paper_source,
            "summary": summary,
            "incident_summary": incident_summary,
            "incidents": incidents,
            "events": events,
        }
        papers.append(paper)
        all_events.extend(events)
        (out_root / f"{hash_value}.json").write_text(
            json.dumps(paper, indent=2, ensure_ascii=True, default=str),
            encoding="utf-8",
        )

    payload = {
        "schema_version": SCHEMA_VERSION,
        "judge_model": judge_model,
        "synonym_model": synonym_model,
        "pred_root": str(pred_root),
        "runtime_root": str(runtime_root),
        "flags": {
            "skip_order": skip_order,
            "ignore": ignore_mode,
            "no_vessel": no_vessel,
        },
        "paper_root": str(paper_root) if paper_root else None,
        "overall": summarize(all_events),
        "incident_summary": summarize_incidents(
            [item for paper in papers for item in paper.get("incidents") or []]
        ),
        "papers": papers,
    }
    (out_root / "attribution.json").write_text(
        json.dumps(
            {k: v for k, v in payload.items() if k != "papers"}
            | {
                "papers": [
                    {
                        k: v
                        for k, v in paper.items()
                        if k not in {"events"}
                    }
                    | {"n_events": len(paper.get("events") or [])}
                    for paper in papers
                ]
            },
            indent=2,
            ensure_ascii=True,
            default=str,
        ),
        encoding="utf-8",
    )
    (out_root / "REPORT.md").write_text(render_report(payload), encoding="utf-8")
    return payload
