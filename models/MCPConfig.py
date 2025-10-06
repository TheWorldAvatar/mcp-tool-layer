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
        import sys

        def _convert_windows_path_to_linux(path):
            # Only convert if running on Linux and path looks like a Windows path
            if sys.platform.startswith("linux") and path and ":" in path:
                # Example: "C:/Users/xz378/Documents/GitHub/mcp-tool-layer/src/mcp_servers/llm_generation_server.py"
                drive, rest = path.split(":", 1)
                drive = drive.lower()
                # Remove leading slash if present
                rest = rest.lstrip("\\/")  
                # Compose Linux path
                rest_fixed = rest.replace("\\", "/")
                return f"/mnt/{drive}/{rest_fixed}"
            return path

        def _convert_config_paths(config):
            # Recursively convert all string paths in config dict/list
            if isinstance(config, dict):
                for k, v in config.items():
                    if isinstance(v, str):
                        config[k] = _convert_windows_path_to_linux(v)
                    elif isinstance(v, (dict, list)):
                        config[k] = _convert_config_paths(v)
            elif isinstance(config, list):
                for i, v in enumerate(config):
                    if isinstance(v, str):
                        config[i] = _convert_windows_path_to_linux(v)
                    elif isinstance(v, (dict, list)):
                        config[i] = _convert_config_paths(v)
            return config

        if len(mcp_name_list) == 1 and mcp_name_list[0] == "all":
            configs = {k: v for k, v in self.mcp_configs.items()}
        elif len(mcp_name_list) == 0:
            configs = {}
        else:
            configs = {k: v for k, v in self.mcp_configs.items() if k in mcp_name_list}
        # Convert Windows paths to Linux if needed
        configs = _convert_config_paths(configs)
        return configs


    

