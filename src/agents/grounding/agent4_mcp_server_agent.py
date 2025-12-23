#!/usr/bin/env python3
"""
Agent 4: Create an MCP server wrapper + config entry for a generated Script C.

Input (domain-specific): the generated Script C python module file (by path).
Output:
1) MCP server `main.py` under `src/mcp_servers/<server_name>/main.py`
2) An entry in a config JSON (default: `configs/chemistry.json`) to run the server via stdio.

This agent does NOT depend on ontology TTL or endpoint directly; it wraps whatever Script C exports.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Optional

from src.agents.grounding._llm_script_agent_utils import LLMGenConfig, generate_python_module_with_repair


def _sanitize_server_name(name: str) -> str:
    name = name.strip()
    name = re.sub(r"[^a-zA-Z0-9_-]+", "_", name)
    return name.lower()


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _read_json(path: Path) -> Any:
    if not path.exists():
        # Allow creating a fresh config file.
        return {}
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {}
    return json.loads(text)


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _compose_prompt(*, server_name: str, script_c_text: str, ccdc_convention_text: str) -> str:
    # IMPORTANT: this function returns a prompt string; it must not accidentally trigger Python f-string
    # interpolation of `{...}` shapes in the instructions. Use doubled braces where needed.
    return f"""
You are a senior Python engineer.

Generate ONE Python module: an MCP server `main.py` that wraps a generated "Script C" module and exposes
its public query/lookup functions as MCP tools.

Hard requirements:
- Output MUST be valid Python code only (no markdown).
- Must follow the conventions/patterns in the provided reference server (`ccdc/main.py`):
  - dedicated logger writing to DATA_LOG_DIR and a server-specific log file
  - a decorator that logs tool calls; supports both sync and async functions
  - uses FastMCP(name=...) and @mcp.tool(...)
- The MCP tool functions MUST have Python type annotations on parameters and return type.
- The server must load Script C from a file path passed via CLI:
    --script-c <path>
  and optionally accept:
    --labels-dir <path>   (if Script C defines LABELS_DIR, override it)
- Do NOT download labels here; Script C already encapsulates that logic.

Tool exposure rules (based on Script C content):
- Expose functions that are intended public utilities, typically:
  - execute_sparql
  - list_*
  - get_*
  - lookup_*
  - fuzzy_lookup_*
- For each exposed function, create a thin MCP tool wrapper with a clear description and typed signature.
  The wrapper should call the Script C function and return JSON-serializable data (string/dict/list).
  If the Script C function returns non-serializable objects, convert them to str.
- Prefer explicit wrappers over dynamic registration; we want the wrappers to have type annotations.

CLI contract:
- main() should parse args, load Script C module (via importlib), optionally set LABELS_DIR, then register tools and run:
    mcp.run(transport="stdio")

Server name (for FastMCP and log naming): {server_name}

Reference implementation style (ccdc/main.py):
{ccdc_convention_text}

Script C module code:
{script_c_text}
""".strip()


def main(argv: Optional[list[str]] = None) -> None:
    p = argparse.ArgumentParser(description="Agent 4: generate MCP server + config for Script C.")
    p.add_argument("--script-c", required=True, help="Path to generated Script C module (python file)")
    p.add_argument("--server-name", required=True, help="Name for MCP server (and folder name under src/mcp_servers)")
    p.add_argument("--out-server-dir", default=None, help="Override output server directory (default: src/mcp_servers/<server-name>)")
    p.add_argument("--config", default="configs/chemistry.json", help="Config JSON file to update/add entry")
    p.add_argument("--config-key", default=None, help="Key name in config JSON (default: server-name)")
    p.add_argument("--labels-dir", default=None, help="Optional labels dir to pass in config args (forwarded to server)")
    p.add_argument("--model", default="gpt-4.1")
    args = p.parse_args(argv)

    server_name = _sanitize_server_name(args.server_name)
    config_key = _sanitize_server_name(args.config_key) if args.config_key else server_name

    out_server_dir = Path(args.out_server_dir) if args.out_server_dir else Path("src/mcp_servers") / server_name
    out_main = out_server_dir / "main.py"

    # LLM-generate main.py using Script C as the ONLY domain input, and ccdc/main.py as style reference.
    script_c_text = Path(args.script_c).read_text(encoding="utf-8")
    ccdc_convention_text = Path("src/mcp_servers/ccdc/main.py").read_text(encoding="utf-8")
    prompt = _compose_prompt(
        server_name=server_name,
        script_c_text=script_c_text,
        ccdc_convention_text=ccdc_convention_text,
    )
    generate_python_module_with_repair(
        prompt=prompt,
        out_path=out_main,
        cfg=LLMGenConfig(model=str(args.model).strip()),
        require_substrings=["FastMCP", "mcp.run", "def main", "->"],
    )

    config_path = Path(args.config)
    cfg = _read_json(config_path)
    if not isinstance(cfg, dict):
        raise ValueError(f"Config file must be a JSON object: {config_path}")

    entry_args = ["-m", f"src.mcp_servers.{server_name}.main", "--script-c", str(Path(args.script_c))]
    if args.labels_dir:
        entry_args += ["--labels-dir", args.labels_dir]

    cfg[config_key] = {
        "command": "python",
        "args": entry_args,
        "transport": "stdio",
    }

    _write_json(config_path, cfg)

    print(
        json.dumps(
            {
                "status": "ok",
                "server_main": str(out_main),
                "config": str(config_path),
                "config_key": config_key,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()


