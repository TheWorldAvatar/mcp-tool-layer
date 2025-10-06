from fastmcp import FastMCP
from src.mcp_servers.simple_sandbox.operations import run_python_script, install_package

mcp = FastMCP()

@mcp.prompt(name="instruction")
def instruction_prompt():
    return """
    This server provides tools to run Python scripts and install packages in a conda environment.

    Tools:
    - run_python_script_tool(conda_environment_name: str, task_name: str, iteration_number: int, script_name: str)
    - install_package_tool(conda_environment_name: str, package_name: str)
    """

@mcp.tool(name="run_python_script_tool", description="Run a Python script in a conda environment")
def run_python_script_tool(conda_environment_name: str, task_name: str, iteration_number: int, script_name: str):
    return run_python_script(conda_environment_name, task_name, iteration_number, script_name)

@mcp.tool(name="install_package_tool", description="Install a package in a conda environment")
def install_package_tool(conda_environment_name: str, package_name: str):
    return install_package(conda_environment_name, package_name)


if __name__ == "__main__":
    mcp.run(transport="stdio")





