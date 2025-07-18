"""
Data Sniffing Agent:

- This agent analyzes data in a folder and generates a comprehensive report about the data structure and content.
- It examines files and documents to understand the data format, purpose, and structure.
- It generates both a markdown report and a structured resources.json file for further processing.
"""

import os
from models.BaseAgent import BaseAgent
from models.ModelConfig import ModelConfig
from models.locations import SANDBOX_TASK_DIR
from src.prompts.DecompositionPrompts import INSTRUCTION_DATA_SNIFFING_PROMPT
from src.utils.file_management import fuzzy_repo_file_search
from src.utils.global_logger import get_logger


async def data_sniffing_agent(folder_path: str, task_meta_name: str):
    """
    This agent sniff the data in the folder and generate a data sniffing report, summarizing the inital data provided. 

    Args:
        folder_path: the path to the data folder
        task_meta_name: the name of the task

    Returns:
        Write the data sniffing report in markdown format to the /sandbox/tasks/{task_meta_name}/data_sniffing_report.md
        Write a resources.json file in the /sandbox/tasks/{task_meta_name}/resources.json, which is a structured output. 
    """
    logger = get_logger("agent", "DataSniffingAgent")
    logger.info(f"Starting data sniffing for folder: {folder_path}, task: {task_meta_name}")

    # fuzzy search the folder_path in the resource db
    resource = fuzzy_repo_file_search(folder_path)
    if resource is None:
        error_msg = f"Folder {folder_path} does not exist in the resource db."
        logger.error(error_msg)
        return error_msg

    folder_uri = resource.uri
    logger.info(f"Found resource with URI: {folder_uri}")

    model_config = ModelConfig()    
    mcp_set_name = "pretask_mcp_configs.json"
    mcp_tools = ["generic"]
    instruction = INSTRUCTION_DATA_SNIFFING_PROMPT.format(folder_uri=folder_uri, task_meta_name=task_meta_name)
    agent = BaseAgent(model_name="gpt-4o-mini", remote_model=True, mcp_set_name=mcp_set_name, mcp_tools=mcp_tools, model_config=model_config)
    logger.info(f"Created BaseAgent with tools: {mcp_tools}")
    
    response, metadata = await agent.run(instruction, recursion_limit=30)
    logger.info("Data sniffing completed")
    return response


if __name__ == "__main__":
    from src.utils.global_logger import initialize_logging
    initialize_logging()
    
    logger = get_logger("agent", "DataSniffingAgent")
    logger.info("Starting DataSniffingAgent main")
    
    from src.engines.utils.task_files import clear_task_dir
    clear_task_dir()
    import asyncio
    result = asyncio.run(data_sniffing_agent(folder_path="/data/generic_data/jiying", task_meta_name="jiying"))
    logger.info(f"DataSniffingAgent main completed with result: {result}") 
 