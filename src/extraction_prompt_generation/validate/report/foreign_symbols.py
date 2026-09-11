"""Cross-ontology symbol leakage checks on generated Python.

Skipped when no foreign contracts are supplied. See report/README.md.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from src.extraction_prompt_generation.compile.context import (
    AgenticGenerationContext,
)
from src.extraction_prompt_generation.validate.report.common import (
    _read_texts,
)


def _foreign_symbol_report(
    context: AgenticGenerationContext,
    foreign_contracts: list[dict[str, Any]] | None,
) -> tuple[list[str], list[str]]:
    """Fail when generated Python uses another ontology's local names."""
    failures: list[str] = []
    warnings: list[str] = []
    if not foreign_contracts:
        return failures, warnings
    allowed = {str(x) for x in context.contract.get("ontology_symbol_locals") or []}
    common_tokens = {
        "label",
        "name",
        "type",
        "class",
        "property",
        "comment",
        "range",
        "domain",
        "literal",
        "ontology",
        "document",
    }
    foreign_symbols: set[str] = set()
    for bundle in foreign_contracts:
        foreign_symbols.update(
            str(x) for x in bundle.get("ontology_symbol_locals") or []
        )
    foreign_symbols = {
        s
        for s in foreign_symbols
        if len(s) >= 4
        and s not in allowed
        and s.lower() not in common_tokens
        and not s.islower()
    }
    # Prompt prose is reviewed semantically from structured contract evidence.
    # Symbol regex checks remain limited to generated Python source.
    texts = {
        name: text
        for name, text in _read_texts(Path(context.scripts_dir), "*.py").items()
        if name
        not in {
            "_fixed_rdf_runtime.py",
            "_fixed_om2_runtime.py",
            "_reuse_pair_judge.py",
        }
    }
    offenders: list[str] = []
    for name, text in texts.items():
        hits = sorted(
            symbol
            for symbol in foreign_symbols
            if re.search(rf"(?<![A-Za-z0-9_]){re.escape(symbol)}(?![A-Za-z0-9_])", text)
        )
        if hits:
            offenders.append(f"{name}: {', '.join(hits[:8])}")
    if offenders:
        failures.append("Foreign ontology symbols found: " + "; ".join(offenders[:8]))
    return failures, warnings
