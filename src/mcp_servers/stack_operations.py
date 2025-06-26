import json
import os
import shutil
from pathlib import Path
from mcp.server.fastmcp import FastMCP
import subprocess
import time
mcp = FastMCP("StackOperations")



def create_ontocompchem_config():
    """Create the ontocompchem.json configuration file"""
    config_data = {
        "name": "ontocompchem",
        "database": "postgres",
        "datasetDirectory": "ontocompchem",
        "dataSubsets": [
            {
                "type": "tabular",
                "skip": False,
                "schema": "public",
                "subdirectory": "csv_data_file"
            }
        ],
        "mappings": [
            "ontocompchem.obda"
        ]
    }
    
    config_path = r"C:\Users\xz378\Documents\GitHub\stack\stack-data-uploader\inputs\config\ontocompchem.json"
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    
    with open(config_path, 'w') as f:
        json.dump(config_data, f, indent=4)
    
    return f"Created/overwritten config file at: {config_path}"

def copy_obda_file():
    """Copy the ontocompchem.obda file to the target directory"""
    source_path = "data/test/ontocompchem.obda"
    target_dir = r"C:\Users\xz378\Documents\GitHub\stack\stack-data-uploader\inputs\data\ontocompchem"
    
    os.makedirs(target_dir, exist_ok=True)
    target_path = os.path.join(target_dir, "ontocompchem.obda")
    
    shutil.copy2(source_path, target_path)
    return f"Copied/overwritten obda file to: {target_path}"

def copy_csv_file():
    """Copy the ontocompchem.csv file to the csv_data_file subdirectory"""
    source_path = "data/test/ontocompchem.csv"
    target_dir = r"C:\Users\xz378\Documents\GitHub\stack\stack-data-uploader\inputs\data\ontocompchem\csv_data_file"
    
    os.makedirs(target_dir, exist_ok=True)
    target_path = os.path.join(target_dir, "ontocompchem.csv")
    
    shutil.copy2(source_path, target_path)
    return f"Copied/overwritten csv file to: {target_path}"

def setup_ontocompchem_stack():
    """Main function to set up the ontocompchem stack configuration"""
    try:
        result1 = create_ontocompchem_config()
        result2 = copy_obda_file()
        result3 = copy_csv_file()
        
        return {
            "status": "success",
            "messages": [result1, result2, result3]
        }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e)
        }

@mcp.tool()
def initialize_stack(stack_name: str):
    """
    
    Initialize the semantic database stack by running the stack.sh start <stack_name> command in stack-manager dir
    
    The stack is an already made docker stack that offers a SPARQL endpoint, a postgres database and a ontop endpoint.

    This is mandatory for the entire semantic data pipeline, i.e., any data introduced will be stored in the stack, and all the queries will be executed against the stack.
    
    **Important**: The stack is the only way to store the data, and the only way to query the data.
    
    """
    setup_ontocompchem_stack()
    import subprocess
    # Replace this path with the actual mounted WSL path to your stack dir
    wsl_stack_path = "/mnt/c/Users/xz378/Documents/GitHub/stack/stack-manager"
    full_cmd = f'wsl bash -c "cd {wsl_stack_path} && ./stack.sh start {stack_name}"'
    result = subprocess.run(full_cmd, capture_output=True, text=True)
    time.sleep(60)
    print("STDOUT:", result.stdout)
    print("STDERR:", result.stderr)
    return result


@mcp.tool()
def update_stack_database(stack_command: str):
    """
    This function is used to update the data in the stack. The data uploaded include the data the csv file, the obda file and the ttl file.

    Only after the data is uploaded to the stack, the data can be queried.
    
    Run a stack command in WSL, the default command is ./stack.sh start <stack_name> in stack-data-uploader dir, which updates the data in the ontop endpoint"""	
    # Replace this path with the actual mounted WSL path to your stack dir
    wsl_stack_path = "/mnt/c/Users/xz378/Documents/GitHub/stack/stack-data-uploader"
    full_cmd = f'wsl bash -c "cd {wsl_stack_path} && {stack_command}"'
    result = subprocess.run(full_cmd, capture_output=True, text=True)
    time.sleep(60)  
    
    print("STDOUT:", result.stdout)
    print("STDERR:", result.stderr)
    return result

@mcp.tool()
# remove all existing stacks     
def remove_stack_data():
    """Remove all existing stack data, this must be done before initializing a new stack"""
    wsl_stack_path = "/mnt/c/Users/xz378/Documents/GitHub/stack/stack-manager"
    full_cmd = f'wsl bash -c "cd {wsl_stack_path} && ./stack.sh remove all"'
    result = subprocess.run(full_cmd, capture_output=True, text=True)
    time.sleep(60)
    return result
 



if __name__ == "__main__":
    mcp.run(transport="stdio")