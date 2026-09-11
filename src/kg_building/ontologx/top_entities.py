"""Load pipeline top-entity inventory (membership + omission + audit already applied)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_LINE = re.compile(
    r"^(?P<key>ChemicalSynthesis-\d+)\s+\[(?P<label>.+)\]\s*$"
)


@dataclass(frozen=True)
class TopEntity:
    key: str
    label: str
    line: str

    @property
    def slug(self) -> str:
        digits = "".join(ch for ch in self.key if ch.isdigit()) or "0"
        return f"cs{digits}"


def load_top_entities(runtime_dir: Path) -> list[TopEntity]:
    path = runtime_dir / "top_entities.txt"
    if not path.exists():
        raise FileNotFoundError(f"Missing top-entity inventory: {path}")
    entities: list[TopEntity] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _LINE.match(line)
        if match:
            entities.append(
                TopEntity(key=match.group("key"), label=match.group("label").strip(), line=line)
            )
            continue
        entities.append(TopEntity(key=f"ChemicalSynthesis-{len(entities)+1}", label=line, line=line))
    if not entities:
        raise ValueError(f"Empty top-entity inventory: {path}")
    return entities
