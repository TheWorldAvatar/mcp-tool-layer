"""
ModelConfig is a class that contains the configuration for the LLM model.
"""

class ModelConfig:

    def __init__(self, 
                 max_tokens=16000,
                 timeout=600,
                 temperature=0.2,
                 top_p=0.01,
                 ):
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.temperature = temperature
        self.top_p = top_p
    def get_config(self, model_name: str):
        if model_name == "o3-mini":
            return {
                "timeout": self.timeout,
                "temperature": self.temperature,
                # "top_p": self.top_p
            }

        elif model_name in ["gpt-4o-mini-search-preview", "gpt-4o-search-preview", "o1", "o3-mini", "o3-mini-high", "gpt-5", "o3"]:
            return {
                "timeout": self.timeout
            }


        else:
            return {
                # "max_tokens": self.max_tokens,
                "timeout": self.timeout,
                "temperature": self.temperature,
                "top_p": self.top_p
            }
