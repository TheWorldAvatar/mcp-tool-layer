"""
This agent one-by-one execute the tasks and via execution, refine the task plan. 

- Lets just simply do that and see how it goes. 
"""
from src.engines.utils.task_tree import TaskNode, TaskTree
import asyncio
import os
import json
from models.locations import SANDBOX_TASK_DIR
from models.ModelConfig import ModelConfig
from models.BaseAgent import BaseAgent
import logging

# Suppress FastMCP logging below WARNING level
logging.getLogger("FastMCP").setLevel(logging.ERROR)

TASK_EXECUTION_PROMPT = """
Your task is to execute the following task, which is part of the overall task. 

{task_node}

This is the overal goal of the overall task. 

{meta_instruction}

the meta task name is {meta_task_name}, which is useful for you to update the resource registration. 

These are the resources that are available to you so far other than the MCP tools, prioritize using the resources that are already available to you. 

{resources}

Remember, if something is not included in the resources, it does not it exist. All file and scritps are registered in the resources. 

When the task includes a hypothetical tool, it should already be included in the resources, where the docker based execution command is provided. 

If the a tool suggest that is_llm_generation is True, you are to directly generate the output your self. 

You are running in a WSL environment, so you need to use the path conversion tool to convert local windows paths to a WSL path. 

Make sure you update the resource registration if your execution creates any new files. 

These is the full task tree, make sure you don't repeat any task. 

{full_task_tree}    

You are not allowed to create functions yourself, report in your final response if you need extra functions to fulfill the task. 

In your final response, you should return the following: 

- The status of the task execution. 
- If the task failed, report the specific function inputs you used, especially if the error is about can not find the scripts or files. 
- What extra steps you **need to take** to fulfill the task. 
- What files you created. 
- What files you used. 
"""




async def main(meta_instruction: str, meta_task_name: str, iteration_index: int, task_node: str, resources: str, full_task_tree: str):

    model_config = ModelConfig()
    mcp_tools = ["stack", "sandbox", "generic"]
    agent = BaseAgent(model_name="gpt-4o-mini", model_config=model_config, remote_model=True, mcp_tools=mcp_tools, mcp_set_name="tas_execution_mcp_configs.json")
    response, metadata = await agent.run(TASK_EXECUTION_PROMPT.format(task_node=task_node, resources=resources, full_task_tree=full_task_tree, meta_task_name=meta_task_name, meta_instruction=meta_instruction), recursion_limit=300)
    print(f"Response from task execution agent: {response}")
    return response


async def iterate_task_nodes(meta_task_name: str, meta_instruction: str):

    # load selected_task_index.json
    selected_task_index_path = os.path.join(SANDBOX_TASK_DIR, meta_task_name, "selected_task_index.json")
    with open(selected_task_index_path, "r") as f:
        selected_task_index = json.load(f)
    for iteration_index in selected_task_index:
        full_response_collection_path = os.path.join(SANDBOX_TASK_DIR, meta_task_name, f"full_response_collection_{iteration_index}.md")
        with open(full_response_collection_path, "w") as f:
            f.write(f"# Full Response Collection {iteration_index}\n")

        # resource.json path 
        # <iteration_index>_refined_task_group.json path 

        resource_json_path = os.path.join(SANDBOX_TASK_DIR, meta_task_name, "resources.json")
        refined_task_group_json_path = os.path.join(SANDBOX_TASK_DIR, meta_task_name, f"{iteration_index}_refined_task_group.json")

        resource_json = json.load(open(resource_json_path))
        refined_task_group = json.load(open(refined_task_group_json_path))

        task_tree = TaskTree(refined_task_group)
        task_tree.build_task_tree()

        task_nodes = task_tree.get_dependency_ordered_task_nodes()
        for task_node in task_nodes:
            print(task_node.task_id)
            print(task_node.name)
            print(task_node.tools_required)
            print(task_node.dependencies)
            print(task_node.file_name)
            response = await main(meta_instruction=meta_instruction, meta_task_name=meta_task_name, iteration_index=iteration_index, task_node=task_node.to_dict(), resources=json.dumps(resource_json), full_task_tree=json.dumps(task_tree.to_dict()))
            print(response)

            sub_section_title = f"\n --\n## Task {task_node.task_id} - {task_node.name}\n\n"
            with open(full_response_collection_path, "a") as f:
                f.write(sub_section_title)
                f.write(response)


if __name__ == "__main__":

    META_INSTRUCTION = """
    
    """
    asyncio.run(iterate_task_nodes("jinfeng", META_INSTRUCTION))


