import os
import shutil
from datetime import datetime
from models.locations import SANDBOX_TASK_DIR, SANDBOX_TASK_ARCHIVE_DIR, SANDBOX_CODE_DIR   


def clean_task_dir() -> str:
    """
    Always call this function **once** **before** you start to create new tasks. This is very important. 
    """
    # Get all directories in SANDBOX_TASK_DIR
    if not os.path.exists(SANDBOX_TASK_DIR):
        raise FileNotFoundError(f"Task directory {SANDBOX_TASK_DIR} not found")
    
    # Create timestamp for archive folder
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # Move all directories and files from SANDBOX_TASK_DIR to archive
    for item in os.listdir(SANDBOX_TASK_DIR):
        if item != "archive":  # Exclude archive folder itself
            source_path = os.path.join(SANDBOX_TASK_DIR, item)
            
            # Append timestamp to folder name and move to archive
            item_with_timestamp = f"{item}_{timestamp}"
            dest_path = os.path.join(SANDBOX_TASK_ARCHIVE_DIR, item_with_timestamp)
            
            shutil.move(source_path, dest_path)
    
    return f"All tasks archived to {SANDBOX_TASK_ARCHIVE_DIR}"


def clean_code_dir() -> str:
    """
    Always call this function **once** **before** you start to create new tasks. This is very important. 
    """
    if not os.path.exists(SANDBOX_CODE_DIR):
        raise FileNotFoundError(f"Code directory {SANDBOX_CODE_DIR} not found")
    
    # Create timestamp for archive folder
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

def get_refined_task_group_files(task_meta_name: str) -> tuple[list, list]:
    task_dir = os.path.join(SANDBOX_TASK_DIR, task_meta_name)
    refined_task_group_files = [f for f in os.listdir(task_dir) if f.endswith("_refined_task_group.json")]
    refined_task_group_files_with_index = [f.split("_")[0] for f in refined_task_group_files]
    refined_task_group_files_with_index = [int(f) for f in refined_task_group_files_with_index]
    refined_task_group_files_with_index.sort()
    refined_task_group_files_with_index = [str(f) for f in refined_task_group_files_with_index]
    return refined_task_group_files_with_index, refined_task_group_files