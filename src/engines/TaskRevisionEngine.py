from models.locations import DATA_LOG_DIR, SANDBOX_TASK_DIR
import os
import json
from src.agents.TaskEvaluationAgent import task_group_selection_agent, workflow_examination_agent
from src.engines.utils.task_files import summarize_refined_task_files, load_selected_task_index, build_overall_reports
import asyncio
from src.utils.resource_db_operations import ResourceDBOperator

async def workflow_examination_engine(meta_task_name: str, meta_instruction: str):
  
    resource_db_operator = ResourceDBOperator()
    resources = resource_db_operator.get_resources_by_meta_task_name(meta_task_name)
    # join the resources into a single string   
    resources_string = "\n".join([str(resource) for resource in resources])


    selected_task_indices_list = load_selected_task_index(meta_task_name)
    for iteration_index in selected_task_indices_list:
        # load the summarized task group
        with open(os.path.join(SANDBOX_TASK_DIR, meta_task_name, str(iteration_index), "refined_task_group.json"), "r") as f:
            summarized_task_group = f.read()
        response = await workflow_examination_agent(task_goal=meta_instruction, 
        meta_task_name=meta_task_name, 
        iteration_index=iteration_index, 
        summarized_task_group=summarized_task_group, 
        resources=resources_string)


async def evaluate_task_plans(meta_task_name: str, meta_instruction: str):
    task_summary_contents = build_overall_reports(os.path.join(SANDBOX_TASK_DIR, meta_task_name))
    response = await task_group_selection_agent(meta_task_name=meta_task_name, meta_instruction=meta_instruction, candidate_reports=task_summary_contents)
    summarize_refined_task_files(meta_task_name=meta_task_name)
    return response



if __name__ == "__main__":
    response = asyncio.run(workflow_examination_engine(meta_task_name="jiying", meta_instruction="General task"))
    print(response)




