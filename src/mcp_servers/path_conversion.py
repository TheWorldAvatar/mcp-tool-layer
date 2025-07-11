import os
import shutil
import subprocess
from pathlib import Path
from fastmcp import FastMCP
import logging

mcp = FastMCP("PathConversion")

@mcp.tool(name="convert_to_wsl_path", description="Convert a path to a WSL path. This helps you to convert the Windows path to a WSL path.")
def convert_to_wsl_path(path: str) -> str:
  
 
    path = path.strip().replace('"', '').replace("'", "")

    # If already a WSL path, return as is
    if path.startswith("/mnt/"):
        return path

    # Replace backslashes with forward slashes
    path = path.replace("\\", "/")

    # Extract drive letter
    if len(path) > 2 and path[1] == ":":
        drive_letter = path[0].lower()
        rest = path[2:]
        # Remove leading slash if present
        if rest.startswith("/"):
            rest = rest[1:]
        wsl_path = f"/mnt/{drive_letter}/{rest}"
        return wsl_path
    else:
        # Not a Windows absolute path, return as is
        return path


if __name__ == "__main__":
    mcp.run(transport="stdio")