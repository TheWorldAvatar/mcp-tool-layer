# math_server.py
from fastmcp import FastMCP
import logging
import pandas as pd
import os
from models.locations import ROOT_DIR, DATA_GENERIC_DIR, SANDBOX_CODE_DIR
from docx import Document
from src.mcp_descriptions.generic_file_operations import CSV_FILE_SUMMARY_DESCRIPTION, WORD_FILE_SUMMARY_DESCRIPTION, TEXT_FILE_TRUNCATE_DESCRIPTION, REPORT_OUTPUT_DESCRIPTION, LIST_FILES_IN_FOLDER_DESCRIPTION, CODE_OUTPUT_DESCRIPTION    
import ast 

mcp = FastMCP("generic_file_operations")


def convert_to_absolute_path(file_path: str) -> str:
    """
    This function converts a relative path to an absolute path.
    """
    # remove the first / if the path starts with it
    if file_path.startswith("/"):
        file_path = file_path[1:]
    return os.path.join(ROOT_DIR, file_path)


@mcp.tool(name="code_output", description=CODE_OUTPUT_DESCRIPTION, tags=["generic_file_operations"])
def code_output(code: str, task_meta_name: str, iteration_index: int, script_name: str) -> str:
    # check the basic syntax of the code
    try:
        ast.parse(code)
    except Exception as e:
        return f"Error: {str(e)}"	

    output_dir = os.path.join(SANDBOX_CODE_DIR, task_meta_name, str(iteration_index))
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    
    # if script_name ends with .py, remove the .py
    if script_name.endswith(".py"):
        script_name = script_name[:-3]
    # if the code is ok, output it to the sandbox code dir
    code_path =  os.path.join(output_dir, f"{script_name}.py")
    with open(code_path, "w") as f:
        f.write(code)
    return f"Code has been written to {code_path}"


@mcp.tool(name="csv_file_summary", description=CSV_FILE_SUMMARY_DESCRIPTION, tags=["generic_file_operations"])
def csv_file_summary(file_path: str) -> str:

    # read the csv file
    file_path = convert_to_absolute_path(file_path)
    print(f"Reading csv file: {file_path}")

    # get file size
    file_size = os.path.getsize(file_path)
    file_size_mb = file_size / (1024 * 1024)  # convert to MB

    df = pd.read_csv(file_path, encoding_errors="ignore")
    # return the head and a few sample rows (less than 5 rows) of the csv file
    summary = f"File size: {file_size_mb:.2f} MB\n\n"
    summary += df.head(5).to_string()
    return summary

@mcp.tool(name="word_file_summary", description=WORD_FILE_SUMMARY_DESCRIPTION, tags=["generic_file_operations"])
def word_file_summary(file_path: str, max_length: int = 500) -> str:

    file_path = convert_to_absolute_path(file_path)
    print(f"Reading word file: {file_path}")
    # read the word file
    doc = Document(file_path)
    # extract all the text from the word file
    text = "\n".join([p.text for p in doc.paragraphs])
    return text[:max_length]


@mcp.tool(name="text_file_truncate", description=TEXT_FILE_TRUNCATE_DESCRIPTION, tags=["generic_file_operations"])
def text_file_truncate(file_path: str) -> str:
    # read the text file
    # check if the file exists
    file_path = convert_to_absolute_path(file_path)
    print(f"Reading text file: {file_path}")
    if not os.path.exists(file_path):
        return f"File {file_path} does not exist."
    
    # get file size
    file_size = os.path.getsize(file_path)
    file_size_mb = file_size / (1024 * 1024)  # convert to MB
    
    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        text = f.read().replace("\n", " ")
    # truncate the text to a certain length
    summary = f"File size: {file_size_mb:.2f} MB\n\n"
    summary += text[:500]
    return summary

@mcp.tool(name="report_output", description=REPORT_OUTPUT_DESCRIPTION, tags=["generic_file_operations"])
def report_output(file_path: str, file_content: str) -> str:
    # read the file
    file_path = convert_to_absolute_path(file_path)
    # write the file content to the file
    with open(file_path, "w", encoding="utf-8", errors="ignore") as f:
        f.write(file_content)
    return f"File {file_path} has been written successfully."


@mcp.tool(name="list_files_in_folder", description=LIST_FILES_IN_FOLDER_DESCRIPTION, tags=["generic_file_operations"])
def list_files_in_folder(folder_path: str) -> str:
    # recursively list files in the folder with their sizes
    folder_path = convert_to_absolute_path(folder_path)
    
    def get_file_info(path):
        file_info = []
        try:
            for item in os.listdir(path):
                item_path = os.path.join(path, item)
                if os.path.isfile(item_path):
                    size = os.path.getsize(item_path)
                    size_mb = size / (1024 * 1024)  # convert to MB
                    file_info.append(f"File: {item} - Size: {size_mb:.2f} MB")
                elif os.path.isdir(item_path):
                    file_info.append(f"Directory: {item}/")
                    # recursively get files in subdirectory
                    sub_files = get_file_info(item_path)
                    for sub_file in sub_files:
                        file_info.append(f"  {sub_file}")
        except PermissionError:
            file_info.append(f"Permission denied accessing: {path}")
        except Exception as e:
            file_info.append(f"Error accessing {path}: {str(e)}")
        
        return file_info
    
    files_info = get_file_info(folder_path)
    return "\n".join(files_info)

if __name__ == "__main__":
    mcp.run(transport="stdio")
    # test_path = "/data/generic_data/feroz/Coastal_flooding.docx"
    # print(word_file_summary(test_path))
    # test_path = "/data/generic_data/feroz/Coastal_flooding.docx"