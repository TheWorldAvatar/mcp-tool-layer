"""
LLMCreator is the basic class for creating LLM instances.

It is designed to init LLM instances, remote or local, with customizable configurations.
"""

import os

from langchain_openai import ChatOpenAI
from dotenv import load_dotenv

from models.llm_call_telemetry import (
    OpenRouterCostCallback,
    apply_openrouter_usage_include,
)
from models.locked_llm import apply_locked_sampling, canonicalize_chat_model, model_leaf


def _truthy_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}


def _is_qwen_model(model) -> bool:
    return "qwen" in str(model or "").lower()


def _is_deepseek_model(model) -> bool:
    return "deepseek" in str(model or "").lower()


def _is_kimi_k3_model(model) -> bool:
    return "kimi-k3" in str(model or "").lower()


def _disable_reasoning_body(extra_body):
    body = dict(extra_body or {})
    reasoning = dict(body.get("reasoning") or {})
    reasoning["enabled"] = False
    reasoning["effort"] = "none"
    body["reasoning"] = reasoning
    body["reasoning_effort"] = "none"
    body["enable_thinking"] = False
    body["thinking"] = {"type": "disabled"}
    return body


def _apply_qwen_thinking_policy(extra_body, model):
    """Turn off Qwen thinking unless TWA_ENABLE_QWEN_THINKING is set."""
    if _is_kimi_k3_model(model):
        return extra_body
    leaf = model_leaf(model)
    if leaf.startswith("gpt-5") or leaf.startswith("gpt-4.1"):
        return extra_body
    disable_all = _truthy_env("TWA_DISABLE_REASONING")
    enable_qwen = _truthy_env("TWA_ENABLE_QWEN_THINKING")
    disable_qwen = _is_qwen_model(model) and (
        disable_all or _truthy_env("TWA_DISABLE_QWEN_THINKING") or not enable_qwen
    )
    if disable_all or disable_qwen:
        extra_body = _disable_reasoning_body(extra_body)
    return extra_body


def _apply_reasoning_effort_policy(extra_body, model):
    """Pin reasoning effort. GPT-5 / GPT-4.1 ignore TWA_REASONING_EFFORT."""
    leaf = model_leaf(model)
    if leaf.startswith("gpt-5") or leaf.startswith("gpt-4.1"):
        return extra_body
    if _is_deepseek_model(model) and not _truthy_env("TWA_ENABLE_DEEPSEEK_THINKING"):
        return extra_body
    effort = os.environ.get("TWA_REASONING_EFFORT", "").strip().lower()
    if not effort and _is_kimi_k3_model(model):
        effort = "low"
    if not effort:
        return extra_body
    body = dict(extra_body or {})
    reasoning = dict(body.get("reasoning") or {})
    reasoning["enabled"] = True
    reasoning["effort"] = effort
    body["reasoning"] = reasoning
    body["reasoning_effort"] = effort
    return body


def _apply_deepseek_tool_path(extra_body, model):
    """Dedicated DeepSeek path: thinking off, tool-capable OpenRouter providers.

    KG ReAct must not inherit V4 thinking tokens. Opt in with
    TWA_ENABLE_DEEPSEEK_THINKING=1. TWA_DEEPSEEK_ANY_PROVIDER=1 relaxes
    provider filtering if a host rejects require_parameters.
    """
    if not _is_deepseek_model(model):
        return extra_body
    if _truthy_env("TWA_ENABLE_DEEPSEEK_THINKING"):
        body = dict(extra_body or {})
        effort = os.environ.get("TWA_REASONING_EFFORT", "").strip().lower() or "high"
        reasoning = dict(body.get("reasoning") or {})
        reasoning["enabled"] = True
        reasoning["effort"] = effort
        body["reasoning"] = reasoning
        body["reasoning_effort"] = effort
        body["enable_thinking"] = True
        body["thinking"] = {"type": "enabled"}
    else:
        body = _disable_reasoning_body(extra_body)
    if not _truthy_env("TWA_DEEPSEEK_ANY_PROVIDER"):
        provider = dict(body.get("provider") or {})
        provider.setdefault("require_parameters", True)
        body["provider"] = provider
    return body


class LLMCreator():

    def __init__(self, model = "gpt-4o-mini", remote_model=True, model_config = None, structured_output=False, structured_output_schema=None):
        # load the environment variables
        load_dotenv(override=True)
        self.model = canonicalize_chat_model(model)
        self.remote_model = remote_model
        self.structured_output = structured_output
        self.structured_output_schema = structured_output_schema

        # if remote model, use the base url and api key from the environment variables
        if self.remote_model:
            self.base_url = self.load_api_key_from_env("REMOTE_BASE_URL")
            self.api_key = self.load_api_key_from_env("REMOTE_API_KEY")
        else:
            self.base_url = self.load_api_key_from_env("LOCAL_BASE_URL")
            self.api_key = self.load_api_key_from_env("LOCAL_API_KEY")
        self.config = model_config


    def load_api_key_from_env(self, key_name):
        # use dot env to load the api key from the environment variables
        key_value = os.environ.get(key_name, None)
        return key_value

    def setup_llm(self):
        """
        Setup the LLM with the given model, base url, api key, and config.
        This function is here because in the
        """
        cfg_kwargs = self.config.get_config(model_name=self.model) if self.config else {}
        cfg_kwargs = apply_locked_sampling(cfg_kwargs, self.model)
        # Avoid flaky runs on transient network failures.
        try:
            env_retries = os.environ.get("LLM_MAX_RETRIES", "").strip()
            env_retries_int = int(env_retries) if env_retries else None
        except Exception:
            env_retries_int = None
        cfg_kwargs.setdefault("max_retries", env_retries_int if env_retries_int is not None else 3)
        callbacks = list(cfg_kwargs.pop("callbacks", []) or [])
        callbacks.append(
            OpenRouterCostCallback(
                model=self.model,
                base_url=self.base_url,
                api_key=self.api_key,
            )
        )
        extra_body = apply_openrouter_usage_include(
            cfg_kwargs.pop("extra_body", None),
            base_url=self.base_url,
        )
        extra_body = _apply_qwen_thinking_policy(extra_body, self.model)
        extra_body = _apply_reasoning_effort_policy(extra_body, self.model)
        extra_body = _apply_deepseek_tool_path(extra_body, self.model)
        if extra_body:
            cfg_kwargs["extra_body"] = extra_body
        if _is_kimi_k3_model(self.model):
            effort = str((extra_body or {}).get("reasoning_effort") or "").strip()
            if effort:
                cfg_kwargs["reasoning_effort"] = effort

        llm = ChatOpenAI(
            model=self.model,
            base_url=self.base_url,
            api_key=self.api_key,
            cache=False,
            callbacks=callbacks,
            **cfg_kwargs
        )

        return llm if not self.structured_output else llm.with_structured_output(self.structured_output_schema)

            
    def get_model_info(self):
        """
        Returns information about the model configuration without initializing an LLM instance.
        
        Returns:
            dict: A dictionary containing model configuration information.
        """
        model_info = {
            "model_name": self.model,
            "remote": self.remote_model,
            "base_url": self.base_url,
            "config": self.config.get_config() if self.config else {}
        }
        return model_info