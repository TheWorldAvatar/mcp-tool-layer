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

Stop when the output file is successfully written. 

------------------------------
{candidate_reports}
"""


WORKFLOW_EXAMINATION_PROMPT = """

Your job is to **audit and upgrade** the task-group below.  
Do **NOT** execute any steps—only inspect, add, reorder, or clarify them.

Don't actually call stack and other tools, just use the information provided to you to refine the task group. 

output_refined_task_group is the only tool you are allowed to call. Other tools are provided only for your reference.

────────────────────────────────────────────────────────
**Task goal:** {task_goal}

**Current workflow:**  
{summarized_task_group}

**Context values you will need**  
• **iteration_index:** {iteration_index}  
• **meta_task_name:** {meta_task_name}  
────────────────────────────────────────────────────────

### What to do


1. **Read every tool description carefully.**  
   Use a tool *only* if you are certain it matches a step’s need.  
   • If no existing tool fits, you may invent a **HypotheticalTool** (give it a clear name and one-sentence description) and mark the related task’s `is_hypothetical_tool = True`.

2. **Find and fix gaps.**  
   • Add, split, or reorder steps wherever intermediate information or files are missing.  
   • Keep the workflow as short as possible **but no shorter** than needed to reach the goal.

3. **Decide if the output can be produced directly by an LLM.**  
   • If so, set that task’s `is_llm_generation = True`; otherwise leave it `False`.

4. **Validate the final workflow.**  
   • Ensure every input in later steps is produced by an earlier step or is already available in `resource`.  
   • Confirm that each step references tools legitimately and that hypothetical tools are clearly flagged.

5. **Output your revision** **once**—and only—by calling  
   ** Make sure you use the `output_refined_task_group` tool to output the revised task group.** with the complete, updated task-group object.  
   (Do not call any other tools for the final output.)


> **Be bold but precise:** add missing pieces, invent tools when justified, and never misuse a tool.

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