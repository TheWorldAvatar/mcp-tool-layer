"""
Task Evaluation Agent: 


"""

import asyncio
from models.BaseAgent import BaseAgent
from models.ModelConfig import ModelConfig
from typing import List
from src.prompts.EvaluationPrompts import TASK_GROUP_SELECTION_PROMPT, WORKFLOW_EXAMINATION_PROMPT, SINGLE_TASK_REFINEMENT_PROMPT


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

    model_config = ModelConfig()
    mcp_tools = ["stack", "task"]
    agent = BaseAgent(model_name="gpt-4o-mini", model_config=model_config, remote_model=True, mcp_tools=mcp_tools, mcp_set_name="task_refinement_mcp_configs.json")
    response, metadata = await agent.run(WORKFLOW_EXAMINATION_PROMPT.format(task_goal=task_goal, 
    meta_task_name=meta_task_name, iteration_index=iteration_index, summarized_task_group=summarized_task_group), recursion_limit=100)
    print(f"Response from workflow examination agent: {response}")
    return response


async def single_task_refinemment_agent(meta_task_name: str, iteration_index: int, task_object: dict, task_goal: str, rest_of_task_objects: str):
    """
    This agent refines a single task object (a single step within a larger task plan). 

    Args:
        meta_task_name: the name of the meta task
        iteration_index: the iteration index of the task object
        task_object: the task object
        task_goal: the task goal
        rest_of_task_objects: the rest of the task objects

    Returns:
        Write the refined task object in json, with a suffix of _refined.json
    """
    model_config = ModelConfig()
    mcp_tools = ["stack", "task"]
    agent = BaseAgent(model_name="gpt-4o-mini", model_config=model_config, remote_model=True, mcp_tools=mcp_tools)
    response, metadata = await agent.run(SINGLE_TASK_REFINEMENT_PROMPT.format(task_object=task_object, 
    iteration_index=iteration_index, 
    meta_task_name=meta_task_name, 
    task_goal=task_goal, 
    rest_of_task_objects=rest_of_task_objects), recursion_limit=300)
    return response

async def task_group_selection_agent(meta_task_name: str, meta_instruction: str, candidate_reports: str):
    """
    This agent selects the best task group from a list of candidate task groups. 
    Args:
        meta_task_name: the name of the meta task
        meta_instruction: the meta instruction

    Returns:
        selected_task_indices.json in the /sandbox/tasks/{meta_task_name} folder
    """
    model_config = ModelConfig()
    mcp_tools = ["task"]
    agent = BaseAgent(model_name="gpt-4o-mini", model_config=model_config, remote_model=True, mcp_tools=mcp_tools)
    response, metadata = await agent.run(TASK_GROUP_SELECTION_PROMPT.format(task_goal=meta_instruction, meta_task_name=meta_task_name, candidate_reports=candidate_reports), recursion_limit=300)
    return response
     
 

if __name__ == "__main__":
    asyncio.run(task_group_selection_agent(meta_task_name="jiying", meta_instruction="Just output 0 and 1 as a test", candidate_reports=""))
    # asyncio.run(workflow_examination_agent(meta_task_name="jiying", iteration_index=0, summarized_task_group="", task_goal="", resources=""))
    # asyncio.run(single_task_refinemment_agent(meta_task_name="jiying", iteration_index=0, task_object="", task_goal="", rest_of_task_objects=""))






