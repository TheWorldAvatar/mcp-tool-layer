TASK_GENERATION_DESCRIPTION = """
    Create a new tool task with reference to the AddTaskInput object.

    This is different from LLM tasks. Tool tasks are tasks that are completed by tools, while LLM tasks are tasks that are completed directly by LLM generation. 

    - task_id: Unique identifier for the task, which is a 8 character string.
    - name: Human-readable name of the task.
    - description: Short description of what the task goal. 
    - tools: List of tools required for this task, each tool is a Tool object. (direct_generation is True if the task is a LLM generation task, including report writing, ttl file creation, etc.)
    - dependencies: List of task IDs this task depends on, which are the task IDs of the tasks that must be completed before this task can be started.

    Args:
        overall_task_name: The name of the overall task group. Will be given in the prompt.
        new_task: The input for the task, which is a AddTaskInput object.
    Returns:
        The file path where the new task is created. 
    """
TASK_ID_GENERATION_DESCRIPTION = """
    Generate a random id for eachtask, which is a 8 character string.
    - The id should be unique for each task.

    Returns:
        The random id for the task.
    """