"""Hard lock: extraction prompts must be the slim (0.838) family.

The 2026-09-07 e2e1/promptfix compiler family (`# Extraction Prompt:` plus
`shortest stable source-supported identifier`) is rejected at generation
and at extraction runtime. This module is the only detector both sides use.

Domain class names and alias vocabularies are not enforced here.
"""

from __future__ import annotations

import re
from pathlib import Path

E2E1_HEADER = "# Extraction Prompt:"
SHORTEST_IDENTIFIER = "shortest stable source-supported identifier"
LINE_ONLY_OUTPUT = re.compile(
    r"Return only normalized top-entity lines",
    re.IGNORECASE,
)


def slim_family_failures(name: str, text: str) -> list[str]:
    """Return hard-gate failures if `text` is the e2e1 extraction family."""
    failures: list[str] = []
    upper = str(name or "").upper()
    body = str(text or "")
    stripped = body.lstrip()
    if not body.strip():
        return failures
    is_semantic_text = "SEMANTIC_HINTS_V1" in body
    # Extension pipeline_iteration_number can name the semantic-text prompt
    # EXTRACTION_ITER_1.md (OntoMOPs). That file is not the main identity JSON.
    if upper == "EXTRACTION_ITER_1.MD" and not is_semantic_text:
        if stripped.startswith(E2E1_HEADER):
            failures.append(
                f"{name}: e2e1/promptfix template header is forbidden; "
                "ITER1 must be the slim identity-bearing JSON prompt"
            )
        if SHORTEST_IDENTIFIER in body.casefold():
            failures.append(
                f"{name}: 'shortest stable source-supported identifier' is the "
                "e2e1 ITER1 contract; slim requires the most specific procedure identity"
            )
        if LINE_ONLY_OUTPUT.search(body):
            failures.append(
                f"{name}: line-only `Class-N [label]` output is forbidden; "
                "slim ITER1 must require one JSON object with entities/relations"
            )
        lowered = body.casefold()
        if "most specific" not in lowered and "most-specific" not in lowered:
            failures.append(
                f"{name}: slim ITER1 must require the most specific "
                "source-supported procedure identity as the entity label"
            )
        if "entities" not in lowered or "relations" not in lowered:
            failures.append(
                f"{name}: slim ITER1 must require a JSON object with entities and relations"
            )
    if upper.startswith("EXTRACTION_ITER_") and (
        upper != "EXTRACTION_ITER_1.MD" or is_semantic_text
    ):
        if is_semantic_text:
            if stripped.startswith(E2E1_HEADER):
                failures.append(
                    f"{name}: e2e1/promptfix template header is forbidden; "
                    "semantic-text ITER prompts must be the slim RUNTIME INPUTS family"
                )
            if SHORTEST_IDENTIFIER in body.casefold():
                failures.append(
                    f"{name}: 'shortest stable source-supported identifier' is forbidden "
                    "on slim semantic-text extraction prompts"
                )
    return failures


def assert_slim_extraction_prompt_family(name: str, text: str) -> None:
    failures = slim_family_failures(name, text)
    if failures:
        raise ValueError("slim extraction prompt lock: " + "; ".join(failures))


def assert_slim_prompt_path(path: str | Path, text: str) -> None:
    assert_slim_extraction_prompt_family(Path(path).name, text)
