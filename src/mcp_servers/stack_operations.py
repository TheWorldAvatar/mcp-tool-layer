import json
import os
import shutil
from pathlib import Path
from mcp.server.fastmcp import FastMCP
import subprocess
import time
from src.mcp_descriptions.stack_operations import STACK_INITIALIZATION_DESCRIPTION, STACK_DATABASE_UPDATION_DESCRIPTION, STACK_DATA_REMOVAL_DESCRIPTION 

mcp_with_instructions = FastMCP(
    name="StackOperations",	
    instructions="""
       This server is critical for any data integration task to the existing stack/semantic database. 
       You must always use this server as final steps if the task is about data integration. 
    """,
    tags=["stack", "semantic database", "data integration"]
)



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

@mcp_with_instructions.tool(name="initialize_stack", description=STACK_INITIALIZATION_DESCRIPTION, tags=["stack"])
def initialize_stack(stack_name: str):
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


@mcp_with_instructions.tool(name="update_stack_database", description=STACK_DATABASE_UPDATION_DESCRIPTION, tags=["stack"])
def update_stack_database(stack_command: str):
    # Replace this path with the actual mounted WSL path to your stack dir
    wsl_stack_path = "/mnt/c/Users/xz378/Documents/GitHub/stack/stack-data-uploader"
    full_cmd = f'wsl bash -c "cd {wsl_stack_path} && {stack_command}"'
    result = subprocess.run(full_cmd, capture_output=True, text=True)
    time.sleep(60)  
    return result

@mcp_with_instructions.tool(name="remove_stack_data", description=STACK_DATA_REMOVAL_DESCRIPTION, tags=["stack"])
# remove all existing stacks     
def remove_stack_data():
    wsl_stack_path = "/mnt/c/Users/xz378/Documents/GitHub/stack/stack-manager"
    full_cmd = f'wsl bash -c "cd {wsl_stack_path} && ./stack.sh remove all"'
    result = subprocess.run(full_cmd, capture_output=True, text=True)
    time.sleep(60)
    return result
 
if __name__ == "__main__":
    mcp_with_instructions.run(transport="stdio")