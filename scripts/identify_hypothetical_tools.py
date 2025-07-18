from models.locations import CONFIGS_DIR, SANDBOX_TASK_DIR
import json
import os


def clean_tool_name(tool_name: str):
    # remove functions. from the tool_name if it exists
    return tool_name.replace("functions.", "")

def lookup_tool_in_mcp_tools(tool_name: str, mcp_tools: dict):
    for tool_key, tool_info in mcp_tools.items():
        registered_function_name = tool_info["function_name"]
        if(registered_function_name == tool_name):
            return True
    return False


def identify_hypothetical_tools(meta_task_name: str):
    # find all x_refined_task_group.json files in the meta_task_name folder, iterate through them
    # for each file, load the file and iterate through the task_objects
    # for each task_object, iterate through the tools
    # if the tool is not in the mcp_tools, add it to the list
    # return the list


    with open(os.path.join(CONFIGS_DIR, "mcp_tools.json"), "r") as f:
        mcp_tools = json.load(f)

    refined_task_group_files = [f for f in os.listdir(os.path.join(SANDBOX_TASK_DIR, meta_task_name)) if f.endswith("_refined_task_group.json")]
    for file in refined_task_group_files:
        file_path = os.path.join(SANDBOX_TASK_DIR, meta_task_name, file)
        with open(file_path, "r") as f:
            refined_task_group = json.load(f)
            updated = False
            for task_object in refined_task_group:
                # if tools_required is empty, create a list of hypothetical tools
                if not task_object["tools_required"]:
                    task_object["tools_required"] = [{"name": "functions.hypothetical_tool", "is_hypothetical_tool": True, "is_llm_generation": False}]
                    updated = True
                else:
                    # if tools_required is not empty, iterate through the tools
                    for tool in task_object["tools_required"]:
                        if not lookup_tool_in_mcp_tools(clean_tool_name(tool["name"]), mcp_tools):
                            # this means this tool is not in the mcp_tools, so it is a hypothetical tool, you need to update 
                            # the refined_task_group file with the tool name
                            tool["is_hypothetical_tool"] = True
                            updated = True
                        else:
                            tool["is_hypothetical_tool"] = False
            if updated:
                with open(file_path, "w") as f_out:
                    json.dump(refined_task_group, f_out, indent=2)

 
if __name__ == "__main__":
    identify_hypothetical_tools("jiying")