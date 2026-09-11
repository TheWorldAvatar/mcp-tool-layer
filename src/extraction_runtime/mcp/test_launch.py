"""Write a temporary MCP set from domain `mcp_capabilities`, not meta_task."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from models.locations import repository_root
from src.extraction_runtime.artifact_root import generated_artifact_root, scripts_dir
from src.extraction_runtime.domain_binding import RuntimeDomain


def _generated_server(
    ontology_name: str,
    *,
    data_dir: Path,
    main_ontology_name: str,
) -> dict[str, Any]:
    artifact_root = generated_artifact_root()
    python_cmd = sys.executable or "python"
    repo = str(repository_root())
    common_env = {
        "PYTHONPATH": repo,
        "PYTHONIOENCODING": "utf-8",
        "TWA_GENERATED_ARTIFACT_ROOT": str(artifact_root),
        "TWA_AGENTIC_DATA_DIR": str(data_dir.resolve()),
        "TWA_MAIN_ONTOLOGY_NAME": main_ontology_name,
        "TWA_LLM_SEED": "42",
        "TWA_MCP_TOOL_DESCRIPTIONS_ENABLED": os.environ.get(
            "TWA_MCP_TOOL_DESCRIPTIONS_ENABLED", "0"
        ),
        "TWA_SEMANTIC_OPERATION_SURFACE": os.environ.get(
            "TWA_SEMANTIC_OPERATION_SURFACE", "1"
        ),
    }
    main_py = scripts_dir(ontology_name) / "main.py"
    if not main_py.is_file():
        raise FileNotFoundError(f"Generated MCP main.py not found: {main_py}")
    if artifact_root.name.startswith("generated") or (artifact_root / "scripts").is_dir():
        launcher = artifact_root / f"_launch_{ontology_name}_mcp.py"
        launcher.write_text(
            "\n".join(
                [
                    "from __future__ import annotations",
                    "import sys",
                    "from pathlib import Path",
                    f"ARTIFACT_ROOT = Path({str(artifact_root)!r})",
                    "SCRIPTS_ROOT = ARTIFACT_ROOT / 'scripts'",
                    "sys.path.insert(0, str(SCRIPTS_ROOT))",
                    f"from {ontology_name}.main import mcp",
                    'mcp.run(transport="stdio")',
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return {
            "command": python_cmd,
            "args": [str(launcher)],
            "transport": "stdio",
            "cwd": repo,
            "env": dict(common_env),
        }
    return {
        "command": python_cmd,
        "args": ["-m", f"{artifact_root.name}.scripts.{ontology_name}.main"],
        "transport": "stdio",
        "cwd": repo,
        "env": dict(common_env),
    }


def _capability_tools(capabilities: dict[str, Any], *keys: str) -> list[str]:
    tools: list[str] = []
    for key in keys:
        block = capabilities.get(key) or {}
        if isinstance(block, dict):
            for name in block.get("tools") or []:
                text = str(name).strip()
                if text and text not in tools:
                    tools.append(text)
    return tools


def setup_test_mcp_configs(domain: RuntimeDomain, *, data_dir: str | Path) -> str:
    """Write `configs/test_mcp_config_<ontology>_<run>.json` and return its filename."""
    runtime = Path(data_dir).resolve()
    capabilities = domain.mcp_capabilities
    kg_tools = _capability_tools(capabilities, "kg_building")
    extension_tools = _capability_tools(capabilities, "extensions")
    if not kg_tools:
        raise RuntimeError("domain mcp_capabilities.kg_building.tools is empty")

    test_mcp_config: dict[str, Any] = {}
    main_ontology_name = domain.pipeline_main_ontology_name
    main_server = _generated_server(
        domain.ontology_name,
        data_dir=runtime,
        main_ontology_name=main_ontology_name,
    )
    for tool_name in kg_tools:
        test_mcp_config[tool_name] = dict(main_server)

    python_cmd = sys.executable or "python"
    repo = str(repository_root())
    for extension in domain.extensions:
        ext_name = str(extension.get("name") or "").strip()
        ext_tools = [str(item).strip() for item in (extension.get("mcp_list") or []) if str(item).strip()]
        if not ext_name or not ext_tools:
            continue
        try:
            ext_server = _generated_server(
                ext_name,
                data_dir=runtime,
                main_ontology_name=main_ontology_name,
            )
        except FileNotFoundError as exc:
            print(f"[WARN] Skipping generated extension MCP {ext_name}: {exc}")
            ext_server = None
        for name in ext_tools:
            if name == "ccdc":
                ccdc_env = {**os.environ, **(main_server.get("env") or {})}
                try:
                    from src.mcp_servers.ccdc.operations.wsl_ccdc import (
                        resolve_csd_python_exe,
                    )

                    ccdc_env["CSD_PYTHON_EXE"] = resolve_csd_python_exe()
                except Exception as exc:
                    print(f"[WARN] CSD python resolve failed ({exc})")
                ccdc_env["CSD_CONDA_ENV"] = os.environ.get("CSD_CONDA_ENV", "csd311")
                test_mcp_config[name] = {
                    "command": python_cmd,
                    "args": ["-m", "src.mcp_servers.ccdc.main"],
                    "transport": "stdio",
                    "cwd": repo,
                    "env": ccdc_env,
                }
                continue
            if ext_server is None:
                continue
            test_mcp_config[name] = dict(ext_server)

    for name in extension_tools:
        test_mcp_config.setdefault(name, dict(main_server))

    safe_ontology = "".join(
        ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in domain.ontology_name
    )
    runtime_tag = runtime.parent.name
    safe_runtime = "".join(
        ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in runtime_tag
    )
    config_path = (
        repository_root() / "configs" / f"test_mcp_config_{safe_ontology}_{safe_runtime}.json"
    )
    config_path.write_text(json.dumps(test_mcp_config, indent=2) + "\n", encoding="utf-8")
    print(f"[OK] Created test MCP config: {config_path}")
    return config_path.name
