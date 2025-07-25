INSTRUCTION_PROMPT_GOLD = f"""

I have a gaussian output log file at data/test/benzene.log. This file contains the output of a gaussian calculation. 
I want you to integrate the data from the gaussian log file into my system stack. Make sure the data is integrated into the stack. 

**Important**: 

1. You are creating the task plan, not actually doing the work. 
2. You should consider all the tools you have access to, and make sure the plan is compatible with the existing system. 

Keep in mind a strategy that: 

1. Read the tool descriptions carefully, where you can learn how the underlying system works. 
2. Use as many existing tools as possible, as existing tools are more likely to be compatible with the existing system. 
3. Don't be afraid to include hypothetical tools, which will be created for the tasks
4. In your plan, you should avoid using filesystem tools to read any file. 
5. In some rare cases, you can use LLMs to generate file content, including report writing, schema file creation, etc. 
6. Break the tasks in to as small as possible subtasks, each subtask should be yield a single task file. 
7. Make sure your plan reaches the final goal. 

Create task files with task_generation tool. (You need to create the task files instead of telling me plan in response)

"""

INSTRUCTION_GENERIC_PROMPT = """

Your task is to decompose a large task by making a set of task files to achieve an overal goal **step by step**. 

Create task files with create_new_tool_task tool. (You need to create the task files instead of telling me plan in response). Remember, you must create the task files, you can't just tell me the plan. 


This is the overall goal: 

{meta_instruction}
--- 

You will also need a task_meta_name for creating task files, which is: {task_meta_name} and the iteration number, which is: {iteration_number}

The following is the data sniffing report, which tells you want data are there in the first placeand what are their purposes 

{data_sniffing_report}

**Important**: 

1. You should consider all the tools you have access to, and make sure the plan is compatible with the existing system. 
2. You are encouraged to propose tools that doesn't exist yet, but are necessary for the task. 
3. In your task plan, there is no need to do any clean-up or revision of executions, during the execution of the task plans, 
there will be explicit indication of the execution results. 
4. You must use different task_id for each task file, which is generated by generate_task_id tool. 

Keep in mind a strategy that: 

1. Read the tool descriptions carefully, where you can learn how the underlying system works.
2. Use as many existing tools as possible, as existing tools are more likely to be compatible with the existing system. 
3. Break the tasks in to as small as possible subtasks, the final outputs will be a set of task files. 
4. Make sure your plan reaches the final goal. 
5. Revision of data_sniffing_report.md should never be a task in the task plan. 

Create task files with create_new_tool_task tool. (You need to create the task files instead of telling me plan in response)
"""

INSTRUCTION_DATA_SNIFFING_PROMPT = """

** You can only try 10 iterations, if you keep getting errors, give up the task and report the error. **

You are provided a folder containing a set of data files, and maybe some documents to provide basic context. 

The folder uri is: {folder_uri}

You will also need a task_meta_name for creating task files, which is: {task_meta_name}

Your task is to look at the data files, and the documents, and write a analysis report of the data for further processing. 

Remember the following rules: 
1. Never read a file with read_file, or read_multiple_files tool, instead, use the generic_operations tool to read just a sample of the file. 
2. Never read a document with read_file, or read_multiple_files tool, instead, use the generic_operations tool to read just a sample of the document. 
3. You are allowed to adjust the max_length parameter of the generic_operations tool to read a larger sample of the file or document if you think it is necessary. 

The report should be in the following format: 

1. A summary of the data, including the number of files, the number of documents, and the number of data points. 
2. An educated guess of the purpose of the data, and the basic structure of the data. 
3. The report should be in markdown format, in the following format: 

```markdown
    - Folder uri: <folder_uri>
    - Files:
        - File name: <file_name>
        - File type: <file_type>
        - File size: <file_size>
    - Summary of the data: <summary_of_the_data>
    - Purpose of the data: <purpose_of_the_data>

Output the report file with the name "data_sniffing_report.md". Use output_data_sniffing_report tool to output the report file. 
 
```
 
"""