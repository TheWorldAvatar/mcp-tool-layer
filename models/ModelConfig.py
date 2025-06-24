"""
ModelConfig is a class that contains the configuration for the LLM model.
"""

class ModelConfig:

    def __init__(self, 
                 max_tokens=16000,
                 timeout=60,
                 temperature=0.2,
                 top_p=0.01):
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.temperature = temperature
        self.top_p = top_p
 
    def get_config(self):
        return {
            "max_tokens": self.max_tokens,
            "timeout": self.timeout,
            "temperature": self.temperature,
            # "top_p": self.top_p
        }
