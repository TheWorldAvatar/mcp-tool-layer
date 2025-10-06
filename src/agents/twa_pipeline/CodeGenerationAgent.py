from __future__ import annotations
import asyncio
from fastmcp import FastMCP
from models.SubBaseAgent import build_react_agent
from models.locations import DATA_LOG_DIR


async def code_generation_agent(task_node: str, task_meta_name: str, task_index: int, resources: str) -> str:
    prompt = f"""
    You are a code‑generation agent that writes Python scripts for each sub‑task.

    Write the script and check its syntax with the code_output tool (This is the only tool you can use to output the code).

    Run the script in docker containers with the docker tools, make sure the script path is relative path (e.g. "sandbox/code/<meta_task_name>/<task_index>/<script_name>.py").

    Use existing docker containers if possible, otherwise create a new one. (We should use python 3.11). 

    If the docker container you used works fine, remember to register it with the register_docker_container tool. You should also use list_registered_docker_containers_for_task tool to check if the container is already registered.  

    ** Important:** You should stick to the same container for the whole task.

    Refine the script until it executes without errors. 

    ** Important:** The code you generated is not for demonstration, it should be the full function.    

    Key rules
    • Use Python 3.11.
    • All input and output should be file‑based.
    • Always use relative paths to read and write files, without "/" in front of the path. 
    • When handing a task, you should always proirtize mature third-party libraries. e.g., cclib for gaussian, geopandas for geopandas, etc. 
    • Always check the files you created and make sure they make sense, otherwise, you should attempt to fix the issue. 

    input paths are usually provided in the resources.
    output paths always start with sandbox/data/{task_meta_name}/{task_index}, with no exception.


    • Feel free to pip install third‑party libraries inside the sandbox before running.
    • Add try–except blocks to handle errors gracefully.
    • Some tasks are tricky, you need to think carefully about the feedbacks. e.g., Parsing gaussian log files using cclib, you might encounter the error that the data is not consistent. In this case, you should think about the reason and the solution. For example,
    output the dictionary into multiple csv files, describing different aspects of the data. When you create the schema of the csv files, read multiple csv files and put the information together. 
    Also, don't assume what fields does the input data have while extracting the data, iterate dictionaries are usually the best way to go. And to play safe, try to extract all the information you can from the input data. 
    
    • When you are dealing with a number of files, your code should concatenate the csv files for the ** same data aspect ** into one csv file. For example, if you have n input files, and you decided to represent the data in m different aspects, you should output m csv files instead of n * m csv files.
    
     
    
    Focus on the sub‑task, iterate until the script works, and always indicate the paths of the output files.

    Args: 

    task_meta_name: {task_meta_name} 
    
    task_index: {task_index}

    Note that the task_index is the index of the task in the task group, not the iteration index. 

    The following are the current task node and all the resources available to you. In many cases, resources gives you some idea about the input files. 

    task_node: {task_node}

    resources: {resources}


    """
 
    client, agent = await build_react_agent(model_name="o4-mini", mcp_keys=["generic", "docker"])

    result = await agent.ainvoke({"messages": prompt}, {"recursion_limit": 100})
    reply = result["messages"][-1].content
    return reply
 

if __name__ == "__main__":
    result = asyncio.run(code_generation_agent(task_node = "", task_meta_name="jiying", task_index=0, resources=""))