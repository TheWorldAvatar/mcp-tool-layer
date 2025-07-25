"""
File operations module for handling file reading operations.
"""

import os
import json
import sys
from typing import List, Dict, Any, Optional

# Add parent directory to path for imports
parent_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, parent_dir)

from models.locations import DATA_LOG_DIR, SANDBOX_TASK_DIR
from src.utils.resource_db_operations import ResourceDBOperator
from src.utils.global_logger import get_logger

logger = get_logger("visualization", "file_operations")


class FileOperationsManager:
    """Manages file operations for the visualization server."""
    
    def __init__(self):
        self.resource_db_operator = ResourceDBOperator()
    
    def get_logs(self) -> str:
        """Read and return the agent logs."""
        try:
            log_file = os.path.join(DATA_LOG_DIR, "agent.log")
            if os.path.exists(log_file):
                with open(log_file, 'r', encoding='utf-8') as f:
                    logs = f.read()
                return logs
            else:
                return 'No logs available'
        except Exception as e:
            logger.error(f"Error reading logs: {e}")
            return f'Error reading logs: {e}'
    
    def get_data_sniffing_report(self, task_name: str) -> Optional[str]:
        """Read and return the data sniffing report for a given task."""
        try:
            report_path = os.path.join(SANDBOX_TASK_DIR, task_name, "data_sniffing_report.md")
            if os.path.exists(report_path):
                with open(report_path, 'r', encoding='utf-8') as f:
                    report_content = f.read()
                return report_content
            return None
        except Exception as e:
            logger.error(f"Error reading data sniffing report for task {task_name}: {e}")
            return None
    
    def check_report_exists(self, task_name: str) -> bool:
        """Check if data sniffing report exists for a given task."""
        report_path = os.path.join(SANDBOX_TASK_DIR, task_name, "data_sniffing_report.md")
        return os.path.exists(report_path)
    
    def get_resources(self) -> List[Dict[str, Any]]:
        """Get all resources from the database."""
        try:
            resources = self.resource_db_operator.get_all_resources()
            resources = [json.loads(str(resource)) for resource in resources]
            return resources
        except Exception as e:
            logger.error(f"Error getting resources: {e}")
            return []
    
    def clear_logs(self) -> None:
        """Clear the agent log file."""
        try:
            log_file = os.path.join(DATA_LOG_DIR, "agent.log")
            with open(log_file, "w") as f:
                f.write("")
            logger.info("Cleared agent log file")
        except Exception as e:
            logger.error(f"Error clearing logs: {e}")
    
    def reset_resource_database(self) -> None:
        """Reset the resource database."""
        try:
            self.resource_db_operator.reset_db()
            logger.info("Reset resource database")
        except Exception as e:
            logger.error(f"Error resetting resource database: {e}")
    
    def register_resources_bulk(self, resources: List[Any]) -> None:
        """Register multiple resources in the database."""
        try:
            self.resource_db_operator.register_resources_bulk(resources)
            logger.info(f"Registered {len(resources)} resources")
        except Exception as e:
            logger.error(f"Error registering resources: {e}")


# Global file operations manager instance
file_ops_manager = FileOperationsManager() 