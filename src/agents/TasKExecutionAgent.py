"""
This agent executes a single RefinedTaskNode and, via execution, can refine the task plan.

- This version is simplified to take one RefinedTaskNode and the resource (as str), no iteration.
"""
import asyncio
from models.ModelConfig import ModelConfig
from models.BaseAgent import BaseAgent
from src.utils.global_logger import get_logger

 
TASK_EXECUTION_PROMPT = """

You are a task execution agent. Your job is to utilize the tools provided to you to execute the task. 

The files and resources available to you are the following: 

{resources}

The task you are executing is the following: 

{task_node}

Focus on the current task. 

Remeber to do the following:

- When you directly output some thing, use create_new_file
- 

 
"""

async def task_execution_agent(
    meta_instruction: str,
    meta_task_name: str,
    task_node,  # expects a RefinedTaskNode instance
    resources: str
    ):
    """
    Execute a single RefinedTaskNode using the agent.
    """
    logger = get_logger("agent", "TaskExecutionAgent")
    logger.info(f"Starting task execution for task: {meta_task_name}")
    
    model_config = ModelConfig()
    mcp_tools = ["stack", "filesystem"]
    agent = BaseAgent(
        model_name="gpt-4o-mini",
        model_config=model_config,
        remote_model=True,
        mcp_tools=mcp_tools,
        mcp_set_name="task_execution_mcp_configs.json"
    )
    logger.info(f"Created BaseAgent with tools: {mcp_tools}")
    
    # Convert task_node to dict if needed
    if hasattr(task_node, "to_dict"):
        task_node_dict = task_node.to_dict()
    else:
        task_node_dict = task_node
    
    logger.info(f"Executing task node: {task_node_dict}")
    response, metadata = await agent.run(
        TASK_EXECUTION_PROMPT.format(
            task_node=task_node_dict,
            resources=resources,
            meta_task_name=meta_task_name,
            meta_instruction=meta_instruction
        ),
        recursion_limit=300
    )
    logger.info("Task execution completed")
    return response
 