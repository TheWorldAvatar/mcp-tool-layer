"""Stage checks for a generated extraction-prompt markdown artifact.

Emptiness and runtime-slot binding for the file just authored.
See stage/README.md.
"""

from __future__ import annotations

import re

from src.extraction_prompt_generation.validate.report.prompts import (
    validate_prompt_runtime_bindings,
)
from src.extraction_prompt_generation.validate.report.stage.context import (
    StageProbe,
)


def validate_stage_prompt(probe: StageProbe) -> None:
    """Require runtime slots and reject unresolved prompt residue."""
    binding_report = validate_prompt_runtime_bindings(probe.path, probe.context)
    probe.failures.extend(binding_report.get("failures") or [])
    if "TODO" in probe.text or "FIXME" in probe.text:
        probe.warn(f"{probe.name}: prompt contains unresolved TODO/FIXME residue")
    unresolved = sorted(
        set(re.findall(r"\{\{[^}\n]+\}\}", probe.text, flags=re.IGNORECASE))
    )
    if unresolved:
        probe.fail(
            f"{probe.name}: unresolved prompt placeholder/residue: "
            + ", ".join(unresolved[:8])
        )
