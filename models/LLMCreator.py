"""
LLMCreator is the basic class for creating LLM instances.

It is designed to init LLM instances, remote or local, with customizable configurations.
"""

import os

from langchain_openai import ChatOpenAI
from dotenv import load_dotenv


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
        if any(self.model.startswith(m) for m in models_without_temperature):
            # Remove temperature if present for these models (let them use default)
            cfg_kwargs.pop("temperature", None)
        else:
            cfg_kwargs.setdefault("temperature", 0)
        
        cfg_kwargs.pop("top_p", None)
        cfg_kwargs.setdefault("n", 1)
        cfg_kwargs.setdefault("max_retries", 0)

        # 正确：显式 seed（不要放 model_kwargs）
        cfg_kwargs.setdefault("seed", 42)

        # 正确：LangChain 用 streaming，不是 stream
        cfg_kwargs.pop("stream", None)
        cfg_kwargs.setdefault("streaming", False)  # 需要流式则设 True

        llm = ChatOpenAI(
            model=self.model,
            base_url=self.base_url,
            api_key=self.api_key,
            cache=False,
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