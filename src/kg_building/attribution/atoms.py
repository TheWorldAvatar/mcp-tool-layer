"""GT vs Pred mistake atoms for the four official scoring modules."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any

from src.kg_building.attribution.normalize import fingerprint, is_placeholder

MODULES = ("steps", "chemicals", "characterisation", "cbu")

PRED_FILES = {
    "steps": "steps.json",
    "chemicals": "chemicals.json",
    "characterisation": "characterisation.json",
    "cbu": "cbu.json",
}


@dataclass
class MistakeAtom:
    atom_id: str
    module: str
    error_type: str
    field: str
    gt_value: Any
    pred_value: Any
    count: int = 1
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def judge_payload(self) -> dict[str, Any]:
        return {
            "atom_id": self.atom_id,
            "module": self.module,
            "error_type": self.error_type,
            "field": self.field,
            "gt_value": _preview(self.gt_value),
            "pred_value": _preview(self.pred_value),
            "context": self.context,
        }


@dataclass
class ModuleScore:
    module: str
    tp: int
    fp: int
    fn: int
    atoms: list[MistakeAtom]
    official: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "module": self.module,
            "tp": self.tp,
            "fp": self.fp,
            "fn": self.fn,
            "official": self.official,
            "atom_count": len(self.atoms),
        }


def _preview(value: Any) -> Any:
    if isinstance(value, (list, tuple)):
        return [_preview(item) for item in list(value)[:8]]
    text = str(value) if value is not None else ""
    return text if len(text) <= 400 else text[:397] + "..."


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _valid_values(value: Any) -> list[str]:
    return [item for item in _as_list(value) if not is_placeholder(item)]


def _name_set(values: Any) -> set[str]:
    return {fingerprint(item) for item in _valid_values(values) if fingerprint(item)}


def _multiset_diff(
    gt_values: list[str], pred_values: list[str]
) -> tuple[list[str], list[str], int]:
    gt_fp = [fingerprint(item) for item in gt_values if fingerprint(item)]
    pred_fp = [fingerprint(item) for item in pred_values if fingerprint(item)]
    gt_c = Counter(gt_fp)
    pred_c = Counter(pred_fp)
    keys = set(gt_c) | set(pred_c)
    tp = sum(min(gt_c[key], pred_c[key]) for key in keys)
    gt_left = Counter(
        {key: max(0, count - pred_c[key]) for key, count in gt_c.items()}
    )
    pred_left = Counter(
        {key: max(0, count - gt_c[key]) for key, count in pred_c.items()}
    )
    missing: list[str] = []
    for raw in gt_values:
        fp = fingerprint(raw)
        if fp and gt_left[fp] > 0:
            missing.append(raw)
            gt_left[fp] -= 1
    extra: list[str] = []
    for raw in pred_values:
        fp = fingerprint(raw)
        if fp and pred_left[fp] > 0:
            extra.append(raw)
            pred_left[fp] -= 1
    return missing, extra, tp


def _ccdc(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        text = str(payload.get(key) or "").strip()
        if text and not is_placeholder(text):
            return text
    return ""


def _product_names(payload: dict[str, Any]) -> list[str]:
    names = payload.get("productNames") or payload.get("names") or []
    return _valid_values(names)


def expand_steps(synth: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    rows: list[tuple[str, dict[str, Any]]] = []
    for item in synth.get("steps") or []:
        if not isinstance(item, dict):
            continue
        for kind, payload in item.items():
            if str(kind).strip() in {"comment", "stepNumber"}:
                continue
            rows.append((str(kind), payload if isinstance(payload, dict) else {}))
    return rows


def _chemical_names(chem: dict[str, Any]) -> list[str]:
    return _valid_values(
        chem.get("chemicalName") or chem.get("names") or chem.get("name")
    )


def _chemical_amount(chem: dict[str, Any]) -> str:
    amount = chem.get("chemicalAmount")
    if amount is None:
        amount = chem.get("amount")
    text = str(amount or "").strip()
    return "" if is_placeholder(text) else text


def _added_chemicals(step: dict[str, Any]) -> list[dict[str, Any]]:
    chemicals: list[dict[str, Any]] = []
    for key in ("addedChemical", "solvent", "washingSolvent"):
        payload = step.get(key) or []
        if isinstance(payload, list):
            chemicals.extend(item for item in payload if isinstance(item, dict))
    return chemicals


def _match_syntheses(
    gt_synths: list[dict[str, Any]], pred_synths: list[dict[str, Any]]
) -> list[tuple[dict[str, Any] | None, dict[str, Any] | None]]:
    pairs: list[tuple[dict[str, Any] | None, dict[str, Any] | None]] = []
    used: set[int] = set()
    for gt in gt_synths:
        gt_ccdc = _ccdc(gt, "productCCDCNumber", "ccdc", "CCDCNumber")
        gt_names = _name_set(_product_names(gt))
        best_i = -1
        best_score = -1
        for index, pred in enumerate(pred_synths):
            if index in used:
                continue
            score = 0
            pred_ccdc = _ccdc(pred, "productCCDCNumber", "ccdc", "CCDCNumber")
            if gt_ccdc and pred_ccdc and gt_ccdc == pred_ccdc:
                score += 5
            pred_names = _name_set(_product_names(pred))
            if gt_names and pred_names and gt_names & pred_names:
                score += 3
            if score > best_score:
                best_score = score
                best_i = index
        if best_i >= 0 and best_score > 0:
            used.add(best_i)
            pairs.append((gt, pred_synths[best_i]))
        else:
            pairs.append((gt, None))
    for index, pred in enumerate(pred_synths):
        if index not in used:
            pairs.append((None, pred))
    return pairs


def _best_add_index(
    gt_data: dict[str, Any], candidates: list[tuple[int, dict[str, Any]]]
) -> int:
    gt_names = set()
    for chem in _added_chemicals(gt_data):
        gt_names |= _name_set(_chemical_names(chem))
    best = -1
    best_overlap = -1
    for offset, (index, pred_data) in enumerate(candidates):
        pred_names: set[str] = set()
        for chem in _added_chemicals(pred_data):
            pred_names |= _name_set(_chemical_names(chem))
        overlap = len(gt_names & pred_names) if gt_names else 0
        if overlap > best_overlap:
            best_overlap = overlap
            best = offset
    if best < 0:
        return -1
    if gt_names and best_overlap <= 0:
        return -1
    return best


def collect_steps_atoms(
    gt_obj: dict[str, Any], pred_obj: dict[str, Any], *, paper_hash: str
) -> ModuleScore:
    atoms: list[MistakeAtom] = []
    tp = fp = fn = 0
    seq = 0

    def _emit(
        error_type: str,
        field: str,
        gt_value: Any,
        pred_value: Any,
        context: dict[str, Any],
        count: int = 1,
    ) -> None:
        nonlocal seq
        seq += 1
        atoms.append(
            MistakeAtom(
                atom_id=f"steps:{paper_hash}:{seq}",
                module="steps",
                error_type=error_type,
                field=field,
                gt_value=gt_value,
                pred_value=pred_value,
                count=count,
                context=context,
            )
        )

    gt_synths = list((gt_obj or {}).get("Synthesis") or [])
    pred_synths = list((pred_obj or {}).get("Synthesis") or [])
    for gt_synth, pred_synth in _match_syntheses(gt_synths, pred_synths):
        context = {
            "ccdc": _ccdc(
                gt_synth or pred_synth or {},
                "productCCDCNumber",
                "ccdc",
                "CCDCNumber",
            ),
            "product_names": _product_names(gt_synth or pred_synth or {}),
        }
        if gt_synth is None and pred_synth is not None:
            for kind, pred_data in expand_steps(pred_synth):
                fp += 1
                _emit("fp", "step_type", None, kind, {**context, "step_type": kind})
            continue
        if pred_synth is None and gt_synth is not None:
            for kind, gt_data in expand_steps(gt_synth):
                fn += 1
                _emit("fn", "step_type", kind, None, {**context, "step_type": kind})
            continue
        assert gt_synth is not None and pred_synth is not None
        gt_steps = expand_steps(gt_synth)
        pred_steps = expand_steps(pred_synth)
        used: set[int] = set()
        for step_idx, (gt_type, gt_data) in enumerate(gt_steps, start=1):
            step_ctx = {**context, "step_type": gt_type, "step_idx": step_idx}
            if gt_type == "Add":
                candidates = [
                    (index, payload)
                    for index, (kind, payload) in enumerate(pred_steps)
                    if kind == "Add" and index not in used
                ]
                offset = _best_add_index(gt_data, candidates)
                if offset < 0:
                    fn += 1
                    names = [
                        name
                        for chem in _added_chemicals(gt_data)
                        for name in _chemical_names(chem)
                    ]
                    _emit("fn", "step_type", gt_type, None, {**step_ctx, "chemicals": names})
                    continue
                pred_index, pred_data = candidates[offset]
                used.add(pred_index)
            else:
                pred_index = next(
                    (
                        index
                        for index, (kind, _payload) in enumerate(pred_steps)
                        if kind.casefold() == gt_type.casefold() and index not in used
                    ),
                    -1,
                )
                if pred_index < 0:
                    fn += 1
                    _emit("fn", "step_type", gt_type, None, step_ctx)
                    continue
                used.add(pred_index)
                pred_data = pred_steps[pred_index][1]
            gt_names = [
                name
                for chem in _added_chemicals(gt_data)
                for name in _chemical_names(chem)
            ]
            pred_names = [
                name
                for chem in _added_chemicals(pred_data)
                for name in _chemical_names(chem)
            ]
            missing_names, extra_names, name_tp = _multiset_diff(gt_names, pred_names)
            tp += name_tp
            fn += len(missing_names)
            if missing_names:
                _emit("fn", "chemical_name", missing_names, pred_names, step_ctx, len(missing_names))
            gt_amounts = [
                amount
                for chem in _added_chemicals(gt_data)
                if (amount := _chemical_amount(chem))
            ]
            pred_amounts = [
                amount
                for chem in _added_chemicals(pred_data)
                if (amount := _chemical_amount(chem))
            ]
            missing_amounts, extra_amounts, amount_tp = _multiset_diff(
                gt_amounts, pred_amounts
            )
            tp += amount_tp
            fn += len(missing_amounts)
            fp += len(extra_amounts)
            if missing_amounts:
                _emit(
                    "fn",
                    "chemical_amount",
                    missing_amounts,
                    pred_amounts,
                    step_ctx,
                    len(missing_amounts),
                )
            if extra_amounts:
                _emit(
                    "fp",
                    "chemical_amount",
                    gt_amounts,
                    extra_amounts,
                    step_ctx,
                    len(extra_amounts),
                )
            for field_name in ("duration", "atmosphere", "usedVesselType"):
                gt_val = gt_data.get(field_name)
                pred_val = pred_data.get(field_name)
                gt_fp = fingerprint(gt_val)
                pred_fp = fingerprint(pred_val)
                if not gt_fp and not pred_fp:
                    continue
                if gt_fp == pred_fp:
                    tp += 1
                    continue
                if gt_fp and not pred_fp:
                    fn += 1
                    _emit("fn", field_name, gt_val, pred_val, step_ctx)
                elif pred_fp and not gt_fp:
                    fp += 1
                    _emit("fp", field_name, gt_val, pred_val, step_ctx)
                else:
                    fn += 1
                    fp += 1
                    _emit("fn", field_name, gt_val, pred_val, step_ctx)
                    _emit("fp", field_name, gt_val, pred_val, step_ctx)
        for index, (pred_type, pred_data) in enumerate(pred_steps):
            if index in used:
                continue
            fp += 1
            _emit("fp", "step_type", None, pred_type, {**context, "step_type": pred_type})
    return ModuleScore(module="steps", tp=tp, fp=fp, fn=fn, atoms=atoms)


def _input_chemicals(obj: dict[str, Any]) -> list[dict[str, Any]]:
    chemicals: list[dict[str, Any]] = []
    for synth in obj.get("Synthesis") or []:
        for kind, step in expand_steps(synth):
            del kind
            chemicals.extend(_added_chemicals(step))
    for proc in obj.get("synthesisProcedures") or []:
        for step in proc.get("steps") or []:
            if not isinstance(step, dict):
                continue
            for wrapper in step.get("inputChemicals") or []:
                if not isinstance(wrapper, dict):
                    continue
                inner = wrapper.get("chemical") or []
                if isinstance(inner, list):
                    chemicals.extend(item for item in inner if isinstance(item, dict))
                else:
                    chemicals.append(wrapper)
    return chemicals


def collect_chemicals_atoms(
    gt_obj: dict[str, Any], pred_obj: dict[str, Any], *, paper_hash: str
) -> ModuleScore:
    gt_names = [
        name
        for chem in _input_chemicals(gt_obj)
        for name in _chemical_names(chem)
    ]
    pred_names = [
        name
        for chem in _input_chemicals(pred_obj)
        for name in _chemical_names(chem)
    ]
    missing, extra, tp = _multiset_diff(gt_names, pred_names)
    atoms: list[MistakeAtom] = []
    seq = 0
    for value in missing:
        seq += 1
        atoms.append(
            MistakeAtom(
                atom_id=f"chemicals:{paper_hash}:{seq}",
                module="chemicals",
                error_type="fn",
                field="chemical_name",
                gt_value=value,
                pred_value=None,
            )
        )
    for value in extra:
        seq += 1
        atoms.append(
            MistakeAtom(
                atom_id=f"chemicals:{paper_hash}:{seq}",
                module="chemicals",
                error_type="fp",
                field="chemical_name",
                gt_value=None,
                pred_value=value,
            )
        )
    gt_amounts = [
        amount
        for chem in _input_chemicals(gt_obj)
        if (amount := _chemical_amount(chem))
    ]
    pred_amounts = [
        amount
        for chem in _input_chemicals(pred_obj)
        if (amount := _chemical_amount(chem))
    ]
    missing_amounts, extra_amounts, _tp_amounts = _multiset_diff(gt_amounts, pred_amounts)
    for value in missing_amounts:
        seq += 1
        atoms.append(
            MistakeAtom(
                atom_id=f"chemicals:{paper_hash}:{seq}",
                module="chemicals",
                error_type="fn",
                field="chemical_amount",
                gt_value=value,
                pred_value=None,
            )
        )
    for value in extra_amounts:
        seq += 1
        atoms.append(
            MistakeAtom(
                atom_id=f"chemicals:{paper_hash}:{seq}",
                module="chemicals",
                error_type="fp",
                field="chemical_amount",
                gt_value=None,
                pred_value=value,
            )
        )
    return ModuleScore(
        module="chemicals",
        tp=tp,
        fp=len(extra),
        fn=len(missing),
        atoms=atoms,
    )


def _characterisations(obj: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for device in obj.get("Devices") or []:
        for item in (device or {}).get("Characterisation") or []:
            if isinstance(item, dict):
                rows.append(item)
    for item in obj.get("characterisations") or []:
        if isinstance(item, dict):
            rows.append(item)
    return rows


def _char_key(item: dict[str, Any]) -> str:
    ccdc = _ccdc(item, "productCCDCNumber", "ccdc")
    names = _product_names(item)
    if ccdc:
        return ccdc
    if names:
        return f"NAME:{names[0]}"
    return ""


def _field_map(item: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    hnmr = item.get("HNMR") or {}
    ir_spec = item.get("InfraredSpectroscopy") or {}
    elemental = item.get("ElementalAnalysis") or {}
    for key, payload, inner in (
        ("HNMR.shifts", hnmr, "shifts"),
        ("HNMR.solvent", hnmr, "solvent"),
        ("InfraredSpectroscopy.bands", ir_spec, "bands"),
        ("ElementalAnalysis.chemicalFormula", elemental, "chemicalFormula"),
    ):
        if isinstance(payload, dict):
            fields[key] = payload.get(inner)
    return fields


def collect_characterisation_atoms(
    gt_obj: dict[str, Any], pred_obj: dict[str, Any], *, paper_hash: str
) -> ModuleScore:
    gt_rows = {_char_key(item) or f"gt-{index}": item for index, item in enumerate(_characterisations(gt_obj))}
    pred_rows = {_char_key(item) or f"pred-{index}": item for index, item in enumerate(_characterisations(pred_obj))}
    atoms: list[MistakeAtom] = []
    tp = fp = fn = 0
    seq = 0
    keys = set(gt_rows) | set(pred_rows)

    def _emit(error_type: str, field: str, gt_value: Any, pred_value: Any, key: str) -> None:
        nonlocal seq
        seq += 1
        atoms.append(
            MistakeAtom(
                atom_id=f"characterisation:{paper_hash}:{seq}",
                module="characterisation",
                error_type=error_type,
                field=field,
                gt_value=gt_value,
                pred_value=pred_value,
                context={"key": key},
            )
        )

    for key in sorted(keys):
        gt_item = gt_rows.get(key)
        pred_item = pred_rows.get(key)
        if gt_item is not None and pred_item is not None:
            tp += 1
        elif gt_item is not None:
            fn += 1
            _emit("fn", "characterisation_record", _product_names(gt_item), None, key)
            continue
        else:
            fp += 1
            _emit("fp", "characterisation_record", None, _product_names(pred_item or {}), key)
            continue
        for field_name, gt_val in _field_map(gt_item).items():
            pred_val = _field_map(pred_item).get(field_name)
            gt_fp = fingerprint(gt_val)
            pred_fp = fingerprint(pred_val)
            if not gt_fp and not pred_fp:
                continue
            if gt_fp == pred_fp:
                tp += 1
                continue
            if gt_fp and not pred_fp:
                fn += 1
                _emit("fn", field_name, gt_val, pred_val, key)
            elif pred_fp and not gt_fp:
                fp += 1
                _emit("fp", field_name, gt_val, pred_val, key)
            else:
                fn += 1
                fp += 1
                _emit("fn", field_name, gt_val, pred_val, key)
                _emit("fp", field_name, gt_val, pred_val, key)
    return ModuleScore(module="characterisation", tp=tp, fp=fp, fn=fn, atoms=atoms)


def _cbu_procedures(obj: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for proc in obj.get("synthesisProcedures") or []:
        if not isinstance(proc, dict):
            continue
        formulas = {
            fingerprint(proc.get("cbuFormula1")),
            fingerprint(proc.get("cbuFormula2")),
        }
        formulas.discard("")
        names = _valid_values(proc.get("cbuSpeciesNames1")) + _valid_values(
            proc.get("cbuSpeciesNames2")
        )
        rows.append(
            {
                "ccdc": _ccdc(proc, "mopCCDCNumber", "CCDCNumber", "ccdc"),
                "formulas": formulas,
                "names": names,
            }
        )
    return rows


def collect_cbu_atoms(
    gt_obj: dict[str, Any], pred_obj: dict[str, Any], *, paper_hash: str
) -> ModuleScore:
    gt_rows = _cbu_procedures(gt_obj)
    pred_rows = _cbu_procedures(pred_obj)
    used: set[int] = set()
    atoms: list[MistakeAtom] = []
    tp = fp = fn = 0
    seq = 0

    def _emit(error_type: str, field: str, gt_value: Any, pred_value: Any, ccdc: str) -> None:
        nonlocal seq
        seq += 1
        atoms.append(
            MistakeAtom(
                atom_id=f"cbu:{paper_hash}:{seq}",
                module="cbu",
                error_type=error_type,
                field=field,
                gt_value=gt_value,
                pred_value=pred_value,
                context={"ccdc": ccdc},
            )
        )

    for gt in gt_rows:
        best = -1
        best_score = -1
        for index, pred in enumerate(pred_rows):
            if index in used:
                continue
            score = 0
            if gt["ccdc"] and pred["ccdc"] and gt["ccdc"] == pred["ccdc"]:
                score += 3
            score += 10 * len(gt["formulas"] & pred["formulas"])
            if score > best_score:
                best_score = score
                best = index
        if best < 0 or best_score <= 0:
            fn += len(gt["formulas"]) or 1
            _emit("fn", "cbu_formula", sorted(gt["formulas"]), None, gt["ccdc"])
            continue
        used.add(best)
        pred = pred_rows[best]
        overlap = gt["formulas"] & pred["formulas"]
        missing = gt["formulas"] - pred["formulas"]
        extra = pred["formulas"] - gt["formulas"]
        tp += len(overlap)
        fn += len(missing)
        fp += len(extra)
        if missing:
            _emit("fn", "cbu_formula", sorted(missing), sorted(pred["formulas"]), gt["ccdc"])
        if extra:
            _emit("fp", "cbu_formula", sorted(gt["formulas"]), sorted(extra), gt["ccdc"])
        gt_names = _name_set(gt["names"])
        pred_name_set = _name_set(pred["names"])
        if gt_names and not (gt_names & pred_name_set):
            fn += 1
            _emit("fn", "cbu_species", gt["names"], pred["names"], gt["ccdc"])
    for index, pred in enumerate(pred_rows):
        if index in used:
            continue
        fp += len(pred["formulas"]) or 1
        _emit("fp", "cbu_formula", None, sorted(pred["formulas"]), pred["ccdc"])
    return ModuleScore(module="cbu", tp=tp, fp=fp, fn=fn, atoms=atoms)


COLLECTORS = {
    "steps": collect_steps_atoms,
    "chemicals": collect_chemicals_atoms,
    "characterisation": collect_characterisation_atoms,
    "cbu": collect_cbu_atoms,
}
