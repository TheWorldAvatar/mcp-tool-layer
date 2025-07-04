TASK_GENERATION_COARSE_DESCRIPTION = """
    Create a new tool task with reference to the AddTaskInput object.
 
    - task_id: Unique identifier for the task, which is a 8 character string.
    - name: Human-readable name of the task.
    - tools_required: List of tool names required for this task, each tool name is a string. Make sure you involve all the tools relevant to the task.
    - task_dependencies: List of task IDs this task depends on, which are the task IDs of the tasks that must be completed before this task can be started.
    - iteration_number: The iteration number of the task, which is an integer, starting from 0.

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