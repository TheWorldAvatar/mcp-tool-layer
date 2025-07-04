import json
import os
import asyncio
from models.locations import CONFIGS_DIR

"""
MCPConfig is a class that contains the configuration for the MCP tools.

It is designed to be used as standard template for creating MCP tools. 
"""	

class MCPConfig:

    def __init__(self, config_name: str = "mcp_configs.json"): 
        # load mcp_configs.json
        config_path = os.path.join(CONFIGS_DIR, config_name)
        try:
            with open(config_path, "r") as f:
                self.mcp_configs = json.load(f)
        except FileNotFoundError:
            raise FileNotFoundError(f"MCP config file not found at {config_path}. Please copy mcp_configs.json.example to mcp_configs.json and update the values.")
        except json.JSONDecodeError:
            raise ValueError(f"Invalid JSON format in MCP config file at {config_path}")
        except Exception as e:
            raise Exception(f"Error loading MCP config file: {str(e)}")


    async def is_docker_running(self):
        process = await asyncio.create_subprocess_exec(
            'docker', 'info',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        return process.returncode == 0

    def get_config(self, mcp_name_list: list[str]):
        if len(mcp_name_list) == 1 and mcp_name_list[0] == "all":
            return {k: v for k, v in self.mcp_configs.items()}
        return {k: v for k, v in self.mcp_configs.items() if k in mcp_name_list}


    

