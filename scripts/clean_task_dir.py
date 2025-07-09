import os
import shutil
from datetime import datetime
from models.locations import SANDBOX_TASK_DIR, SANDBOX_TASK_ARCHIVE_DIR


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