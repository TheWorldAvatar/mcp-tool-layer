from pathlib import Path
from datetime import datetime
from typing import Dict


def safe_name(name: str) -> str:
    cleaned = "".join(c for c in name if c.isalnum() or c in (" ", "-", "_")).rstrip()
    return cleaned.replace(" ", "_") or "entity"


def write_individual_md(output_dir: str, species_name: str, response_text: str) -> Path:
    p = Path(output_dir) / f"{safe_name(species_name)}.md"
    content = [
        f"# Organic CBU Derivation: {species_name}",
        "",
        f"**Timestamp:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Result",
        "",
        "```",
        response_text,
        "```",
        "",
    ]
    p.write_text("\n".join(content), encoding="utf-8")
    return p


def write_summary_md(output_dir: str, summary_rows: Dict[str, str]) -> Path:
    p = Path(output_dir) / "summary.md"
    lines = ["# Organic CBU Derivation Summary", "", "| Species | Match |", "|---|---|"]
    for name, match in summary_rows.items():
        lines.append(f"| {name} | {match or 'N/A'} |")
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def write_instruction_md(instructions_dir: str, species_name: str, instruction_text: str) -> Path:
    p = Path(instructions_dir)
    p.mkdir(parents=True, exist_ok=True)
    out = p / f"{safe_name(species_name)}.md"
    content = [
        f"# Instruction for {species_name}",
        "",
        f"**Timestamp:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Full Prompt",
        "",
        "```",
        instruction_text,
        "```",
        "",
    ]
    out.write_text("\n".join(content), encoding="utf-8")
    return out


# -------------------- Metal derivation writers --------------------
def write_metal_individual_md(output_dir: str, ccdc_number: str, response_text: str) -> Path:
    p = Path(output_dir) / f"{safe_name(ccdc_number)}.md"
    content = [
        f"# Metal CBU Derivation: CCDC {ccdc_number}",
        "",
        f"**Timestamp:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Result",
        "",
        "```",
        response_text,
        "```",
        "",
    ]
    p.write_text("\n".join(content), encoding="utf-8")
    return p


def write_metal_summary_md(output_dir: str, summary_rows: Dict[str, str]) -> Path:
    p = Path(output_dir) / "summary.md"
    lines = ["# Metal CBU Derivation Summary", "", "| CCDC | Status |", "|---|---|"]
    for ccdc, status in summary_rows.items():
        lines.append(f"| {ccdc} | {status or 'N/A'} |")
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def write_metal_instruction_md(instructions_dir: str, ccdc_number: str, instruction_text: str) -> Path:
    p = Path(instructions_dir)
    p.mkdir(parents=True, exist_ok=True)
    out = p / f"{safe_name(ccdc_number)}.md"
    content = [
        f"# Instruction for CCDC {ccdc_number}",
        "",
        f"**Timestamp:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Full Prompt",
        "",
        "```",
        instruction_text,
        "```",
        "",
    ]
    out.write_text("\n".join(content), encoding="utf-8")
    return out