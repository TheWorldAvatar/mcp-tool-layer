"""Lightweight process logger used by MCP servers and the extraction runtime."""

from __future__ import annotations

import json
import logging
import sys
import traceback
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Any, Optional

from models.locations import DATA_LOG_DIR


def _configure_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_configure_utf8_stdio()

_global_logger: "GlobalLogger | None" = None
_initialized = False


class GlobalLogger:
    def __init__(self) -> None:
        self.loggers: dict[str, logging.Logger] = {}
        self.base_log_dir = Path(DATA_LOG_DIR)
        self.base_log_dir.mkdir(parents=True, exist_ok=True)
        self.log_filename: str | None = None
        self.log_path: Path | None = None

    def initialize_log_file(self) -> None:
        if self.log_filename is not None:
            return
        self.log_filename = "agent.log"
        self.log_path = self.base_log_dir / self.log_filename
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def get_logger(self, component_name: str, script_name: Optional[str] = None) -> logging.Logger:
        logger_name = f"{component_name}_{script_name}" if script_name else component_name
        if logger_name in self.loggers:
            return self.loggers[logger_name]
        if self.log_path is None:
            self.initialize_log_file()
        logger = logging.getLogger(logger_name)
        logger.setLevel(logging.INFO)
        if logger.handlers:
            self.loggers[logger_name] = logger
            return logger
        formatter = logging.Formatter(
            "[%(asctime)s] [%(name)s] [%(filename)s:%(lineno)d] %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        file_handler = logging.FileHandler(self.log_path, encoding="utf-8")
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setLevel(logging.WARNING)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
        self.loggers[logger_name] = logger
        return logger

    def log_mcp_tool_call(
        self,
        tool_name: str,
        inputs: dict,
        outputs: Any,
        error: Optional[Exception] = None,
    ) -> None:
        logger = self.get_logger("mcp_tool", tool_name)
        log_data = {
            "tool_name": tool_name,
            "timestamp": datetime.now().isoformat(),
            "inputs": inputs,
            "outputs": str(outputs) if outputs is not None else None,
            "error": str(error) if error else None,
            "traceback": traceback.format_exc() if error else None,
        }
        payload = json.dumps(log_data, indent=2, default=str)
        if error:
            logger.error("MCP Tool Error: %s", payload)
        else:
            logger.info("MCP Tool Call: %s", payload)


def get_global_logger() -> GlobalLogger:
    global _global_logger
    if _global_logger is None:
        _global_logger = GlobalLogger()
    return _global_logger


def initialize_logging() -> None:
    global _initialized
    if not _initialized:
        get_global_logger().initialize_log_file()
        _initialized = True


def get_logger(component_name: str, script_name: Optional[str] = None) -> logging.Logger:
    return get_global_logger().get_logger(component_name, script_name)


def log_mcp_tool_call(
    tool_name: str,
    inputs: dict,
    outputs: Any,
    error: Optional[Exception] = None,
) -> None:
    get_global_logger().log_mcp_tool_call(tool_name, inputs, outputs, error)


def mcp_tool_logger(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        inputs = {"args": args, "kwargs": kwargs}
        try:
            outputs = func(*args, **kwargs)
            log_mcp_tool_call(func.__name__, inputs, outputs)
            return outputs
        except Exception as exc:
            log_mcp_tool_call(func.__name__, inputs, None, exc)
            raise

    return wrapper
