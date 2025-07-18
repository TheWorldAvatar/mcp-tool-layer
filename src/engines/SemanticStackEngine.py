"""
The semantic stack engine is responsible for the following tasks: 

1. Given **ANY** data from any domain, the semantic stack engine aims to create/coordinate tools to parse the data 
2. Generate the semantic description of the data 
3. Working out explicit workflow to parse and integrate the data into the semantic stack
4. Prepare the semantic data. 
"""

from models.locations import SANDBOX_TASK_DIR
from src.agents.TaskDecompositionAgent import task_decomposition_agent
from src.agents.DataSniffingAgent import data_sniffing_agent
from src.agents.TaskEvaluationAgent import task_group_selection_agent
from src.engines.TaskRevisionEngine import evaluate_task_plans
from src.engines.TaskRevisionEngine import summarize_refined_task_files
from src.engines.TaskRevisionEngine import workflow_examination_engine
from src.engines.CodeGenerationEngine import code_generation_engine
from src.engines.utils.task_files import delete_task_tracing_file, remove_db_files, clear_sandbox_data_dir, clear_sandbox_code_dir
from src.engines.utils.task_files import clear_task_dir
from src.utils.file_management import read_file_content_from_uri
from scripts.identify_hypothetical_tools import identify_hypothetical_tools
from src.utils.resource_db_operations import ResourceDBOperator
from src.utils.file_management import scan_base_folders_recursively
from src.utils.global_logger import get_logger
import asyncio
import json
import os
import time

META_INSTRUCTION = """
You are provided a folder loaded with some data files.

I want you to integrate the data from the data folder into my system stack. Make sure the data is integrated into the stack. 
"""
 
 

async def semantic_stack_engine():
    """
    This engine is responsible for the overall task workflow for integrating arbitrary data into the semantic stack. 

    Args:
        None

    Returns:
        None
    """
    from src.utils.global_logger import initialize_logging
    initialize_logging()
    
    logger = get_logger("engine", "SemanticStackEngine")
    logger.info("Starting SemanticStackEngine")

    # clear the task directory
    clear_task_dir()
    clear_sandbox_data_dir()
    clear_sandbox_code_dir()
    remove_db_files()
    
    logger.info("Cleared task directory")

    # reset the resource db
    resource_db_operator = ResourceDBOperator()
    resource_db_operator.reset_db()
    logger.info("Reset resource database")

    resources = scan_base_folders_recursively()
    resource_db_operator.register_resources_bulk(resources)
    logger.info(f"Registered {len(resources)} resources")

    name_list = ["jiying"]
    # name_list = ["patrick"]
    
    # delete_task_tracing_file() # remove the task tracing file, which is used to trace the overall task workflow
    for name in name_list:
        logger.info(f"Processing {name}")
        # run the data sniffing agent until the data sniffing report is generated
        logger.info("1. Data sniffing")
        while not os.path.exists(os.path.join(SANDBOX_TASK_DIR, name, "data_sniffing_report.md")):
            data_sniffing_result = await data_sniffing_agent(folder_path=f"data/generic_data/{name}", task_meta_name=name)
            logger.info(f"Data sniffing result: {data_sniffing_result}")
            time.sleep(1)
        
        logger.info("2. Task decomposition")
        task_decomposition_result = await task_decomposition_agent(task_meta_name=name, meta_instruction=META_INSTRUCTION, iteration_number=3)
        logger.info(f"Task decomposition result: {task_decomposition_result}")
        
        # run the task evaluation agent until selected_task_index.json is created in the folder
        logger.info("3. Task evaluation")
        while not os.path.exists(os.path.join(SANDBOX_TASK_DIR, name, "selected_task_index.json")):
            task_evaluation_result = await evaluate_task_plans(meta_task_name=name, meta_instruction=META_INSTRUCTION)
            logger.info(f"Task evaluation result: {task_evaluation_result}")
            time.sleep(1)
        
        # run the workflow examination agent until two refined task group files are created
        logger.info("4. Workflow examination")
        refined_files_count = 0

        # load selected_task_index.json
        selected_task_index_path = f"file://{os.path.join(SANDBOX_TASK_DIR, name, 'selected_task_index.json')}"
        selected_task_index = json.loads(read_file_content_from_uri(selected_task_index_path))

        while refined_files_count < len(selected_task_index):
            try:
                workflow_examination_result = await workflow_examination_engine(meta_task_name=name, meta_instruction=META_INSTRUCTION)
                logger.info(f"Workflow examination result: {workflow_examination_result}")
            except Exception as e:
                logger.error(f"Error in workflow examination: {e}")
                time.sleep(1)
                continue
            # count the number of files with "_refined_task_group.json" suffix
            task_dir = os.path.join(SANDBOX_TASK_DIR, name)
            if os.path.exists(task_dir):
                refined_files_count = len([f for f in os.listdir(task_dir) if f.endswith("_refined_task_group.json")])
            time.sleep(1)
        
        logger.info("5. Identifying hypothetical tools")
        identify_hypothetical_tools(name)
 

if __name__ == "__main__":
    asyncio.run(semantic_stack_engine())

