import os
import asyncio
import json
from src.agents.CodeGenerationAgent import code_generation_agent
from models.locations import SANDBOX_TASK_DIR
from src.engines.utils.task_tree import TaskTree, TaskNode
from typing import List


def identify_hypothetical_tools(task_tree: TaskTree) -> List[TaskNode]:
    all_task_nodes = task_tree.get_all_task_nodes()
    hypothetical_task_nodes = []
    for task_node in all_task_nodes:
        for tool in task_node.tools_required:
            if tool.get('is_hypothetical_tool', False):
                hypothetical_task_nodes.append(task_node)
                break
    return hypothetical_task_nodes

async def code_generation_engine(task_meta_name: str) -> str:
    # load the refined task group files
    task_dir = os.path.join(SANDBOX_TASK_DIR, task_meta_name)
    refined_task_group_files = [f for f in os.listdir(task_dir) if f.endswith("_refined_task_group.json")]
    refined_task_group_files_with_index = [f.split("_")[0] for f in refined_task_group_files]
    refined_task_group_files_with_index = [int(f) for f in refined_task_group_files_with_index]
    refined_task_group_files_with_index.sort()
    refined_task_group_files_with_index = [str(f) for f in refined_task_group_files_with_index]

    # load the resources

    for index, refined_task_group_file in zip(refined_task_group_files_with_index, refined_task_group_files):
        print(f"Processing {index}th refined task group")
        refined_task_group_file_path = os.path.join(task_dir, refined_task_group_file)
        with open(refined_task_group_file_path, "r") as f:
            refined_task_group = json.load(f)
        task_tree = TaskTree(refined_task_group)
        hypothetical_task_nodes = identify_hypothetical_tools(task_tree)
        for hypothetical_task_node in hypothetical_task_nodes:
            # use this task node in json format as the instruction to the code generation agent
            code_generation_result = await code_generation_agent(hypothetical_task_node, task_meta_name, index, resources)
            print(code_generation_result)


if __name__ == "__main__":
    asyncio.run(code_generation_engine("jinfeng"))





