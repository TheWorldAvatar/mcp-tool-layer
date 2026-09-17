"""Locked chemistry steps protocol: type matching, vessel out of gold.

Skip-order is not a scoring switch. Vessel is not a conversion/scorer
filter: the committed gold JSON has no vessel keys, and the cloned steps
engine is pinned to that gold schema.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

VESSEL_JSON_KEYS = frozenset(
    {
        "usedVesselName",
        "usedVesselType",
        "targetVesselName",
        "targetVesselType",
        "sealedVessel",
    }
)
STEPS_LOCK_MARK = "# TWA_LOCKED_STEPS_PROTOCOL\n"


def strip_vessel_fields(value: Any) -> Any:
    """Drop vessel keys from gold or prediction JSON objects."""
    if isinstance(value, dict):
        return {
            key: strip_vessel_fields(item)
            for key, item in value.items()
            if key not in VESSEL_JSON_KEYS
        }
    if isinstance(value, list):
        return [strip_vessel_fields(item) for item in value]
    return value


def json_has_vessel_keys(value: Any) -> bool:
    if isinstance(value, dict):
        if any(key in VESSEL_JSON_KEYS for key in value):
            return True
        return any(json_has_vessel_keys(item) for item in value.values())
    if isinstance(value, list):
        return any(json_has_vessel_keys(item) for item in value)
    return False


def lock_cloned_steps_engine(scorer: Path) -> None:
    """Pin the cloned steps scorer to skip-order and the vessel-free gold schema."""
    path = Path(scorer) / "evaluation" / "scoring_steps.py"
    if not path.is_file():
        return
    text = path.read_text(encoding="utf-8")
    if STEPS_LOCK_MARK in text:
        return
    original = text
    replacements = (
        (
            """VESSEL_FIELDS = {
    "usedVesselName",
    "usedVesselType",
    "targetVesselName",
    "targetVesselType",
}""",
            """VESSEL_FIELDS = {
    "usedVesselName",
    "usedVesselType",
    "targetVesselName",
    "targetVesselType",
    "sealedVessel",
}""",
        ),
        (
            "        if ignore_vessel and key in vessel_fields:\n            continue",
            "        if key in vessel_fields:\n            continue",
        ),
        (
            "            if ignore_vessel and k in vessel_fields:\n                continue",
            "            if k in vessel_fields:\n                continue",
        ),
        (
            "evaluate_previous(use_anchored=not args.no_anchor, ignore_vessel=args.no_vessel, short_mode=args.short, skip_order=args.skip_order, ignore_mode=args.ignore, use_new_gt=args.new, use_full_gt=args.full, equivalence_config=equivalence_config)",
            "evaluate_previous(use_anchored=not args.no_anchor, ignore_vessel=False, short_mode=args.short, skip_order=True, ignore_mode=args.ignore, use_new_gt=args.new, use_full_gt=args.full, equivalence_config=equivalence_config)",
        ),
        (
            "evaluate_current(ignore_vessel=args.no_vessel, short_mode=args.short, skip_order=args.skip_order, ignore_mode=args.ignore, use_new_gt=args.new, use_full_gt=args.full, equivalence_config=equivalence_config, hash_filter=set(args.hashes or []), correct_ccdc_by_name=args.correct_ccdc_by_name, pred_root=args.pred_root, out_root=args.out_root)",
            "evaluate_current(ignore_vessel=False, short_mode=args.short, skip_order=True, ignore_mode=args.ignore, use_new_gt=args.new, use_full_gt=args.full, equivalence_config=equivalence_config, hash_filter=set(args.hashes or []), correct_ccdc_by_name=args.correct_ccdc_by_name, pred_root=args.pred_root, out_root=args.out_root)",
        ),
    )
    for old, new in replacements:
        text = text.replace(old, new)
    if text == original:
        print(f"[WARN] Could not lock steps protocol in {path}", flush=True)
        return
    path.write_text(STEPS_LOCK_MARK + text, encoding="utf-8")
    print(f"[OK] Locked steps scoring protocol -> {path}", flush=True)
