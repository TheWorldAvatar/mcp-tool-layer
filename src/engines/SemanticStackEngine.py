"""
The semantic stack engine is responsible for the following tasks: 

1. Given **ANY** data from any domain, the semantic stack engine aims to create/coordinate tools to parse the data 
2. Generate the semantic description of the data 
3. Working out explicit workflow to parse and integrate the data into the semantic stack
4. Prepare the semantic data. 
"""
from src.agents.TaskDecompositionAgent import task_decomposition_agent
from src.agents.TaskDecompositionAgent import data_sniffing_agent
from src.agents.TaskEvaluationAgent import task_group_selection_agent
from src.engines.TaskRevisionEngine import evaluate_task_plans
from src.engines.TaskRevisionEngine import refine_task_plans
from src.engines.TaskRevisionEngine import summarize_refined_task_files
from src.engines.TaskRevisionEngine import workflow_examination_engine
from src.engines.utils.task_files import delete_task_tracing_file
import asyncio
import json

META_INSTRUCTION = """
You are provided a folder loaded with some data files, together with a data sniffing report (data_sniffing_report.md). 
The report summarizes the data in the folder, and provides a basic structure of the data. This is an important reference for you to understand the data. 
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
    name_list = ["jiying", "jinfeng", "patrick", "feroz"]
    delete_task_tracing_file() # remove the task tracing file, which is used to trace the overall task workflow
    for name in name_list:
        await data_sniffing_agent(folder_path=f"/data/generic_data/{name}", task_meta_name=name)
        await task_decomposition_agent(task_meta_name=name, data_folder_path=f"/sandbox/tasks/{name}", meta_instruction=META_INSTRUCTION, iteration_number=3)
        await evaluate_task_plans(meta_task_name=name, meta_instruction=META_INSTRUCTION)
        # await refine_task_plans(meta_task_name=name, meta_instruction=META_INSTRUCTION) # THIS AGENT IS NOT EFFECTIVE
        await workflow_examination_engine(meta_task_name=name, meta_instruction=META_INSTRUCTION)

if __name__ == "__main__":
    asyncio.run(semantic_stack_engine())

