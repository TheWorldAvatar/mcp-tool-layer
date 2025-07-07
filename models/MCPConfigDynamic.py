import os
from pathlib import Path
from langchain_mcp_adapters.client import MultiServerMCPClient
from typing import Dict, List

ROOT = Path(
    os.getenv("MCP_PROJECT_ROOT", Path(__file__).resolve().parent.parent)
).resolve()
DATA = (ROOT / "data").resolve()
SANDBOX = (ROOT / "sandbox").resolve()
MCP_SRC = (ROOT / "src" / "mcp_servers").resolve()

def _p(p: Path) -> str:
    return p.as_posix()

_ALL_MCP_CONFIGS: Dict[str, Dict] = {
    "filesystem": {
        "command": "docker",
        "args": [
            "run", "-i", "--rm",
            "--mount", f"type=bind,src={_p(DATA)},dst=/data",
            "--mount", f"type=bind,src={_p(SANDBOX)},dst=/sandbox",
            "mcp/filesystem", "/data", "/sandbox",
        ],
        "transport": "stdio",
    },
    "docker": {
        "command": "python",
        "args": [_p(MCP_SRC / "docker_mcp.py")],
        "transport": "stdio",
    },
    "generic_file_operations": {
        "command": "python",
        "args": [_p(MCP_SRC / "generic_file_operations.py")],
        "transport": "stdio",
    },

    "python_code_sandbox": {
        "command": "python",
        "args": [_p(MCP_SRC / "python_sandbox.py")],
        "transport": "stdio",
    },

    "resource_registration": {
        "command": "python",
        "args": [_p(MCP_SRC / "resource_registration_server.py")],
        "transport": "stdio",
    }
}

def get_mcp_configs(names: List[str] | None = None) -> Dict[str, Dict]:
    return _ALL_MCP_CONFIGS if not names else {k: v for k, v in _ALL_MCP_CONFIGS.items() if k in names}


def create_client(names: List[str] | None = None) -> MultiServerMCPClient:
    return MultiServerMCPClient(get_mcp_configs(names))

if __name__ == "__main__":
    print(get_mcp_configs(["filesystem", "docker", "generic_file_operations", "python_code_sandbox", "resource_registration"]))