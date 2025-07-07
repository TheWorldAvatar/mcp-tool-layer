from __future__ import annotations
import asyncio
import logging
from fastmcp import FastMCP
from models.SubBaseAgent import build_react_agent


async def code_generation_agent(task_node: str, task_meta_name: str, iteration_index: int, resources: list) -> str:
    prompt = f"""
    You are a code generation agent that generates python scripts according to a subtask,.

    You are to generate the code and output it with the code_output tool, which will also check the basic syntax of the code and give you feedbacks. 

    Run the python script with run_sandbox_operation_python_file tool together with the docker tools in a sandbox container and check whether the code works. 

    The task meta name is {task_meta_name} and the iteration index is {iteration_index}. 

    Focus on the subtask. 

    Here are some general rules: 

    1. The dataflow in this system is usually file-based, so if your code were to produce any data, it should be saved to a file. 
    It also applies to the input data, usually, data are passed to the code as file paths. 

    2. Don't be afraid to use third party libraries to fulfill the user's request.

    3. Make sure you use enough try-except blocks to handle errors, which gives you a chance to revise the code and try again.

    4. Keep in mind that we are generally using python 3.11. 

    5. Make sure the output dir of the script you created is sandbox/data

    6. Make sure the script you created indicates the paths of the output files. 

    This is the task description, you should generate the code to fulfill the task: 

    {task_node}  

    Here are the resources available to you, make sure you use the correct file paths.  

    {resources}

    After you generate the script, also use resource_registration tool to add the script as a resource. 

    Remember the docker container name and the docker command for executing the script, you need to include them in the resource_registration tool. 
    """

    client, agent = await build_react_agent(mcp_keys=["docker", "generic_file_operations", "python_code_sandbox", "resource_registration"])

    result = await agent.ainvoke({"messages": prompt}, {"recursion_limit": 300})
    reply = result["messages"][-1].content
    return reply


async def code_test_agent(code: str) -> str:
    prompt = f"""
    You are a code test agent that tests the code to see if it fulfills the user's request.

    Here are some general rules: 

    """

    client, agent = await build_react_agent(mcp_keys=["filesystem", "docker"])

    result = await agent.ainvoke({"messages": prompt}, {"recursion_limit": 300})


if __name__ == "__main__":

    test_task_node = """

        {
        "task_id": "5447d6",
        "name": "Extract Data from GeoPackage",
        "description": "Extract spatial data from the GeoPackage file 'ukbuildings_6009073.gpkg' to a suitable format for integration into the stack.",
        "tools_required": [
            {
                "name": "extract_geopackage_data",
                "is_hypothetical_tool": true
            }
        ],
        "task_dependencies": [],
        "output_files": [
            "ukbuildings_data.json"
        ],
        "required_input_files": [
            "ukbuildings_6009073.gpkg"
        ]
    }

    """

    resources = [
    {
        "resource_name": "ukbuildings_6009073.gpkg",
        "resource_type": "file",
        "resource_description": "GeoPackage file containing spatial data of buildings in the UK.",
        "resource_location": "/data/generic_data/jinfeng/ukbuildings_6009073.gpkg"
    }]


    result = asyncio.run(code_generation_agent(task_node=test_task_node, task_meta_name="jinfeng", iteration_index=0, resources=resources))
    print(result)