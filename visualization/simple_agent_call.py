#!/usr/bin/env python3
"""
Simple DataSniffingAgent Call
Directly calls the DataSniffingAgent without complex threading or subprocess.
"""

import os
import sys
import asyncio
from datetime import datetime

# Add parent directory to path
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, parent_dir)

from src.agents.DataSniffingAgent import data_sniffing_agent
from src.utils.global_logger import get_logger

logger = get_logger("visualization", "simple_agent_call")

def run_data_sniffing_agent_simple(task_name, meta_instruction=""):
    """
    Simple direct call to DataSniffingAgent.
    """
    try:
        logger.info(f"Starting DataSniffingAgent for task: {task_name}")
        
        # Initialize logging in this thread
        from src.utils.global_logger import initialize_logging
        initialize_logging()
        
        # Create new resource DB connection in this thread
        from src.utils.resource_db_operations import ResourceDBOperator
        resource_db_operator = ResourceDBOperator()
        
        # Create thread-safe fuzzy search function
        def thread_safe_fuzzy_search(query: str):
            """Thread-safe version of fuzzy_repo_file_search."""
            from fuzzywuzzy import fuzz
            from models.Resource import Resource
            import os
            
            def is_data_log_or_temp(path):
                norm = path.replace("\\", "/")
                return "data/log" in norm or "data/temp" in norm
            
            best_match = None
            best_similarity = 0
            threshold = 0.8
            resources = resource_db_operator.get_all_resources()
            
            query_no_path_sep = query.replace(os.path.sep, "").replace("/", "")
            
            for resource in resources:
                if is_data_log_or_temp(resource.relative_path) or is_data_log_or_temp(resource.absolute_path):
                    continue
                
                rel_no_path_sep = resource.relative_path.replace(os.path.sep, "").replace("/", "")
                abs_no_path_sep = resource.absolute_path.replace(os.path.sep, "").replace("/", "")
                
                sim_rel = fuzz.ratio(query_no_path_sep, rel_no_path_sep) / 100.0
                sim_abs = fuzz.ratio(query_no_path_sep, abs_no_path_sep) / 100.0
                
                similarity = max(sim_rel, sim_abs)
                
                if similarity > best_similarity:
                    best_similarity = similarity
                    best_match = resource
            
            if best_similarity >= threshold:
                return best_match
            else:
                return None
        
        # Monkey patch ALL the SQLite connections to use our thread-local one
        import src.utils.file_management as file_management
        file_management.fuzzy_repo_file_search = thread_safe_fuzzy_search
        file_management.db_operator = resource_db_operator
        
        # Patch MCP generic operations
        import src.mcp_servers.generic.operations.file_operations as file_ops
        file_ops.resource_db_operator = resource_db_operator
        
        # Patch any other modules that might have SQLite connections
        try:
            import src.mcp_servers.stack.operations.ttl_validation_operations as ttl_ops
            ttl_ops.resource_db_operator = resource_db_operator
        except ImportError:
            pass
        
        try:
            import src.mcp_servers.stack.operations.obda_creation_operations as obda_ops
            obda_ops.resource_db_operator = resource_db_operator
        except ImportError:
            pass
        
        try:
            import src.mcp_servers.task.operations.task_generation_coarse_operations as task_ops
            task_ops.db_operator = resource_db_operator
        except ImportError:
            pass
        
        try:
            import src.mcp_servers.refine_and_examination.operations.task_plan_selection_operations as plan_ops
            plan_ops.db_operator = resource_db_operator
        except ImportError:
            pass
        
        # Ensure the task directory exists
        from models.locations import SANDBOX_TASK_DIR
        task_dir = os.path.join(SANDBOX_TASK_DIR, task_name)
        os.makedirs(task_dir, exist_ok=True)
        
        # Run the agent directly
        folder_path = f"data/generic_data/{task_name}"
        result = asyncio.run(data_sniffing_agent(folder_path=folder_path, task_meta_name=task_name))
        
        logger.info(f"DataSniffingAgent completed for task: {task_name}")
        return True
        
    except Exception as e:
        logger.error(f"Error running DataSniffingAgent for task {task_name}: {e}")
        return False

