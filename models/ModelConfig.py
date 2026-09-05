"""
ModelConfig is a class that contains the configuration for the LLM model.
"""

class ModelConfig:

    def __init__(self, 
                 max_tokens=None,
                 timeout=600,
                 temperature=0.0,
                 top_p=0.01,
                 max_retries=None,
                 seed=None,
                 extra_body=None,
                 ):
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.temperature = temperature
        self.top_p = top_p
        self.max_retries = max_retries
        self.seed = seed
        self.extra_body = extra_body

    def _with_retries(self, config):
        if self.max_retries is not None:
            config["max_retries"] = self.max_retries
        if self.seed is not None:
            config["seed"] = int(self.seed)
        if self.extra_body:
            config["extra_body"] = dict(self.extra_body)
        return config

    def get_config(self, model_name: str):
        if model_name == "o3-mini":
            return self._with_retries({
                "timeout": self.timeout,
                "temperature": self.temperature,
                # "top_p": self.top_p
            })

        elif model_name in ["gpt-4o-mini-search-preview", "gpt-4o-search-preview", "o1", "o3-mini", "o3-mini-high", "gpt-5", "o3"]:
            return self._with_retries({
                "timeout": self.timeout
            })


        else:
            return self._with_retries({
                # "max_tokens": self.max_tokens,
                "timeout": self.timeout,
                "temperature": self.temperature,
                "top_p": self.top_p
            })
