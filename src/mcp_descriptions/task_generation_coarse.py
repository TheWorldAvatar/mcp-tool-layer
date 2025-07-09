TASK_GENERATION_COARSE_DESCRIPTION = """
    Create a new task file with reference to the AddTaskInput object. (This create one of many task files that will be used to form a task plan.)
 
    - task_id: Unique identifier for the task, which is a 6 character string.
    - name: Human-readable name of the task.
    - tools_required: List of tools required for this task, each tool is a Tool object. 
    - task_dependencies: List of task IDs this task depends on, which are the task IDs of the tasks that must be completed before this task can be started.
    - iteration_number: The iteration number of the task, which is an integer, starting from 0.

    Tool object:
        - name: The name of the tool.
        - is_hypothetical_tool: Whether the tool is hypothetical, i.e., not in the mcp tools available to you now.
        - is_llm_generation: Whether you think this task is suitable for directly using an LLM to create the output.

    Args:
        task_meta_name: The name of the overall task group, which is a string with meaning, in one task decomposition plan, all tasks should share the same overall task name.
        new_task: The input for the task, which is a AddTaskInput object.
    Returns:
        The file path where the new task is created. 
    """

TASK_ID_GENERATION_DESCRIPTION = """
    Generate a random id for each task, which is a 6 character string.
    - The id should be unique for each task.

    Returns:
        The random id for the task.
    """

TASK_INDEX_SELECTION_DESCRIPTION = """
This tool is used to output two selected task index (0-4) from a list of five task index. Will return the file path of the selected task files. 
"""