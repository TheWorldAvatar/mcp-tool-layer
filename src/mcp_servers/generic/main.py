from fastmcp import FastMCP
from src.mcp_descriptions.generic_file_operations import CSV_FILE_SUMMARY_DESCRIPTION, WORD_FILE_SUMMARY_DESCRIPTION, TEXT_FILE_TRUNCATE_DESCRIPTION, REPORT_OUTPUT_DESCRIPTION, LIST_FILES_IN_FOLDER_DESCRIPTION, CODE_OUTPUT_DESCRIPTION
from src.utils.file_management import safe_handle_file_write, fuzzy_repo_file_search, read_file_content_from_uri
from models.locations import SANDBOX_TASK_DIR

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
    list_files_in_folder,
    output_data_sniffing_report,
    extract_tar_gz
)

 

mcp = FastMCP(name ="generic_operations", instructions="""This is a tool to perform generic file operations. It is used to create new files, read files, and write files. It is also used to register resources and create reports.
""")

from src.utils.global_logger import get_logger, mcp_tool_logger

logger = get_logger("mcp_server", "generic_main")

# File Operations Tools
@mcp.tool(name="create_arbitary_file", description="Create a new file with arbitary extension.", tags=["generic_file_operations"])
@mcp_tool_logger
def create_arbitary_file(file_path: str, content: str) -> str:
    # reject .py files
    if file_path.endswith(".py"):
        return "You are not allowed to create .py files with this tool, use code_output tool instead.   "   
    return create_new_file(file_path, content)

# Code Output Tools
@mcp.tool(name="code_output", description=CODE_OUTPUT_DESCRIPTION, tags=["generic_file_operations"])
@mcp_tool_logger
def code_output_tool(code: str, task_meta_name: str, task_index: int, script_name: str) -> str:
    logger.info(f"[code_output] task_meta_name={task_meta_name}, task_index={task_index}, script_name={script_name}")
    result = code_output(code, task_meta_name, task_index, script_name)
    logger.info(f"[code_output] result={result}")
    return result

@mcp.tool(name="csv_file_summary", description=CSV_FILE_SUMMARY_DESCRIPTION, tags=["generic_file_operations"])
@mcp_tool_logger
def csv_file_summary_tool(file_path: str) -> str:
    return csv_file_summary(file_path)

@mcp.tool(name="word_file_summary", description=WORD_FILE_SUMMARY_DESCRIPTION, tags=["generic_file_operations"])
@mcp_tool_logger
def word_file_summary_tool(file_path: str, max_length: int = 500) -> str:
    return word_file_summary(file_path, max_length)

@mcp.tool(name="read_arbitrary_file", description="Suitable to read arbitary files other than csv, word, and text files.", tags=["generic_file_operations"])
@mcp_tool_logger
def read_arbitrary_file_tool(file_path: str) -> str:
    return read_arbitrary_file(file_path)

@mcp.tool(name="text_file_truncate", description=TEXT_FILE_TRUNCATE_DESCRIPTION, tags=["generic_file_operations"])
@mcp_tool_logger
def text_file_truncate_tool(file_path: str) -> str:
    return text_file_truncate(file_path)

# Report Output Tools
@mcp.tool(name="report_output", description=REPORT_OUTPUT_DESCRIPTION, tags=["generic_file_operations"])
@mcp_tool_logger
def report_output_tool(file_uri: str, file_content: str) -> str:

    # reject .py files unless explicitly targeting scripts/ (allowed destination for generated scripts)
    if file_uri.endswith(".py"):
        normalized = file_uri.replace("\\", "/")
        if "/scripts/" not in normalized:
            return "You are not allowed to output .py files with this tool outside scripts/. Use code_output or write under scripts/<ontology>/.   "
    logger.info(f"[report_output] target={file_uri}, size={len(file_content)} bytes")
    result = report_output(file_uri, file_content)
    logger.info(f"[report_output] result={result}")
    return result

@mcp.tool(name="output_data_sniffing_report", description="Use this tool to output the data sniffing report, only provide the content and the meta_task_name", tags=["generic_file_operations"])
@mcp_tool_logger
def output_data_sniffing_report_tool(file_content: str, meta_task_name: str) -> str:
    return output_data_sniffing_report(file_content, meta_task_name)


@mcp.tool(name="read_markdown_file", description="Get the content of a markdown file.", tags=["generic_file_operations"])
@mcp_tool_logger
def read_markdown_file_tool(file_path: str) -> str:
    return read_markdown_file(file_path)

@mcp.tool(name="list_files_in_folder", description=LIST_FILES_IN_FOLDER_DESCRIPTION, tags=["generic_file_operations"])
@mcp_tool_logger
def list_files_in_folder_tool(folder_path: str) -> str:
    return list_files_in_folder(folder_path)
 

@mcp.tool(name="extract_tar_gz", description="Extract a .tar.gz (or .tgz) archive to the current directory.", tags=["generic_file_operations"])
@mcp_tool_logger
def extract_tar_gz_tool(archive_path: str) -> None:
    return extract_tar_gz(archive_path)

if __name__ == "__main__":
    mcp.run(transport="stdio") 