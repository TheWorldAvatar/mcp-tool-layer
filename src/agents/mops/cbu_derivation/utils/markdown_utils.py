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

