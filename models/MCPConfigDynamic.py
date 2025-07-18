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
 
    "generic": {
        "command": "python",
        "args": [_p(MCP_SRC / "generic" / "main.py")],
        "transport": "stdio",
    },
    "sandbox": {
        "command": "python",
        "args": [_p(MCP_SRC / "sandbox" / "main.py")],
        "transport": "stdio",
    },

    "docker": {
        "command": "python",
        "args": [_p(MCP_SRC / "docker" / "main.py")],
        "transport": "stdio",
    },

    "stack_operations": {
        "command": "python",
        "args": [_p(MCP_SRC / "stack" / "main.py")],
        "transport": "stdio",
    },
    "task_operations": {
        "command": "python",
        "args": [_p(MCP_SRC / "task" / "main.py")],
        "transport": "stdio",
    }
}

def get_mcp_configs(names: List[str] | None = None) -> Dict[str, Dict]:
    return _ALL_MCP_CONFIGS if not names else {k: v for k, v in _ALL_MCP_CONFIGS.items() if k in names}


def create_client(names: List[str] | None = None) -> MultiServerMCPClient:
    return MultiServerMCPClient(get_mcp_configs(names))

if __name__ == "__main__":
    pass 