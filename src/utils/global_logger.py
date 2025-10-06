import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional
import traceback
import json
from functools import wraps
from models.locations import DATA_LOG_DIR

# Global logger instance
_global_logger = None
_initialized = False

class GlobalLogger:
    """
    Global logging system that creates timestamped log files for each component.
    Logs are saved to data/log/agent_{timestamp}.log format.
    """
    
    def __init__(self):
        self.loggers = {}
        self.base_log_dir = Path(DATA_LOG_DIR)
        self.base_log_dir.mkdir(parents=True, exist_ok=True)
        self.log_filename = None
        self.log_path = None
    
        
    def initialize_log_file(self):
        """
        Initialize the log file for this run. This should be called once at the start of the application.
        """
        if self.log_filename is None:
            self.log_filename = "agent.log"
            self.log_path = self.base_log_dir / self.log_filename
            
            # Ensure the log directory exists
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Log the initialization
            init_logger = logging.getLogger("system_init")
            init_logger.setLevel(logging.INFO)
            
            # Create formatter with source information
            formatter = logging.Formatter(
                '[%(asctime)s] [%(name)s] [%(filename)s:%(lineno)d] %(levelname)s - %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            )
            
            # File handler
            file_handler = logging.FileHandler(self.log_path, encoding='utf-8')
            file_handler.setLevel(logging.INFO)
            file_handler.setFormatter(formatter)
            init_logger.addHandler(file_handler)
            
        # Console handler - only show WARNING and above
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.WARNING)
        console_handler.setFormatter(formatter)
        init_logger.addHandler(console_handler)
            
            # init_logger.info(f"Logging system initialized. Log file: {self.log_path}")
            
            # Store the init logger
        self.loggers["system_init"] = init_logger
    
    def get_logger(self, component_name: str, script_name: Optional[str] = None) -> logging.Logger:
        """
        Get or create a logger for a specific component.
        
        Args:
            component_name: Name of the component (e.g., 'agent', 'mcp_server', 'engine')
            script_name: Name of the script/module (e.g., 'MetaAgent', 'docker_operations')
            
        Returns:
            Configured logger instance
        """
        # Create unique logger name
        logger_name = f"{component_name}_{script_name}" if script_name else component_name
        
        if logger_name in self.loggers:
            return self.loggers[logger_name]
        
        # Ensure log file is initialized
        if self.log_path is None:
            self.initialize_log_file()
        
        # Create logger
        logger = logging.getLogger(logger_name)
        logger.setLevel(logging.INFO)
        
        # Prevent duplicate handlers
        if logger.handlers:
            return logger
        
        # Create formatter with source information
        formatter = logging.Formatter(
            '[%(asctime)s] [%(name)s] [%(filename)s:%(lineno)d] %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        # File handler - use the same log file for all loggers
        file_handler = logging.FileHandler(self.log_path, encoding='utf-8')
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        
        # Console handler - only show WARNING and above
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.WARNING)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
        
        # Store logger
        self.loggers[logger_name] = logger
        
        return logger
    
    def log_mcp_tool_call(self, tool_name: str, inputs: dict, outputs: any, error: Optional[Exception] = None):
        """
        Log MCP tool calls with inputs, outputs, and errors.
        
        Args:
            tool_name: Name of the MCP tool
            inputs: Input parameters to the tool
            outputs: Output from the tool
            error: Exception if any occurred
        """
        logger = self.get_logger("mcp_tool", tool_name)

        # limit the single entity length of inputs["kwargs"], ["args"] for logging
        # but preserve full SPARQL queries for debugging
        if "kwargs" in inputs:
            inputs["kwargs"] = {k: v if (isinstance(v, str) and k == "query") else (v[:min(len(v), 1000)] if isinstance(v, str) else v) for k, v in inputs["kwargs"].items()}
        if "args" in inputs:
            inputs["args"] = [v[:min(len(v), 1000)] if isinstance(v, str) else v for v in inputs["args"]]
        
        log_data = {
            "tool_name": tool_name,
            "timestamp": datetime.now().isoformat(),
            "inputs": inputs,
            "outputs": str(outputs) if outputs is not None else None,
            "error": str(error) if error else None,
            "traceback": traceback.format_exc() if error else None
        }
        
        if error:
            logger.error(f"MCP Tool Error: {json.dumps(log_data, indent=2)}")
        else:
            logger.info(f"MCP Tool Call: {json.dumps(log_data, indent=2)}")

def get_global_logger() -> GlobalLogger:
    """Get the global logger instance."""
    global _global_logger
    if _global_logger is None:
        _global_logger = GlobalLogger()
    return _global_logger

def initialize_logging():
    """
    Initialize the logging system. This should be called once at the start of the application.
    Creates a single log file for the entire run.
    """
    global _initialized
    if not _initialized:
        logger = get_global_logger()
        logger.initialize_log_file()
        _initialized = True

def get_logger(component_name: str, script_name: Optional[str] = None) -> logging.Logger:
    """
    Convenience function to get a logger.
    
    Args:
        component_name: Name of the component
        script_name: Name of the script/module
        
    Returns:
        Configured logger instance
    """
    return get_global_logger().get_logger(component_name, script_name)

def log_mcp_tool_call(tool_name: str, inputs: dict, outputs: any, error: Optional[Exception] = None):
    """
    Convenience function to log MCP tool calls.
    Tries to serialize inputs and outputs, falls back to string if not possible.
    Args:
        tool_name: Name of the MCP tool
        inputs: Input parameters to the tool
        outputs: Output from the tool
        error: Exception if any occurred
    """
    def safe_serialize(obj):
        try:
            return json.loads(json.dumps(obj, default=str))
        except Exception:
            return str(obj)
    safe_inputs = safe_serialize(inputs)
    safe_outputs = safe_serialize(outputs)
    get_global_logger().log_mcp_tool_call(tool_name, safe_inputs, safe_outputs, error)

def mcp_tool_logger(func):
    """
    Decorator to automatically log MCP tool calls.
    
    Usage:
        @mcp_tool_logger
        def my_mcp_tool(param1: str, param2: int) -> str:
            # tool implementation
            return result
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        tool_name = func.__name__
        inputs = {
            "args": args,
            "kwargs": kwargs
        }
        
        try:
            outputs = func(*args, **kwargs)
            log_mcp_tool_call(tool_name, inputs, outputs)
            return outputs
        except Exception as e:
            log_mcp_tool_call(tool_name, inputs, None, e)
            raise
    
    return wrapper 