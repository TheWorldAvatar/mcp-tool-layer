import pandas as pd
import os
from models.locations import ROOT_DIR, DATA_GENERIC_DIR, SANDBOX_CODE_DIR, SANDBOX_TASK_DIR
from docx import Document
import ast 
from src.utils.file_management import safe_handle_file_write, check_if_folder_or_file_exists, file_path_handling
from src.utils.resource_db_operations import ResourceDBOperator
from models.Resource import Resource
import fsspec
import json 
from typing import List
resource_db_operator = ResourceDBOperator()
 


def create_new_file(file_uri: str, content: str, task_meta_name: str = "", iteration: int = -1) -> str:
    # check if the file exists

    # reject attempt to write to the sandbox/data or sandbox/code
    if "sandbox/data" in file_uri or "sandbox/code" in file_uri:
        return "Attempt to write to the sandbox/data or sandbox/code is not allowed. For code generation, you should only use code_output. Direct writing to sandbox/data or sandbox/code is not allowed in all cases.  "

    if "data_sniffing_report.md" in file_uri:
        return "Attempt to write to the data_sniffing_report.md is not allowed. For data sniffing, you should only use report_output tool instead.  "

    file_uri = safe_handle_file_write(file_uri, content)
    # register the file to the resource db
    resource_db_operator.register_resource(Resource(type="file", relative_path=file_uri, absolute_path=file_uri, uri=file_uri, meta_task_name=task_meta_name, iteration=iteration))
    return f"File {file_uri} has been written successfully."

 
def code_output(code: str, task_meta_name: str, task_index: int, script_name: str) -> str:
    # check the basic syntax of the code
    try:
        ast.parse(code)
    except Exception as e:
        error_msg = f"Error: {str(e)}"
        return error_msg

    # Compose the file URI for the code file in the sandbox code directory
    output_dir = os.path.join(SANDBOX_CODE_DIR, task_meta_name, str(task_index))
    # if script_name ends with .py, remove the .py
    if script_name.endswith(".py"):
        script_name = script_name[:-3]
    code_path = os.path.join(output_dir, f"{script_name}.py")
    file_uri = f"file://{code_path}"

    # Use safe_handle_file_write to write the code, as in create_new_file
    file_uri = safe_handle_file_write(file_uri, code)

    # register the file to the resource db
    resource_db_operator.register_resource(Resource(type="script", relative_path=file_uri, absolute_path=file_uri, uri=file_uri, meta_task_name=task_meta_name))
    return f"Code has been written to {file_uri}"


def csv_file_summary(file_uri: str) -> str:
    if not file_uri.endswith(".csv"):
        error_msg = f"File {file_uri} is not a csv file."
        return error_msg
    # check if the file exists and is a csv file
 
    fs, path = fsspec.core.url_to_fs(file_uri)
    # Step 1: Get file size (in bytes → MB)
    file_size_bytes = fs.size(path)
    file_size_mb = file_size_bytes / (1024 * 1024)

    # Step 2: Open the file and load a DataFrame
    with fs.open(path, "r", encoding_errors="ignore") as f:
        df = pd.read_csv(f)

    # Step 3: Return summary
    summary = f"File size: {file_size_mb:.2f} MB\n\n"
    summary += df.head(5).to_string(index=False)
    return summary

def word_file_summary(file_uri: str, max_length: int = 500) -> str:
    if not file_uri.endswith(".docx"):
        error_msg = f"File {file_uri} is not a word file."
        return error_msg

    # check if the file exists and is a word file
    resource = resource_db_operator.get_resource_by_uri(file_uri)
    if resource is None:
        error_msg = f"File {file_uri} does not exist."
        return error_msg

    try:
        file_path = resource.absolute_path
        # read the word file
        doc = Document(file_path)
        # extract all the text from the word file
        text = "\n".join([p.text for p in doc.paragraphs])
        return text[:max_length]
    except Exception as e:
        error_msg = f"Error reading Word file {file_path}: {str(e)}"
        raise Exception(error_msg)


def output_data_sniffing_report(file_content: str, meta_task_name: str) -> str:

 
    file_path = os.path.join(SANDBOX_TASK_DIR, meta_task_name, "data_sniffing_report.md")
    file_uri = f"file://{file_path}"


    # register the file to the resource db
    resource_db_operator.register_resource(Resource(type="report", relative_path=file_uri, absolute_path=file_uri, uri=file_uri, meta_task_name=meta_task_name, iteration=-1, description=f"data sniffing report for task {meta_task_name}"))
    return report_output(file_uri=file_uri, file_content=file_content)

