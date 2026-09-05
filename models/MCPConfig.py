import json
import os
import asyncio
from models.locations import CONFIGS_DIR

MCP_SET_POLICY_KEY = "_mcp_set_policy"

"""
MCPConfig is a class that contains the configuration for the MCP tools.

It is designed to be used as standard template for creating MCP tools. 
"""


def is_reserved_mcp_set_key(key: str) -> bool:
    """Return True for metadata keys that must never be launched as MCP servers."""
    return str(key).startswith("_")


def load_mcp_set_document(config_name: str) -> dict:
    """Load one MCP set JSON document from the configs directory."""
    config_path = os.path.join(CONFIGS_DIR, config_name)
    try:
        with open(config_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        raise FileNotFoundError(
            f"MCP config file not found at {config_path}. Please copy "
            "mcp_configs.json.example to mcp_configs.json and update the values."
        )
    except json.JSONDecodeError:
        raise ValueError(f"Invalid JSON format in MCP config file at {config_path}")
    if not isinstance(payload, dict):
        raise ValueError(f"MCP config file must be a JSON object: {config_path}")
    return payload


def load_mcp_set_policy(config_name: str | None) -> dict:
    """Return the reserved `_mcp_set_policy` object for one MCP set."""
    if not str(config_name or "").strip():
        return {}
    try:
        document = load_mcp_set_document(str(config_name).strip())
    except FileNotFoundError:
        return {}
    policy = document.get(MCP_SET_POLICY_KEY) or {}
    return dict(policy) if isinstance(policy, dict) else {}


def load_mcp_set_extraction_validation(config_name: str | None) -> dict:
    """Return extraction_validation owned by an MCP set, if declared."""
    validation = load_mcp_set_policy(config_name).get("extraction_validation") or {}
    return dict(validation) if isinstance(validation, dict) else {}


def load_mcp_set_tool_purposes(config_name: str | None) -> dict[str, str]:
    """Return prompt-facing tool purposes owned by an MCP set."""
    purposes = load_mcp_set_policy(config_name).get("tool_purposes") or {}
    if not isinstance(purposes, dict):
        return {}
    return {
        str(name).strip(): str(text).strip()
        for name, text in purposes.items()
        if str(name).strip() and str(text).strip()
    }


class MCPConfig:

    def __init__(self, config_name: str = "mcp_configs.json"): 
        self.mcp_configs = load_mcp_set_document(config_name)


    async def is_docker_running(self):
        try:
            process = await asyncio.create_subprocess_exec(
                'docker', 'info',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
        except (FileNotFoundError, OSError):
            return False
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
                        config[k] = _convert_windows_path_to_linux(os.path.expandvars(v))
                    elif isinstance(v, (dict, list)):
                        config[k] = _convert_config_paths(v)
            elif isinstance(config, list):
                for i, v in enumerate(config):
                    if isinstance(v, str):
                        config[i] = _convert_windows_path_to_linux(os.path.expandvars(v))
                    elif isinstance(v, (dict, list)):
                        config[i] = _convert_config_paths(v)
            return config

        if len(mcp_name_list) == 1 and mcp_name_list[0] == "all":
            configs = {
                k: v
                for k, v in self.mcp_configs.items()
                if not is_reserved_mcp_set_key(k)
            }
        elif len(mcp_name_list) == 0:
            configs = {}
        else:
            configs = {
                k: v
                for k, v in self.mcp_configs.items()
                if k in mcp_name_list and not is_reserved_mcp_set_key(k)
            }
        # Convert Windows paths to Linux if needed
        configs = _convert_config_paths(configs)
        return configs


    

