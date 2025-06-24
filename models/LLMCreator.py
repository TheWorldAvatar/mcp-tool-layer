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
        load_dotenv()
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
        print(f"Loaded {key_name} from environment variables: {key_value}")
        return key_value

    def setup_llm(self):
        """
        Setup the LLM with the given model, base url, api key, and config.
        This function is here because in the
        """
        if not self.structured_output:
            return ChatOpenAI(
                model=self.model,
                base_url=self.base_url,
                api_key=self.api_key,
                **self.config.get_config()
            )
        else:
            return ChatOpenAI(
                model=self.model,
                base_url=self.base_url,
                api_key=self.api_key,
                **self.config.get_config()
            ).with_structured_output(self.structured_output_schema)
    
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

 