import os
import json
from models.locations import DATA_LOG_DIR, SANDBOX_TASK_DIR, CONFIGS_DIR, SANDBOX_DATA_DIR, SANDBOX_CODE_DIR
import shutil


def remove_db_files():
    # .db files are in configs
    for file in os.listdir(CONFIGS_DIR):
        if file.endswith(".sqlite"):
            os.remove(os.path.join(CONFIGS_DIR, file))
    
def clear_sandbox_data_dir():
    # remove ever folder in the task data dir but keep the file in the SANDBOX_DATA_DIR root folder
    for folder in os.listdir(SANDBOX_DATA_DIR):
        if os.path.isdir(os.path.join(SANDBOX_DATA_DIR, folder)):
            shutil.rmtree(os.path.join(SANDBOX_DATA_DIR, folder))

def clear_sandbox_code_dir():
    # empty the sandbox code dir recursively    
    for folder in os.listdir(SANDBOX_CODE_DIR):
        shutil.rmtree(os.path.join(SANDBOX_CODE_DIR, folder))

def clear_task_dir():
    """
    Clear the task directory but preserve archive folder.
    """
    if os.path.exists(SANDBOX_TASK_DIR):
        # Get all items in the task directory
        for item in os.listdir(SANDBOX_TASK_DIR):
            item_path = os.path.join(SANDBOX_TASK_DIR, item)
            # Skip archive folder, remove everything else
            if os.path.isdir(item_path) and item != "archive":
                shutil.rmtree(item_path)
            elif os.path.isfile(item_path):
                os.remove(item_path)

def delete_task_tracing_file():
    task_tracing_file_path = os.path.join(SANDBOX_TASK_DIR, "task_tracing.json")
    """
    Delete the task tracing file.
    """
    if os.path.exists(task_tracing_file_path):
        os.remove(task_tracing_file_path)

def summarize_refined_task_files(meta_task_name: str):
    """
    Summarize the refined task files into a single json file. 
    """
    # load selected task index
    selected_task_indices_list = load_selected_task_index(meta_task_name)
    # load all task files from the selected task index
    all_tasks_list = load_all_task_files_from_indices(selected_task_indices_list, meta_task_name)
    # load task summary contents
    for task_index, task_group in zip(selected_task_indices_list, all_tasks_list):
        # generate a combined json object for the task group
        combined_task_group = {
            "task_id": task_index,
            "task_group": task_group
        }
        # save the combined task group to the task_files folder
        with open(os.path.join(SANDBOX_TASK_DIR, meta_task_name, str(task_index), "refined_task_group.json"), "w") as f:
            json.dump(combined_task_group, f, indent=4)

 
def build_overall_reports(meta_task_name: str):
    """Build overall task reports from all markdown files in the report directory."""
    overall_task_report = ""
    meta_task_path = os.path.join(SANDBOX_TASK_DIR, meta_task_name)
    
    # Iterate through all subdirectories (iteration numbers)
    for item in os.listdir(meta_task_path):
        item_path = os.path.join(meta_task_path, item)
        # Check if it's a directory and the name is a number (iteration)
        if os.path.isdir(item_path) and item.isdigit():
            iteration_num = item
            task_summary_path = os.path.join(item_path, "task_summary.md")
            
            # Check if task_summary.md exists in this iteration directory
            if os.path.exists(task_summary_path):
                with open(task_summary_path, "r") as f:
                    content = f.read()
                    overall_task_report += f"\n============ Iteration {iteration_num} =============\n"
                    overall_task_report += content
    
    return overall_task_report


def load_selected_task_index(meta_task_name: str):
    """Load the selected task index from the log file."""
    with open(os.path.join(SANDBOX_TASK_DIR, meta_task_name, "selected_task_index.json"), "r") as f:
        return json.load(f)

 

def load_task_files(selected_task_index: int, meta_task_name: str):
    """Load task files, preferring refined versions over original ones."""
    # Form the dir name with the index number 
    task_files_path = f"{SANDBOX_TASK_DIR}/{meta_task_name}/{str(selected_task_index)}"
     
    # Check if directory exists
    if not os.path.exists(task_files_path):
        return []
    
    # Load all the task files in the task dir
    task_files = os.listdir(task_files_path)
    loaded_tasks = []
    
    # Load the task files, preferring refined versions
    for task_file in task_files:
        if task_file.endswith('.json') and task_file != "refined_task_group.json":
            # Check if there's a refined version of this file
            base_name = task_file.replace('.json', '')
            refined_file = f"{base_name}_refined.json"
            refined_path = os.path.join(task_files_path, refined_file)
            
            # Use refined version if it exists, otherwise use original
            if os.path.exists(refined_path):
                file_to_load = refined_file
            else:
                file_to_load = task_file
            
            with open(os.path.join(task_files_path, file_to_load), "r") as f:
                task_content = json.load(f)
                # add the file name to the task content
                task_content["file_name"] = file_to_load
                loaded_tasks.append(task_content)    
    return loaded_tasks

def get_report_directory(engine_name: str, timestamp: str):
    """Get the report directory path for a given engine and timestamp."""
    return os.path.join(DATA_LOG_DIR, f"task_decomposition_coarse_{engine_name}_{timestamp}")


def load_all_task_files_from_indices(selected_indices: list, meta_task_name: str):
    """Load all task files from multiple selected indices."""
    all_tasks = []
    for index in selected_indices:
        tasks = load_task_files(index, meta_task_name)
        all_tasks.append(tasks)
    return all_tasks


if __name__ == "__main__":
    clear_sandbox_code_dir()
    clear_sandbox_data_dir()
    clear_task_dir()
    remove_db_files()