def run_data_sniffing_agent_with_feedback(task_name, user_feedback, meta_instruction=""):
    """
    Run DataSniffingAgent with user feedback incorporated into the prompt.
    """
    try:
        logger.info(f"Starting DataSniffingAgent rerun for task: {task_name} with feedback")
        
        # Initialize logging in this thread
        from src.utils.global_logger import initialize_logging
        initialize_logging()
        
        # Create new resource DB connection in this thread
        from src.utils.resource_db_operations import ResourceDBOperator
        resource_db_operator = ResourceDBOperator()
        
        # Create thread-safe fuzzy search function
        def thread_safe_fuzzy_search(query: str):
            """Thread-safe version of fuzzy_repo_file_search."""
            from fuzzywuzzy import fuzz
            from models.Resource import Resource
            import os
            
            def is_data_log_or_temp(path):
                norm = path.replace("\\", "/")
                return "data/log" in norm or "data/temp" in norm
            
            best_match = None
            best_similarity = 0
            threshold = 0.8
            resources = resource_db_operator.get_all_resources()
            
            query_no_path_sep = query.replace(os.path.sep, "").replace("/", "")
            
            for resource in resources:
                if is_data_log_or_temp(resource.relative_path) or is_data_log_or_temp(resource.absolute_path):
                    continue
                
                rel_no_path_sep = resource.relative_path.replace(os.path.sep, "").replace("/", "")
                abs_no_path_sep = resource.absolute_path.replace(os.path.sep, "").replace("/", "")
                
                sim_rel = fuzz.ratio(query_no_path_sep, rel_no_path_sep) / 100.0
                sim_abs = fuzz.ratio(query_no_path_sep, abs_no_path_sep) / 100.0
                
                similarity = max(sim_rel, sim_abs)
                
                if similarity > best_similarity:
                    best_similarity = similarity
                    best_match = resource
            
            if best_similarity >= threshold:
                return best_match
            else:
                return None
        
        # Monkey patch ALL the SQLite connections to use our thread-local one
        import src.utils.file_management as file_management
        file_management.fuzzy_repo_file_search = thread_safe_fuzzy_search
        file_management.db_operator = resource_db_operator
        
        # Patch MCP generic operations
        import src.mcp_servers.generic.operations.file_operations as file_ops
        file_ops.resource_db_operator = resource_db_operator
        
        # Patch any other modules that might have SQLite connections
        try:
            import src.mcp_servers.stack.operations.ttl_validation_operations as ttl_ops
            ttl_ops.resource_db_operator = resource_db_operator
        except ImportError:
            pass
        
        try:
            import src.mcp_servers.stack.operations.obda_creation_operations as obda_ops
            obda_ops.resource_db_operator = resource_db_operator
        except ImportError:
            pass
        
        try:
            import src.mcp_servers.task.operations.task_generation_coarse_operations as task_ops
            task_ops.db_operator = resource_db_operator
        except ImportError:
            pass
        
        try:
            import src.mcp_servers.refine_and_examination.operations.task_plan_selection_operations as plan_ops
            plan_ops.db_operator = resource_db_operator
        except ImportError:
            pass
        
        # Ensure the task directory exists
        from models.locations import SANDBOX_TASK_DIR
        task_dir = os.path.join(SANDBOX_TASK_DIR, task_name)
        os.makedirs(task_dir, exist_ok=True)
        
        # Run the agent with user feedback
        folder_path = f"data/generic_data/{task_name}"
        result = asyncio.run(data_sniffing_agent(folder_path=folder_path, task_meta_name=task_name, user_feedback=user_feedback))
        
        logger.info(f"DataSniffingAgent rerun completed for task: {task_name} with feedback: {user_feedback}")
        return True
        
    except Exception as e:
        logger.error(f"Error running DataSniffingAgent rerun for task {task_name}: {e}")
        return False

def check_data_sniffing_report(task_name):
    """Check if a data sniffing report exists for the given task."""
    from models.locations import SANDBOX_TASK_DIR
    
    report_path = os.path.join(SANDBOX_TASK_DIR, task_name, "data_sniffing_report.md")
    if os.path.exists(report_path):
        try:
            with open(report_path, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            logger.error(f"Error reading data sniffing report: {e}")
            return None
    return None

if __name__ == "__main__":
    # Test the simple implementation
    print("Testing simple DataSniffingAgent call...")
    
    success = run_data_sniffing_agent_simple("gaussian", "Test instruction")
    print(f"Simple agent call: {'Success' if success else 'Failed'}")
    
    # Check for report
    report = check_data_sniffing_report("gaussian")
    if report:
        print("Report generated successfully!")
    else:
        print("No report found.")
    
    print("Simple implementation test completed!") 