def read_arbitrary_file(file_uri: str) -> str:
    """
    Read a short snippet of an arbitrary file using its URI, always using fsspec.
    Returns a preview of the file content and its size.
    """
    # Always use fsspec and uri
    fs, path = fsspec.core.url_to_fs(file_uri)
    if not fs.exists(path):
        error_msg = f"File {file_uri} does not exist."
        raise FileNotFoundError(error_msg)

    try:
        with fs.open(path, "rb") as f:
            snippet = f.read(256)
        # Try to decode as utf-8, fallback to hex if not possible
        try:
            snippet_text = snippet.decode("utf-8", errors="replace")
            content_preview = snippet_text
        except Exception:
            content_preview = snippet.hex()
        file_size = fs.size(path)
        file_size_mb = file_size / (1024 * 1024)
        file_extension = path.split(".")[-1]
        return f"File size: {file_size_mb:.2f} MB\nFile extension: {file_extension}\nContent preview (first 256 bytes):\n{content_preview}"
    except Exception as e:
        error_msg = f"Could not read file {file_uri}: {str(e)}"
        raise Exception(error_msg)

def text_file_truncate(file_uri: str) -> str:
    """
    Truncate a text file (given as a URI) and return a summary with file size and first 500 characters.
    Always uses fsspec and uri.
    """
    if not file_uri.endswith(".txt"):
        error_msg = f"File {file_uri} is not a txt file."
        return error_msg

    import fsspec
    try:
        fs, path = fsspec.core.url_to_fs(file_uri)
        if not fs.exists(path):
            error_msg = f"File {file_uri} does not exist."
            raise FileNotFoundError(error_msg)

        file_size = fs.size(path)
        file_size_mb = file_size / (1024 * 1024)  # convert to MB

        with fs.open(path, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read().replace("\n", " ")
        summary = f"File size: {file_size_mb:.2f} MB\n\n"
        summary += text[:500]
        return summary
    except Exception as e:
        error_msg = f"Error reading text file {file_uri}: {str(e)}"
        raise Exception(error_msg)

def report_output(file_uri: str, file_content: str) -> str:


    # reject attempt to write to the sandbox/data or sandbox/code
    if "sandbox/data" in file_uri or "sandbox/code" in file_uri:
        return "Attempt to write to the sandbox/data or sandbox/code is not allowed. For code generation, you should only use code_output. Direct writing to sandbox/data or sandbox/code is not allowed in all cases.  "
    
    """
    Write file_content to the file specified by file_uri using fsspec.
    """
    try:
        result = safe_handle_file_write(file_uri, file_content)
        if result.startswith("Write denied"):
            raise Exception(result)
        return f"File {file_uri} has been written successfully."
    except Exception as e:
        error_msg = f"Error writing file {file_uri}: {str(e)}"
        raise Exception(error_msg)

def read_markdown_file(file_uri: str) -> str:
    """
    Read a markdown file using its URI and fsspec.
    """

    # fuzzy search the file_uri
    resource = fuzzy_repo_file_search(file_uri, resource_db_operator.get_all_resources())
    if resource is None:
        error_msg = f"File {file_uri} does not exist in the resource db."
        raise FileNotFoundError(error_msg)
    file_uri = resource.uri


    try:
        fs, path = fsspec.core.url_to_fs(file_uri)
        if not fs.exists(path):
            error_msg = f"File {file_uri} does not exist."
            raise FileNotFoundError(error_msg)
        with fs.open(path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        return content
    except Exception as e:
        error_msg = f"Error reading markdown file {file_uri}: {str(e)}"
        raise Exception(error_msg)

def list_files_in_folder(relative_path: str) -> str:
    """
    Recursively list files in the folder specified by relative_path, with their sizes.
    """
    # check if the folder exists
    relative_path_with_out_protocol = file_path_handling(relative_path)
    print(f"relative_path_with_out_protocol: {relative_path_with_out_protocol}")
 
    if not os.path.exists(relative_path_with_out_protocol):
        error_msg = f"Folder {relative_path_with_out_protocol} does not exist."
        return error_msg
    
    # list the files in the folder
    files = os.listdir(relative_path_with_out_protocol)

    # add file:// to the files if they don't have it
    # give the file paths with the relative path
    files_with_relative_path = [os.path.join(relative_path_with_out_protocol, file) for file in files]
    files_with_relative_path = [f"file://{file}" for file in files_with_relative_path if not file.startswith("file://")]
    print(f"files_with_relative_path: {files_with_relative_path}")

    return f"Listing of the files successfully completed. The files are: {files_with_relative_path}"


if __name__ == "__main__":
    print(list_files_in_folder("file:///mnt/c/Users/xz378/Documents/GitHub/mcp-tool-layer/data/generic_data/jiying"))
    print("="*100)
    summary = csv_file_summary("file:////mnt/c/Users/xz378/Documents/GitHub/mcp-tool-layer/data/generic_data/jiying/Non-Domestic EPC.csv")
    print(summary)