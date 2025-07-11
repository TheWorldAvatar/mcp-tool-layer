from fastmcp import FastMCP
from src.mcp_descriptions.generic_file_operations import CSV_FILE_SUMMARY_DESCRIPTION, WORD_FILE_SUMMARY_DESCRIPTION, TEXT_FILE_TRUNCATE_DESCRIPTION, REPORT_OUTPUT_DESCRIPTION, LIST_FILES_IN_FOLDER_DESCRIPTION, CODE_OUTPUT_DESCRIPTION
from src.mcp_descriptions.task_refinement import RESOURCE_REGISTRATION_DESCRIPTION
from typing import List
from pydantic import BaseModel
from typing import Optional

# Import functions from separated files
from src.mcp_servers.generic.operations.file_operations import (
    create_new_file,
    code_output,
    csv_file_summary,
    word_file_summary,
    read_arbitrary_file,
    text_file_truncate,
    report_output,
    read_markdown_file,
    list_files_in_folder
)

from src.mcp_servers.generic.operations.resource_registration import (
    ResourceRegistrationInput,
    output_resource_registration_report
)

mcp = FastMCP(name ="generic_operations", instructions="""This is a tool to perform generic file operations. It is used to create new files, read files, and write files. It is also used to register resources and create reports.
""")


# File Operations Tools
@mcp.tool(name="create_arbitary_file", description="Create a new file with arbitary extension.", tags=["generic_file_operations"])
def create_arbitary_file(file_path: str, content: str) -> str:
    return create_new_file(file_path, content)

@mcp.tool(name="code_output", description=CODE_OUTPUT_DESCRIPTION, tags=["generic_file_operations"])
def code_output_tool(code: str, task_meta_name: str, task_index: int, script_name: str) -> str:
    return code_output(code, task_meta_name, task_index, script_name)

@mcp.tool(name="csv_file_summary", description=CSV_FILE_SUMMARY_DESCRIPTION, tags=["generic_file_operations"])
def csv_file_summary_tool(file_path: str) -> str:
    return csv_file_summary(file_path)

@mcp.tool(name="word_file_summary", description=WORD_FILE_SUMMARY_DESCRIPTION, tags=["generic_file_operations"])
def word_file_summary_tool(file_path: str, max_length: int = 500) -> str:
    return word_file_summary(file_path, max_length)

@mcp.tool(name="read_arbitrary_file", description="Suitable to read arbitary files other than csv, word, and text files.", tags=["generic_file_operations"])
def read_arbitrary_file_tool(file_path: str) -> str:
    return read_arbitrary_file(file_path)

@mcp.tool(name="text_file_truncate", description=TEXT_FILE_TRUNCATE_DESCRIPTION, tags=["generic_file_operations"])
def text_file_truncate_tool(file_path: str) -> str:
    return text_file_truncate(file_path)

@mcp.tool(name="report_output", description=REPORT_OUTPUT_DESCRIPTION, tags=["generic_file_operations"])
def report_output_tool(file_path: str, file_content: str) -> str:
    return report_output(file_path, file_content)

@mcp.tool(name="read_markdown_file", description="Get the content of a markdown file.", tags=["generic_file_operations"])
def read_markdown_file_tool(file_path: str) -> str:
    return read_markdown_file(file_path)

@mcp.tool(name="list_files_in_folder", description=LIST_FILES_IN_FOLDER_DESCRIPTION, tags=["generic_file_operations"])
def list_files_in_folder_tool(folder_path: str) -> str:
    return list_files_in_folder(folder_path)


# Resource Registration Tools
@mcp.tool(name="output_resource_registration_report", description=RESOURCE_REGISTRATION_DESCRIPTION, tags=["resource_registration"])
def output_resource_registration_report_tool(meta_task_name: str, resource_registration_input: List[ResourceRegistrationInput]) -> str:
    return output_resource_registration_report(meta_task_name, resource_registration_input)


if __name__ == "__main__":
    mcp.run(transport="stdio") 