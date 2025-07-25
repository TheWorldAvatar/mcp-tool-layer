import json
import os
import shutil
from pathlib import Path
import subprocess
import time
from models.locations import STACK_REPO_DIR
import os

def create_meta_task_config(meta_task_name: str, iteration_index: int):
    """Create the ontocompchem.json configuration file"""
    config_data = {
        "name": f"{meta_task_name}_{iteration_index}",
        "database": "postgres",
        "datasetDirectory": f"{meta_task_name}_{iteration_index}",
        "dataSubsets": [
            {
                "type": "tabular",
                "skip": False,
                "schema": "public",
                "subdirectory": "csv_data_file"
            }
        ],
        "mappings": [
            f"{meta_task_name}_{iteration_index}.obda"
        ]
    }

    # check os running, if it is windows, use the windows path, if it is linux, use the linux path

    # /mnt/c/Users/xz378/Documents/GitHub/stack
    if os.name == "nt":
        config_path = os.path.join(STACK_REPO_DIR, "stack-data-uploader", "inputs", "config", f"{meta_task_name}_{iteration_index}.json")
    else:
        config_path = os.path.join(STACK_REPO_DIR, "stack-data-uploader", "inputs", "config", f"{meta_task_name}_{iteration_index}.json")
    
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    
    with open(config_path, 'w') as f:
        json.dump(config_data, f, indent=4)
    
    return f"Created/overwritten config file at: {config_path}"

def copy_obda_file(meta_task_name: str, iteration_index: int):
    """Copy the ontocompchem.obda file to the target directory"""
    source_path = f"sandbox/data/{meta_task_name}/{iteration_index}/{meta_task_name}_{iteration_index}.obda"
    
    
    if os.name == "nt":
        target_dir = os.path.join(STACK_REPO_DIR, "stack-data-uploader", "inputs", "data", f"{meta_task_name}_{iteration_index}")
    else:
        target_dir = os.path.join(STACK_REPO_DIR, "stack-data-uploader", "inputs", "data", f"{meta_task_name}_{iteration_index}")
    
    os.makedirs(target_dir, exist_ok=True)
    target_path = os.path.join(target_dir, f"{meta_task_name}_{iteration_index}.obda")
    
    shutil.copy2(source_path, target_path)
    return f"Copied/overwritten obda file to: {target_path}"

def copy_csv_file(csv_file_relative_path: str, meta_task_name: str, iteration_index: int):
    """Copy the ontocompchem.csv file to the csv_data_file subdirectory"""
    
    target_dir = os.path.join(STACK_REPO_DIR, "stack-data-uploader", "inputs", "data", f"{meta_task_name}_{iteration_index}", "csv_data_file")
    os.makedirs(target_dir, exist_ok=True)
    target_path = os.path.join(target_dir, f"{meta_task_name}_{iteration_index}.csv") # rename the file to the meta_task_name_iteration_index.csv. 
    shutil.copy2(csv_file_relative_path, target_path)
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

def _initialize_stack(stack_name: str):
    setup_ontocompchem_stack()
    import subprocess
    # Replace this path with the actual mounted WSL path to your stack dir
    wsl_stack_path = os.path.join(STACK_REPO_DIR, "stack-manager")
    full_cmd = f"cd {wsl_stack_path} && ./stack.sh start {stack_name}"
    result = subprocess.run(full_cmd, capture_output=True, text=True, shell=True)
    time.sleep(60)
    return result

def _update_stack_database(stack_name: str):
    # Replace this path with the actual mounted WSL path to your stack dir
    wsl_stack_path = os.path.join(STACK_REPO_DIR, "stack-data-uploader")
    full_cmd = f"cd {wsl_stack_path} && ./stack.sh start {stack_name}"
    result = subprocess.run(full_cmd, capture_output=True, text=True, shell=True)
    time.sleep(60)
    return result

def _remove_stack_data():
    wsl_stack_path = os.path.join(STACK_REPO_DIR, "stack-manager")
    full_cmd = f"cd {wsl_stack_path} && ./stack.sh remove all"
    result = subprocess.run(full_cmd, capture_output=True, text=True, shell=True)
    time.sleep(60)
    return result

def initialize_stack(stack_name: str):
    return _initialize_stack(stack_name)    

def update_stack_database(stack_name: str):
    return _update_stack_database(stack_name)

def remove_stack_data():
    return _remove_stack_data() 



if __name__ == "__main__":
    create_meta_task_config("ontocompchem", 1)
    copy_obda_file("ontocompchem", 1)
    copy_csv_file("sandbox/data/ontocompchem/1/ontocompchem.csv", "ontocompchem", 1)
    remove_stack_data()
    initialize_stack("ontocompchem")
    update_stack_database("ontocompchem")
    