"""
Initialization module for handling startup setup and initialization.
"""

import os
import sys

# Add parent directory to path for imports
parent_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, parent_dir)

from models.locations import DATA_LOG_DIR
from src.utils.global_logger import initialize_logging, get_logger
from src.utils.file_management import scan_base_folders_recursively
from src.engines.utils.task_files import clear_task_dir
from .file_operations import file_ops_manager


def initialize_application():
    """Initialize the visualization application."""
    # Initialize logging
    initialize_logging()
    logger = get_logger("visualization", "app")
    logger.info("Starting Visualization Server")
    
    # Clear log files and task directory
    file_ops_manager.clear_logs()
    clear_task_dir()
    logger.info("Cleared task directory and log files")
    
    # Reset resource database
    file_ops_manager.reset_resource_database()
    logger.info("Reset resource database")
    
    # Scan and register resources
    resources = scan_base_folders_recursively()
    logger.info(f"Initial Resources: {len(resources)}")
    for resource in resources:
        logger.info(f"Resource relative path: {resource.relative_path}")
    
    file_ops_manager.register_resources_bulk(resources)
    logger.info(f"Registered {len(resources)} resources")
    
    return logger 