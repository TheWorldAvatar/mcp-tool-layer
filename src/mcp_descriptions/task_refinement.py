TASK_GROUP_REFINEMENT_DESCRIPTION = """
    This tool is used to output the refined task group. 
    The input is a list of Task objects. 
    The output is a string indicating the file path where the refined task group is output. 

    Args:
        refined_task_group: the refined task group, which is a list of Task objects.
        meta_task_name: the name of the meta task, which is a string.
        iteration_index: the iteration index of the task, which is an integer.

    Returns:
        The file path where the refined task group is output. 


    Here is the breakdown of the tool object:
        Tool object: 
            - name: the name of the tool, which is a string. 
            - description: the description of the tool, which is a string. 

    Here is the breakdown of the task object:
        Task object: 
            - task_id: the task id, which is a string. 
            - name: the name of the task, which is a string. 
            - description: the description of the task, which is a string. 
            - tools_required: the tools required for the task, which is a list of Tool objects. 
            - dependencies: the task dependencies, which is the task ids of tasks that must be completed before this task can be started, which is a list of strings. 
    """


TASK_REFINEMENT_DESCRIPTION = """
    This tool is for outputing detailed updated task files.  

    - task_id: the task id remains the same as the old task. 
    - name: the name remains the same as the old task. 
    - description: a detailed description of the task, which is a string. 
    - tools_required: the tools required for the task, which is a list of Tool objects. If the task requires LLM generation of content, e.g., creating a ttl file, writing a report, include 
    "llm" in the list.
    - dependencies: remains the same as the old task. Make sure you put the task id of the parent tasks in the list. 


        Tool object: 
            - name: the name of the tool, which is a string. 
            - description: the description of the tool, which is a string. 

    Args:
        new_task: the input for the task, which is a AddDetailedTaskInput object.
        iteration_index: the iteration index of the task, which is an integer provided in the prompt.
        meta_task_name: the name of the meta task, which is a string provided in the prompt.
    Returns:
        The file path where the new task is created. 
    """

RESOURCE_REGISTRATION_DESCRIPTION = """
    This tool is used to register the resources found in the data folder. 

    The input is a list of ResourceRegistrationInput objects. 

    - resource_name: The name of the resource.
    - resource_type: The type of the resource. (Only the following options: file, document, database, api, script)
    - resource_description: The description of the resource.
    - resource_location: The location of the resource.

    For script only:
        - docker_container_id: The id of the docker container where the script is executed.
        - execution_command: The command to execute the script via docker. This usually include the full command starting with "docker exec" and the container id.
        - extra_libraries: The extra libraries installed for that.

    The output is a string indicating the file path where the resource registration report is output. 


    """