def find_conda_executable():
    """
    Find conda executable using multiple detection methods.
    Returns the path to conda executable or None if not found.
    """
    import subprocess
    import os
    import shutil
    
    # Method 1: Try to find conda in PATH
    conda_exe = shutil.which("conda")
    if conda_exe:
        try:
            result = subprocess.run([conda_exe, "--version"], capture_output=True, text=True)
            if result.returncode == 0:
                return conda_exe
        except (FileNotFoundError, subprocess.CalledProcessError):
            pass
    
    # Method 2: Check common installation directories
    home_dir = os.path.expanduser("~")
    possible_conda_paths = [
        "/opt/conda/bin/conda",
        "/usr/local/bin/conda",
        "/usr/bin/conda",
        f"{home_dir}/miniconda3/bin/conda",
        f"{home_dir}/anaconda3/bin/conda",
        f"{home_dir}/conda/bin/conda",
        f"{home_dir}/.conda/bin/conda",
        f"{home_dir}/.local/bin/conda"
    ]
    
    for conda_path in possible_conda_paths:
        if os.path.exists(conda_path):
            try:
                result = subprocess.run([conda_path, "--version"], capture_output=True, text=True)
                if result.returncode == 0:
                    return conda_path
            except (FileNotFoundError, subprocess.CalledProcessError):
                continue
    
    # Method 3: Search in common conda installation patterns
    search_paths = [
        "/opt",
        "/usr/local",
        home_dir,
        f"{home_dir}/.local"
    ]
    
    for search_path in search_paths:
        if os.path.exists(search_path):
            for root, dirs, files in os.walk(search_path):
                # Limit depth to avoid searching too deep
                if root.count(os.sep) - search_path.count(os.sep) > 3:
                    dirs[:] = []
                    continue
                    
                if "conda" in root.lower() and "bin" in root:
                    conda_candidate = os.path.join(root, "conda")
                    if os.path.exists(conda_candidate):
                        try:
                            result = subprocess.run([conda_candidate, "--version"], capture_output=True, text=True)
                            if result.returncode == 0:
                                return conda_candidate
                        except (FileNotFoundError, subprocess.CalledProcessError):
                            continue
    
    return None


def run_python_script(conda_environment_name: str, task_name: str, iteration_number: int, script_name: str):
    """
    Run a Python script directly with a conda environment using WSL (Windows Subsystem for Linux) compatibility.
    """
    import subprocess
    import os

    # Construct the path to the script (absolute path)
    script_path = os.path.abspath(os.path.join("sandbox", "code", task_name, str(iteration_number), script_name))

    # Find conda executable
    conda_exe = find_conda_executable()
    if not conda_exe:
        print("Error: Could not find conda executable")
        return

    # Construct the command to properly initialize conda and run the script
    # Change to the script directory first to ensure relative paths work correctly
    script_dir = os.path.dirname(script_path)
    command = f"""
    cd {script_dir} && \
    source {os.path.dirname(conda_exe)}/../etc/profile.d/conda.sh && \
    conda activate {conda_environment_name} && \
    python {os.path.basename(script_path)}
    """

    # Execute the command using subprocess
    try:
        result = subprocess.run(command, shell=True, check=True, text=True, capture_output=True, executable="/bin/bash")
        print("Script output:", result.stdout)
        if result.stderr:
            print("Script stderr:", result.stderr)
        return result.stdout
    except subprocess.CalledProcessError as e:
        print("An error occurred while running the script:", e.stderr)
        return e.stderr


def install_package(conda_environment_name: str, package_name: str, use_conda: bool = False):
    """
    Install a package in the specified conda environment using either pip or conda.
    
    Args:
        conda_environment_name: Name of the conda environment
        package_name: Name of the package to install
        use_conda: If True, use conda install, otherwise use pip install
    """
    import subprocess
    import os

    # Find conda executable
    conda_exe = find_conda_executable()
    if not conda_exe:
        print("Error: Could not find conda executable")
        return

    # Choose installation command
    if use_conda:
        install_cmd = f"conda install -y {package_name}"
    else:
        install_cmd = f"pip install {package_name}"

    # Construct the command to properly initialize conda and install package
    command = f"""
    source {os.path.dirname(conda_exe)}/../etc/profile.d/conda.sh && \
    conda activate {conda_environment_name} && \
    {install_cmd}
    """

    # Execute the command using subprocess
    try:
        print(f"Installing {package_name} using {'conda' if use_conda else 'pip'}...")
        result = subprocess.run(command, shell=True, check=True, text=True, capture_output=True, executable="/bin/bash")
        print("Installation output:", result.stdout)
        if result.stderr:
            print("Installation stderr:", result.stderr)
        print(f"Successfully installed {package_name}")
        return result.stdout
    except subprocess.CalledProcessError as e:
        print(f"An error occurred while installing {package_name}:", e.stderr)
        return e.stderr


if __name__ == "__main__":
    # Test the conda detection
    conda_exe = find_conda_executable()
    print(f"Found conda at: {conda_exe}")

    install_package("mcp_creation", "pydantic")
    # Test running a script
    print("\n--- Testing script execution ---")
    run_python_script("mcp_creation", "mcp_creation", 0, "test.py")
    
 