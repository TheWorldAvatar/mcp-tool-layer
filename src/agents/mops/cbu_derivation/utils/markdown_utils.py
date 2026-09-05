from pathlib import Path
from datetime import datetime
from typing import Dict

from src.pipelines.utils.runtime_paths import bounded_runtime_file, write_runtime_text
from src.pipelines.utils.top_entity_identity import entity_artifact_name


def safe_name(name: str) -> str:
    return entity_artifact_name(name)


def write_individual_md(output_dir: str, species_name: str, response_text: str) -> Path:
    p = Path(bounded_runtime_file(str(Path(output_dir) / f"{safe_name(species_name)}.md")))
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
    write_runtime_text(str(p), "\n".join(content))
    return p


def write_summary_md(output_dir: str, summary_rows: Dict[str, str]) -> Path:
    p = Path(output_dir) / "summary.md"
    lines = ["# Organic CBU Derivation Summary", "", "| Species | Match |", "|---|---|"]
    for name, match in summary_rows.items():
        lines.append(f"| {name} | {match or 'N/A'} |")
    write_runtime_text(str(p), "\n".join(lines))
    return p


def write_instruction_md(instructions_dir: str, species_name: str, instruction_text: str) -> Path:
    p = Path(instructions_dir)
    out = Path(bounded_runtime_file(str(p / f"{safe_name(species_name)}.md")))
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
    write_runtime_text(str(out), "\n".join(content))
    return out


# -------------------- Metal derivation writers --------------------
def write_metal_individual_md(output_dir: str, ccdc_number: str, response_text: str) -> Path:
    p = Path(bounded_runtime_file(str(Path(output_dir) / f"{safe_name(ccdc_number)}.md")))
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
    write_runtime_text(str(p), "\n".join(content))
    return p


def write_metal_summary_md(output_dir: str, summary_rows: Dict[str, str]) -> Path:
    p = Path(output_dir) / "summary.md"
    lines = ["# Metal CBU Derivation Summary", "", "| CCDC | Status |", "|---|---|"]
    for ccdc, status in summary_rows.items():
        lines.append(f"| {ccdc} | {status or 'N/A'} |")
    write_runtime_text(str(p), "\n".join(lines))
    return p


def write_metal_instruction_md(instructions_dir: str, ccdc_number: str, instruction_text: str) -> Path:
    p = Path(instructions_dir)
    p.mkdir(parents=True, exist_ok=True)
    out = Path(bounded_runtime_file(str(p / f"{safe_name(ccdc_number)}.md")))
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
    write_runtime_text(str(out), "\n".join(content))
    return out