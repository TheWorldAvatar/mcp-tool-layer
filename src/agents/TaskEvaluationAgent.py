"""
Task Evaluation Agent: 


"""

import asyncio
from models.BaseAgent import BaseAgent
from models.ModelConfig import ModelConfig
from typing import List
from src.prompts.EvaluationPrompts import TASK_GROUP_SELECTION_PROMPT, WORKFLOW_EXAMINATION_PROMPT, SINGLE_TASK_REFINEMENT_PROMPT
from src.utils.file_management import read_file_content_from_uri
from models.locations import SANDBOX_TASK_DIR
from src.utils.global_logger import get_logger
import os

async def workflow_examination_agent(meta_task_name: str, iteration_index: int, summarized_task_group: dict, task_goal: str, resources: str):
    """
    This agent examines the overall workflow of a task group, and add missing steps if necessary. 

    Args:
        meta_task_name: the name of the meta task
        iteration_index: the iteration index of the task group
        summarized_task_group: the summarized task group
        task_goal: the task goal
        resources: the resources available to the agent

    Returns:
        The reassembled task group in markdown format. TODO: This should be a structured output 
    """
    logger = get_logger("agent", "TaskEvaluationAgent")
    logger.info(f"Starting workflow examination for task: {meta_task_name}, iteration: {iteration_index}")

    model_config = ModelConfig()
    mcp_tools = ["stack", "task_refinement"]
    agent = BaseAgent(model_name="gpt-4o-mini", model_config=model_config, remote_model=True, mcp_tools=mcp_tools, mcp_set_name="task_refinement_mcp_configs.json")
    logger.info(f"Created BaseAgent with tools: {mcp_tools}")
    
    response, metadata = await agent.run(WORKFLOW_EXAMINATION_PROMPT.format(task_goal=task_goal, 
    meta_task_name=meta_task_name, iteration_index=iteration_index, summarized_task_group=summarized_task_group), recursion_limit=100)
    logger.info("Workflow examination completed")
    return response
 



async def task_group_selection_agent(meta_task_name: str, meta_instruction: str, candidate_reports: str):
    """
    This agent selects the best task group from a list of candidate task groups. 
    Args:
        meta_task_name: the name of the meta task
        meta_instruction: the meta instruction
        candidate_reports: the candidate reports    

    Returns:
        selected_task_indices.json in the /sandbox/tasks/{meta_task_name} folder
    """
    logger = get_logger("agent", "TaskEvaluationAgent")
    logger.info(f"Starting task group selection for task: {meta_task_name}")
    
    model_config = ModelConfig()
    mcp_tools = ["task_refinement"]
    agent = BaseAgent(model_name="gpt-4o-mini", model_config=model_config, remote_model=True, mcp_tools=mcp_tools, mcp_set_name="task_refinement_mcp_configs.json")
    logger.info(f"Created BaseAgent with tools: {mcp_tools}")
    
    response, metadata = await agent.run(TASK_GROUP_SELECTION_PROMPT.format(task_goal=meta_instruction, 
    meta_task_name=meta_task_name, candidate_reports=candidate_reports))
    logger.info("Task group selection completed")
    return response
     
 

if __name__ == "__main__":
    from src.utils.global_logger import initialize_logging
    initialize_logging()
    
    logger = get_logger("agent", "TaskEvaluationAgent")
    logger.info("Starting TaskEvaluationAgent main")

    meta_task_name = "jiying"

    # read the candidate reports from the /sandbox/tasks/{meta_task_name}/{iteration}/task_summary.md
    candidate_reports = []
    iteration_number = 0
    task_summary_path = f"file://{os.path.join(SANDBOX_TASK_DIR, meta_task_name, str(iteration_number), 'task_summary.md')}"
    logger.info(f"Reading task summary from {os.path.join(SANDBOX_TASK_DIR, meta_task_name, str(iteration_number), 'task_summary.md')}")
    candidate_reports.append(read_file_content_from_uri(task_summary_path))
    candidate_reports_str = "\n".join(candidate_reports)
    response = asyncio.run(task_group_selection_agent(meta_task_name=meta_task_name, meta_instruction="", candidate_reports=candidate_reports_str))
    logger.info("TaskEvaluationAgent main completed")
 





