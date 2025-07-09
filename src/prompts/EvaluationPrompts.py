TASK_GROUP_SELECTION_PROMPT = """
Your task is to select two best task plans from five candidates. 
Each candidate forms a task plan according to the following task goal: 

Task Goal: {task_goal}

Task Meta Name: {meta_task_name}

You should consider the following aspects: 

1. Is the task plan complete? Does it reach the final goal? (Most important)
2. Is this plan compatible with the existing system? 
3. How detailed the plan is? (More detailed is better)

Use output_selected_task_index tool to output the selected task index. 

Here are the candidate reports, each report contains a full task plan and there are five reports in total, seperated by 
something like this: 

============ Iteration 0 =============

You should select the iteration number of the two best task plans ànd use the output_selected_task_index tool to output the selected task index (This is very important). 

------------------------------
{candidate_reports}
"""


WORKFLOW_EXAMINATION_PROMPT = """

Your task is to check a task group and add missing steps if necessary. 
The task goal is: {task_goal}
Add extra step if you find, in the workflow, there are files missing between the steps. You can look at the resource to know what files are available to you in the first place, and review the workflow to see whether some files are missing. Don't be afraid to add extra steps. It is possible for creating new tools for the missing step. 
Also look at the tools available to you, their descriptions tells you some important information about what are required to do the task. 

You should also revise the two Tool object fields in the task object, is_llm_generation and is_hypothetical_tool.

If the task is suitable for directly using an LLM to create the output, set is_llm_generation to True. This usually involves creating ontologies. 

Here is the whole workflow: 

{summarized_task_group}

Use output_refined_task_group tool to output the revised task group, please do not use any other tools to output this. 

The iteration index of the task object is {iteration_index}. The meta task name is {meta_task_name}. These are required for figuring out the output dir. 


"""


SINGLE_TASK_REFINEMENT_PROMPT = """
You are a task refinement agent. 

You are given a task object. The task object is a single step within a larger task plan. 

This is the task object: {task_object}

Your task is to refine the task object, mostly on whether this particular step uses the optimal tools. 


You should carefully consider the mcp tools available to you and the current task object in the following rules:

1. You should not use LLMs to read or extract information from relatively large files. 
2. In some cases you should use LLMs to generate files, for example, coming up with data schema.
3. You are allowed to add hypothetical tools that doesn't exist yet. 
4. **Important Note**: Note that read_file and write_file from filesystem only provides LLMs access to read and write files, they are not specific tools for any specific task. This means 
that it will still be the LLMs to do the content generation, which should be avoided in most of the cases. Overuse of these tools will lead to a lot of LLM calls, which is not efficient. 
5. Avoid using read_file and write_file for any data extraction task!!! (Unless you are sure that the data is small enough to be read and written by LLMs or there is not possiblity to parse the data by Python scripts.)

Here are some general guidelines: 

1. Before creating any schema, it usually requires understanding the data first. 
2. For large files that contains many data, it is better to create or use some tools to extract the data and use tools to write a schema file too.
3. For creating smaller files, for example an ontology file, it is better to use LLMs to generate the content. 
4. A schema file is nice to provide concentrated yet detailed information about the data, for tasks including creating ontologies, obda mapping files, etc. 

Use refine_task_file tool to refine the task object. 
"""