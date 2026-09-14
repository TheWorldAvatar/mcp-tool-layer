"""Load official scorer TP/FP/FN and error lists from a score run folder."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from src.kg_building.attribution.atoms import MistakeAtom, ModuleScore

SCORE_DIRS = {
    "steps": "scoring_steps",
    "chemicals": "scoring_chemicals",
    "characterisation": "scoring_characterisation",
    "cbu": "scoring_cbu",
    "cbu_formula": "scoring_cbu",
}

HASH_RE = re.compile(r"\b([0-9a-f]{8})\b")
COUNTS_RE = re.compile(
    r"([0-9a-f]{8}).*?\|?\s*\*?\*?(\d+)\*?\*?\s*\|\s*\*?\*?(\d+)\*?\*?\s*\|\s*\*?\*?(\d+)\*?\*?"
    r"\s*\|\s*\*?\*?([0-9.]+)\*?\*?"
)
FN_LINE_RE = re.compile(r"^FN \(([^)]+)\):\s*(.*)$", re.IGNORECASE)
FP_LINE_RE = re.compile(r"^FP \(([^)]+)\):\s*(.*)$", re.IGNORECASE)
FIELD_DIFF_RE = re.compile(
    r"([A-Za-z0-9.]+):\s*pred='(.*?)'\s*vs gt='(.*?)'(?:;|$)"
)


def scores_dir(scores_root: Path, module: str) -> Path:
    return Path(scores_root) / SCORE_DIRS[module]


def parse_overall_counts(md_text: str, *, section: str | None = None) -> dict[str, dict[str, Any]]:
    text = md_text
    if section:
        marker = f"## {section}"
        start = text.find(marker)
        if start < 0:
            return {}
        rest = text[start + len(marker) :]
        nxt = rest.find("\n## ")
        text = rest if nxt < 0 else rest[:nxt]
    else:
        nxt = text.find("\n## ")
        if nxt >= 0:
            text = text[:nxt]
    out: dict[str, dict[str, Any]] = {}
    for line in text.splitlines():
        if "Overall" in line:
            continue
        match = HASH_RE.search(line)
        if not match:
            continue
        counts = COUNTS_RE.search(line)
        if not counts:
            continue
        paper_hash, tp, fp, fn, f1 = counts.groups()
        if paper_hash != match.group(1):
            continue
        out[paper_hash] = {
            "tp": int(tp),
            "fp": int(fp),
            "fn": int(fn),
            "f1": float(f1),
        }
    return out


def load_official_counts(scores_root: Path, module: str) -> dict[str, dict[str, Any]]:
    path = scores_dir(scores_root, module) / "_overall.md"
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8")
    if module == "cbu_formula":
        return parse_overall_counts(text, section="Formula-only Scoring")
    if module == "cbu":
        parsed = parse_overall_counts(text, section="Combined Scoring (Formulas + Species Names with Fairness)")
        return parsed or parse_overall_counts(text)
    return parse_overall_counts(text)


def _split_items(raw: str) -> list[str]:
    text = raw.strip()
    if not text or text.casefold() in {"none", "n/a", "null"}:
        return []
    return [item.strip(" `") for item in text.split(", ") if item.strip(" `")]


def _strip_ticks(value: str) -> str:
    return value.strip().strip("`").strip()


def _differences_block(md_text: str) -> str:
    marker = "## Differences"
    start = md_text.find(marker)
    if start < 0:
        return ""
    return md_text[start + len(marker) :]


def _atoms_from_differences(
    *,
    module: str,
    paper_hash: str,
    md_text: str,
    formula_only: bool = False,
) -> list[MistakeAtom]:
    atoms: list[MistakeAtom] = []
    seq = 0
    block = _differences_block(md_text)
    if not block:
        return atoms

    def emit(error_type: str, field: str, gt_value: Any, pred_value: Any) -> None:
        nonlocal seq
        seq += 1
        atoms.append(
            MistakeAtom(
                atom_id=f"{module}:{paper_hash}:{seq}",
                module=module,
                error_type=error_type,
                field=field,
                gt_value=gt_value,
                pred_value=pred_value,
            )
        )

    for line in block.splitlines():
        stripped_line = line.strip()
        fn_match = FN_LINE_RE.match(stripped_line)
        if fn_match:
            kind, rest = fn_match.groups()
            if formula_only and "formula" not in kind.casefold():
                continue
            for item in _split_items(rest):
                emit("fn", kind, item, None)
            continue
        fp_match = FP_LINE_RE.match(stripped_line)
        if fp_match:
            kind, rest = fp_match.groups()
            if formula_only and "formula" not in kind.casefold():
                continue
            for item in _split_items(rest):
                emit("fp", kind, None, item)
            continue
        if stripped_line.startswith("- ["):
            for field, pred, gt in FIELD_DIFF_RE.findall(stripped_line):
                gt_s, pred_s = _strip_ticks(gt), _strip_ticks(pred)
                if gt_s.casefold() in {"n/a", "none", ""} and pred_s.casefold() not in {"n/a", "none", ""}:
                    emit("fp", field, gt_s, pred_s)
                elif pred_s.casefold() in {"n/a", "none", ""} and gt_s.casefold() not in {"n/a", "none", ""}:
                    emit("fn", field, gt_s, pred_s)
                elif gt_s != pred_s:
                    emit("fn", field, gt_s, pred_s)
    return atoms


def _atoms_from_error_details(scores_root: Path, paper_hash: str) -> list[MistakeAtom]:
    details = scores_dir(scores_root, "steps") / "error_details"
    if not details.is_dir():
        return []
    atoms: list[MistakeAtom] = []
    seq = 0
    for path in sorted(details.glob("*.md")):
        field = path.stem
        text = path.read_text(encoding="utf-8")
        section = "fn"
        for line in text.splitlines():
            if line.startswith("## False Negatives"):
                section = "fn"
                continue
            if line.startswith("## False Positives"):
                section = "fp"
                continue
            if not line.startswith("|"):
                continue
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if len(cells) < 6 or cells[0] in {"Hash", "------"} or set(cells[0]) <= {"-"}:
                continue
            if cells[0] != paper_hash:
                continue
            seq += 1
            gt_value = _strip_ticks(cells[-2]) if len(cells) >= 2 else ""
            pred_value = _strip_ticks(cells[-1])
            atoms.append(
                MistakeAtom(
                    atom_id=f"steps:{paper_hash}:{seq}",
                    module="steps",
                    error_type=section,
                    field=field,
                    gt_value=gt_value,
                    pred_value=pred_value,
                    context={
                        "step_type": cells[4] if len(cells) > 4 else "",
                        "step": cells[3] if len(cells) > 3 else "",
                    },
                )
            )
    return atoms


def load_official_module(
    scores_root: Path,
    module: str,
    paper_hash: str,
) -> ModuleScore | None:
    counts = load_official_counts(scores_root, module).get(paper_hash)
    if counts is None:
        return None
    paper_md = scores_dir(scores_root, module) / f"{paper_hash}.md"
    md_text = paper_md.read_text(encoding="utf-8") if paper_md.is_file() else ""
    if module == "steps":
        atoms = _atoms_from_error_details(scores_root, paper_hash)
    else:
        atoms = _atoms_from_differences(
            module="cbu" if module.startswith("cbu") else module,
            paper_hash=paper_hash,
            md_text=md_text,
            formula_only=module == "cbu_formula",
        )
        if module == "cbu_formula":
            for atom in atoms:
                atom.module = "cbu_formula"
    return ModuleScore(
        module=module,
        tp=counts["tp"],
        fp=counts["fp"],
        fn=counts["fn"],
        atoms=atoms,
        official=True,
    )
