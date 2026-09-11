"""Semantic review for extraction prompts only.

Re-exports `prompt_semantic_review.review_generated_prompt_semantics_with_llm`.
See extraction_prompts/README.md.
"""

from src.extraction_prompt_generation.generate.prompt_semantic_review import (
    review_generated_prompt_semantics_with_llm,
)

__all__ = ["review_generated_prompt_semantics_with_llm"]
