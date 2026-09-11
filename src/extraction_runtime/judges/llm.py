"""Shared judge LLM factory matching the official extraction runtime."""

from __future__ import annotations

from models.LLMCreator import LLMCreator
from models.ModelConfig import ModelConfig


def setup_judge_llm(model_name: str):
    return LLMCreator(
        model=model_name,
        model_config=ModelConfig(temperature=0, top_p=1.0),
        remote_model=True,
    ).setup_llm()
