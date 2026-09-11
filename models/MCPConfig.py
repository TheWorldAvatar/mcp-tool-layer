"""Load human-written MCP set JSON documents and their reserved policy blocks."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from models.locations import CONFIGS_DIR, MCP_CONFIGS_DIR, repository_root

MCP_SET_POLICY_KEY = "_mcp_set_policy"


def is_reserved_mcp_set_key(key: str) -> bool:
    """Return True for metadata keys that must never be launched as MCP servers."""
    return str(key).startswith("_")


def resolve_mcp_set_path(config_name: str) -> Path:
    """Resolve an MCP set filename under configs/mcp, then configs/."""
    name = str(config_name or "").strip()
    if not name:
        raise FileNotFoundError("MCP set name is empty")
    candidate = Path(name)
    if candidate.is_absolute() and candidate.is_file():
        return candidate
    search = [
        Path(MCP_CONFIGS_DIR) / name,
        Path(CONFIGS_DIR) / name,
        Path(CONFIGS_DIR) / "mcp" / name,
    ]
    for path in search:
        if path.is_file():
            return path
    raise FileNotFoundError(
        f"MCP config file not found for {name!r}. Looked in: "
        + ", ".join(str(item) for item in search)
    )


def load_mcp_set_document(config_name: str) -> dict:
    """Load one MCP set JSON document."""
    config_path = resolve_mcp_set_path(config_name)
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON format in MCP config file at {config_path}") from exc
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


_BARE_PYTHON_COMMANDS = {
    "python",
    "python.exe",
    "python3",
    "python3.exe",
    "py",
    "py.exe",
}


def _normalize_mcp_launch_config(config: dict) -> dict:
    """Launch MCP children with this interpreter, repo cwd, and repo PYTHONPATH."""
    import sys

    if not isinstance(config, dict):
        return config
    out = dict(config)
    command = str(out.get("command") or "").strip()
    if Path(command).name.lower() in _BARE_PYTHON_COMMANDS:
        out["command"] = sys.executable
    repo = str(repository_root())
    out.setdefault("cwd", repo)
    env = dict(out.get("env") or {})
    existing = str(env.get("PYTHONPATH") or "").strip()
    parts = [part for part in existing.split(os.pathsep) if part]
    if repo not in parts:
        env["PYTHONPATH"] = repo if not existing else f"{repo}{os.pathsep}{existing}"
    out["env"] = env
    args = list(out.get("args") or [])
    resolved_args: list[Any] = []
    for arg in args:
        if not isinstance(arg, str) or arg.startswith("-"):
            resolved_args.append(arg)
            continue
        path = Path(arg)
        if path.is_absolute():
            resolved_args.append(arg)
            continue
        candidate = Path(repo) / arg
        if candidate.is_file():
            resolved_args.append(str(candidate))
        else:
            resolved_args.append(arg)
    if args:
        out["args"] = resolved_args
    return out


class MCPConfig:
    """Launch map for named MCP servers in one set document."""

    def __init__(self, config_name: str = "mcp_configs.json"):
        self.mcp_configs = load_mcp_set_document(config_name)

    async def is_docker_running(self) -> bool:
        try:
            process = await asyncio.create_subprocess_exec(
                "docker",
                "info",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (FileNotFoundError, OSError):
            return False
        await process.communicate()
        return process.returncode == 0

    def get_config(self, mcp_name_list: list[str]) -> dict:
        import sys

        def _convert_windows_path_to_linux(path: str) -> str:
            if sys.platform.startswith("linux") and path and ":" in path:
                drive, rest = path.split(":", 1)
                rest_fixed = rest.lstrip("\\/").replace("\\", "/")
                return f"/mnt/{drive.lower()}/{rest_fixed}"
            return path

        def _convert_config_paths(config):
            if isinstance(config, dict):
                return {
                    key: (
                        _convert_windows_path_to_linux(os.path.expandvars(value))
                        if isinstance(value, str)
                        else _convert_config_paths(value)
                    )
                    for key, value in config.items()
                }
            if isinstance(config, list):
                return [
                    _convert_windows_path_to_linux(os.path.expandvars(value))
                    if isinstance(value, str)
                    else _convert_config_paths(value)
                    for value in config
                ]
            return config

        if len(mcp_name_list) == 1 and mcp_name_list[0] == "all":
            configs = {
                key: value
                for key, value in self.mcp_configs.items()
                if not is_reserved_mcp_set_key(key)
            }
        elif not mcp_name_list:
            configs = {}
        else:
            configs = {
                key: value
                for key, value in self.mcp_configs.items()
                if key in mcp_name_list and not is_reserved_mcp_set_key(key)
            }
        converted = _convert_config_paths(configs)
        return {
            key: _normalize_mcp_launch_config(value)
            for key, value in converted.items()
        }
