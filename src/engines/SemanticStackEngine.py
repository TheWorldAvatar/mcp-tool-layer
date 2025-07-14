"""
The semantic stack engine is responsible for the following tasks: 

1. Given **ANY** data from any domain, the semantic stack engine aims to create/coordinate tools to parse the data 
2. Generate the semantic description of the data 
3. Working out explicit workflow to parse and integrate the data into the semantic stack
4. Prepare the semantic data. 
"""

from models.locations import SANDBOX_TASK_DIR
from src.agents.TaskDecompositionAgent import task_decomposition_agent
from src.agents.TaskDecompositionAgent import data_sniffing_agent
from src.agents.TaskEvaluationAgent import task_group_selection_agent
from src.engines.TaskRevisionEngine import evaluate_task_plans
from src.engines.TaskRevisionEngine import refine_task_plans
from src.engines.TaskRevisionEngine import summarize_refined_task_files
from src.engines.TaskRevisionEngine import workflow_examination_engine
from src.engines.TaskRevisionEngine import single_node_refinement
from src.engines.CodeGenerationEngine import code_generation_engine
from src.engines.utils.task_files import delete_task_tracing_file
from src.engines.utils.task_files import clear_task_dir
from src.agents.TasKExecutionAgent import iterate_task_nodes
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
    name_list = ["jinfeng"]
    # name_list = ["patrick"]
    # clear_task_dir()
    # delete_task_tracing_file() # remove the task tracing file, which is used to trace the overall task workflow
    for name in name_list:
        # print(f"Processing {name}")
        # print("1. Data sniffing")
        # # run the data sniffing agent until the data sniffing report is generated
        # while not os.path.exists(os.path.join(SANDBOX_TASK_DIR, name, "data_sniffing_report.md")):
        #     data_sniffing_result = await data_sniffing_agent(folder_path=f"/data/generic_data/{name}", task_meta_name=name)
        #     print(data_sniffing_result)
        #     time.sleep(1)
        # print("2. Task decomposition")
        # task_decomposition_result = await task_decomposition_agent(task_meta_name=name, meta_instruction=META_INSTRUCTION, iteration_number=3)
        # print(task_decomposition_result)
        # print("3. Task evaluation")
        # # run the task evaluation agent until selected_task_index.json is created in the folder
        # while not os.path.exists(os.path.join(SANDBOX_TASK_DIR, name, "selected_task_index.json")):
        #     task_evaluation_result = await evaluate_task_plans(meta_task_name=name, meta_instruction=META_INSTRUCTION)
        #     print(task_evaluation_result)
        #     time.sleep(1)
        # print("4. Task refinement") 
        # print("5. Workflow examination")
        # # run the workflow examination agent until two refined task group files are created
        # refined_files_count = 0

        # # load selected_task_index.json
        # with open(os.path.join(SANDBOX_TASK_DIR, name, "selected_task_index.json"), "r") as f:
        #     selected_task_index = json.load(f)


        # while refined_files_count < len(selected_task_index):
        #     workflow_examination_result = await workflow_examination_engine(meta_task_name=name, meta_instruction=META_INSTRUCTION)
        #     print(workflow_examination_result)
        #     # count the number of files with "_refined_task_group.json" suffix
        #     task_dir = os.path.join(SANDBOX_TASK_DIR, name)
        #     if os.path.exists(task_dir):
        #         refined_files_count = len([f for f in os.listdir(task_dir) if f.endswith("_refined_task_group.json")])
        #     time.sleep(1)

        # print("6. Code generation")
        # code_generation_result = await code_generation_engine(task_meta_name=name)
        # print(code_generation_result)
        print("7. Task execution")
        task_execution_result = await iterate_task_nodes(meta_task_name=name, meta_instruction=META_INSTRUCTION )
        print(task_execution_result)

if __name__ == "__main__":
    asyncio.run(semantic_stack_engine())

