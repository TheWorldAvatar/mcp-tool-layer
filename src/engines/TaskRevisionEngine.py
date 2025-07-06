from models.locations import DATA_LOG_DIR, SANDBOX_TASK_DIR
import os
import json
from src.agents.TaskEvaluationAgent import task_group_selection_agent, workflow_examination_agent, single_task_refinemment_agent
from src.engines.utils.task_files import summarize_refined_task_files, load_selected_task_index, load_task_files, load_all_task_files_from_indices, build_overall_reports
import asyncio
import pprint   
from src.engines.utils.task_tree import TaskTree


async def workflow_examination_engine(meta_task_name: str, meta_instruction: str):
    # put all the refined task files into a single json file, which is used for the workflow examination agent
    # load the resource file

    summarize_refined_task_files(meta_task_name)    # this function is used to summarize the refined task files into a single json file
    with open(os.path.join(SANDBOX_TASK_DIR, meta_task_name, "resources.json"), "r") as f:
        resources = f.read()
    selected_task_indices_list = load_selected_task_index(meta_task_name)
    for iteration_index in selected_task_indices_list:
        # load the summarized task group
        with open(os.path.join(SANDBOX_TASK_DIR, meta_task_name, str(iteration_index), "refined_task_group.json"), "r") as f:
            summarized_task_group = f.read()
        response = await workflow_examination_agent(task_goal=meta_instruction, 
        meta_task_name=meta_task_name, 
        iteration_index=iteration_index, 
        summarized_task_group=summarized_task_group, 
        resources=resources)



# async def all_child_node_refinement(parent_node: Optional[TaskNode] = None, current_node: Optional[TaskNode] = None):
#     """
#     Recursively traverse all child nodes starting from the root(s), and apply refinement logic or inspection.

#     This function considers the following: 

#     1. Whether the current node has sufficient information to be executed prpp
#     """
#     if parent_node is None:
#         # Top-level: load all roots and begin traversal
#         selected_task_index = load_selected_task_index()
#         all_tasks = load_all_task_files_from_indices(selected_task_index)
#         for task_index, refined_task_group in zip(selected_task_index, all_tasks):
#             task_tree = TaskTree(refined_task_group)
#             roots = task_tree.get_root_task_nodes()
#             for root in roots:
#                 for child in root.children:
#                     print("--------------------------------")
#                     print(f"Task file name: {root.file_name}")
#                     print(f"Parent node: {root.name}")
#                     print(f"Current node: {child.name}")
#                     print(f"All parent nodes: {root.get_all_parent_nodes()}")
#                     print("--------------------------------")
#                     await all_child_node_refinement(parent_node=root, current_node=child)
#     else:
#         for child in current_node.children:
#             print("--------------------------------")
#             print(f"Task file name: {current_node.file_name}")
#             print(f"Parent node: {current_node.name}")
#             print(f"Current node: {child.name}")
#             print(f"All parent nodes: {current_node.get_all_parent_nodes()}")
#             print("--------------------------------")
#             await all_child_node_refinement(parent_node=current_node, current_node=child)
 

async def single_node_refinement(meta_task_name: str, meta_instruction: str, iteration_number: int):
    TASK_GOAL = """
    {meta_instruction}
    """
    selected_task_index = load_selected_task_index(meta_task_name)
    all_tasks = load_all_task_files_from_indices(selected_task_index, meta_task_name)
    for task_index, task_group in zip(selected_task_index, all_tasks):
        task_tree = TaskTree(task_group)
        # get all nodes from the task tree
        all_nodes = task_tree.get_all_task_nodes()
        for node in all_nodes:
            rest_of_task_objects = "\n".join([json.dumps(task) for task in task_group if task["task_id"] != node.task_id])    
            response = await single_task_refinemment_agent(meta_task_name=meta_task_name, 
            iteration_index=task_index, 
            task_object=node, 
            task_goal=TASK_GOAL, 
            rest_of_task_objects=rest_of_task_objects)


async def evaluate_task_plans(meta_task_name: str, meta_instruction: str):
    task_summary_contents = build_overall_reports(os.path.join(SANDBOX_TASK_DIR, meta_task_name))
    response = await task_group_selection_agent(meta_task_name=meta_task_name, meta_instruction=meta_instruction, candidate_reports=task_summary_contents)
    summarize_refined_task_files(meta_task_name=meta_task_name)
    return response


async def refine_task_plans(meta_task_name: str, meta_instruction: str):
    selected_task_index = load_selected_task_index(meta_task_name)  
    all_tasks = load_all_task_files_from_indices(selected_task_index, meta_task_name)
    for task_index, task_group in zip(selected_task_index, all_tasks):
        task_tree = TaskTree(task_group)
        all_nodes = task_tree.get_all_task_nodes()
        for node in all_nodes:
            # realod the task files
            task_group_reloaded = load_task_files(task_index, meta_task_name)
            rest_of_task_objects = "\n".join([json.dumps(task) for task in task_group_reloaded if task["task_id"] != node.task_id])    
            response = await task_refinemment_agent(meta_task_name=meta_task_name, 
            iteration_index=task_index, task_object=node, task_goal=meta_instruction, rest_of_task_objects=rest_of_task_objects)



if __name__ == "__main__":
    response = asyncio.run(evaluate_task_plans(meta_task_name="jiying", meta_instruction="General task"))
    print(response)




