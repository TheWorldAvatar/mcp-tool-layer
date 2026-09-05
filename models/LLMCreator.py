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
    disable_all = _truthy_env("TWA_DISABLE_REASONING")
    enable_qwen = _truthy_env("TWA_ENABLE_QWEN_THINKING")
    disable_qwen = _is_qwen_model(model) and (
        disable_all or _truthy_env("TWA_DISABLE_QWEN_THINKING") or not enable_qwen
    )
    if disable_all or disable_qwen:
        extra_body = _disable_reasoning_body(extra_body)
    return extra_body


def _apply_reasoning_effort_policy(extra_body, model):
    """Pin OpenRouter/Kimi reasoning effort when TWA_REASONING_EFFORT is set."""
    if _is_deepseek_model(model) and not _truthy_env("TWA_ENABLE_DEEPSEEK_THINKING"):
        return extra_body
    effort = os.environ.get("TWA_REASONING_EFFORT", "").strip().lower()
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
        self.model = model
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
        # Prepare kwargs and inject a default deterministic seed if missing
        cfg_kwargs = self.config.get_config(model_name=self.model) if self.config else {}

        # For models that only support default temperature (gpt-5, gpt-4.1-mini, etc.), 
        # don't set temperature (let them use default value of 1.0)
        # For other models, set default temperature to 0 for determinism
        models_without_temperature = ["gpt-5", "gpt-5-mini", "gpt-4.1-mini"]
        model_leaf = str(self.model or "").split("/")[-1]
        if any(model_leaf.startswith(m) for m in models_without_temperature) or _is_kimi_k3_model(
            self.model
        ):
            # Kimi K3 fixes temperature at 1.0; do not send 0.
            cfg_kwargs.pop("temperature", None)
        else:
            cfg_kwargs.setdefault("temperature", 0)
        
        cfg_kwargs.pop("top_p", None)
        cfg_kwargs.setdefault("n", 1)
        # Avoid flaky runs on transient network failures.
        # Can be overridden by ModelConfig or by setting LLM_MAX_RETRIES.
        try:
            env_retries = os.environ.get("LLM_MAX_RETRIES", "").strip()
            env_retries_int = int(env_retries) if env_retries else None
        except Exception:
            env_retries_int = None
        cfg_kwargs.setdefault("max_retries", env_retries_int if env_retries_int is not None else 3)

        try:
            deterministic_seed = int(
                os.environ.get("TWA_LLM_SEED", "42").strip()
            )
        except ValueError:
            deterministic_seed = 42
        # Providers treat seed as best-effort, but using one value removes the
        # largest controllable source of sampling drift.
        cfg_kwargs.setdefault("seed", deterministic_seed)

        # 正确：LangChain 用 streaming，不是 stream
        cfg_kwargs.pop("stream", None)
        cfg_kwargs.setdefault("streaming", False)  # 需要流式则设 True
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

if __name__ == "__main__":
    load_dotenv(override=True)
    from models.ModelConfig import ModelConfig
    llm_creator = LLMCreator(model="gpt-4o-search-preview", remote_model=True, model_config=ModelConfig(), structured_output=False, structured_output_schema=None)
    llm = llm_creator.setup_llm()

    prompt = """
    Convert given linker names (e.g. H2EDB, H2NDBDC) into their linker fragment formula for use in MOF/MOP core formulas.

    ================================

    Input: H2EDB, H2NDBDC, H2edb, 4,4'-(ethyne-1,2-diyl)dibenzoic acid, H2DCPP (4,4′-(porphyrin-5,15-diyl)dibenzoic acid), H3TATB(1,3,5-triamino-2,4,6-trinitrobenzene)

    ================================

    e.g., (C10H6)(C6H4)2(CO2)2
 
    Give very brief outputs. Don't use subscripts or superscripts.
    """

    # Expected output: [(C12H6)(CO2)4]
    # Input: H4BPTC

    prompt_with_rule = """
    Rule: MOF/MOP core formulas

    Convert given linker names (e.g. H2EDB, H2NDBDC) into their linker fragment formula for use in MOF/MOP core formulas. 

    Also, if the linker has name like H<Number of Hydrogen atoms>XXXX, you should get the formula from the given name first and remove <Number of Hydrogen atoms> x H from the formula。 

    Linker name is: H4BPTC 
 
 """

    prompt_for_smiles_and_inchi = """
    Given the chemical species name, search for its other representation. Search the web for the information, don't come it up yourself. Search the websites, not databases.

    If there are multiple candidates, provide all of them.

    The chemical species name is: H2edb

    Be patient and try hard. 
    """
    response = llm.invoke(prompt_for_smiles_and_inchi)
    print(response)
    print(response.content)
    print(f"Token usage: {response.response_metadata['token_usage']['total_tokens']}")