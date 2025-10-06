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

- meta_task_name: {meta_task_name}
- iteration_index: {iteration_index}


The task you are executing is the following: 

{task_node}

Focus on the current task. 

Remeber to do the following:

- When you directly output some thing, use create_new_file

- When you create the obda file, make sure you read the ttl file and the data schema file (usually json file) to understand the underlying ontology structure and the data represented. 
full_file_access is the suitable tool to read the files. Also, very importantly, you need to mapping everything in the ontology and the schema in your obda file. (There are multiple files)

Some times the data schema file contains file names fields, that is because the data is parsed into multiple csv files representing different aspects of the data.

The csv files in the resources represent different aspects of the data, you should include all of them in the obda file. Each of them should be mapped to the ontology. The file name will 
give you the information about the data. 

The files and resources available to you are the following: 

{resources} 
"""

TEST_PROMPT = """
Create a random obda file for building, make sure it is very detailed: 

meta_task_name: gaussian
iteration_index: 1

"""

async def task_execution_agent(
    meta_instruction: str,
    meta_task_name: str,
    task_node,  # expects a RefinedTaskNode instance
    resources: str,
    iteration_index: int
    ):
    """
    Execute a single RefinedTaskNode using the agent.
    """
    logger = get_logger("agent", "TaskExecutionAgent")
    logger.info(f"Starting task execution for task: {meta_task_name}")
    
    model_config = ModelConfig()
    mcp_tools = ["stack"]
    # mcp_tools = ["execution_utils"]
    # mcp_tools = ["stack"]   
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
            meta_instruction=meta_instruction,
            iteration_index=iteration_index
        ),
        recursion_limit=300
    )

    # response, metadata = await agent.run(
    #     TEST_PROMPT,
    #     recursion_limit=10
    # )

    logger.info("Task execution completed")
    return response
 

if __name__ == "__main__":
    result = asyncio.run(task_execution_agent(
        meta_instruction="",
        meta_task_name="gaussian",
        task_node="",
        resources="",
        iteration_index=1
    ))
    print(result)