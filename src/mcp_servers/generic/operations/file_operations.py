import logging
import pandas as pd
import os
from models.locations import ROOT_DIR, DATA_GENERIC_DIR, SANDBOX_CODE_DIR, SANDBOX_TASK_DIR
from docx import Document
import ast 
from src.utils.file_management import handle_generic_data_file_path, remove_mnt_prefix, handle_sandbox_task_dir


def create_new_file(file_path: str, content: str) -> str:
    # check if the file exists
    file_path = handle_sandbox_task_dir(file_path)
    # make the folder if it does not exist
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    # write the content to the file
    with open(file_path, "w", encoding="utf-8", errors="ignore") as f:
        f.write(content)
    return f"File {file_path} has been written successfully."

 
# output sandbox/code/task_meta_name/task_index/script_name.py
def code_output(code: str, task_meta_name: str, task_index: int, script_name: str) -> str:
    # check the basic syntax of the code

    try:
        ast.parse(code)
    except Exception as e:
        error_msg = f"Error: {str(e)}"
        return error_msg
    
    try:
        output_dir = os.path.join(SANDBOX_CODE_DIR, task_meta_name, str(task_index))
        if not os.path.exists(output_dir):
            os.makedirs(output_dir, exist_ok=True)
        
        # if script_name ends with .py, remove the .py
        if script_name.endswith(".py"):
            script_name = script_name[:-3]
        # if the code is ok, output it to the sandbox code dir
        code_path =  os.path.join(output_dir, f"{script_name}.py")
        code_path = remove_mnt_prefix(code_path)
        with open(code_path, "w") as f:
            f.write(code)
        return f"Code has been written to {code_path}"
    except Exception as e:
        error_msg = f"Error writing code to file: {str(e)}"
        raise Exception(error_msg)


def csv_file_summary(file_path: str) -> str:
    if not file_path.endswith(".csv"):
        error_msg = f"File {file_path} is not a csv file."
        return error_msg
    # check if the file exists and is a csv file
    if not os.path.exists(file_path):
        error_msg = f"File {file_path} does not exist."
        raise FileNotFoundError(error_msg)

    try:
        # read the csv file
        file_path = handle_generic_data_file_path(file_path)

        # get file size
        file_size = os.path.getsize(file_path)
        file_size_mb = file_size / (1024 * 1024)  # convert to MB

        df = pd.read_csv(file_path, encoding_errors="ignore")
        # return the head and a few sample rows (less than 5 rows) of the csv file
        summary = f"File size: {file_size_mb:.2f} MB\n\n"
        summary += df.head(5).to_string()
        return summary
    except Exception as e:
        error_msg = f"Error reading CSV file {file_path}: {str(e)}"
        raise Exception(error_msg)

def word_file_summary(file_path: str, max_length: int = 500) -> str:
    if not file_path.endswith(".docx"):
        error_msg = f"File {file_path} is not a word file."
        return error_msg

    # check if the file exists and is a word file
    if not os.path.exists(file_path):
        error_msg = f"File {file_path} does not exist."
        raise FileNotFoundError(error_msg)

    try:
        file_path = convert_to_absolute_path(file_path)
        # read the word file
        doc = Document(file_path)
        # extract all the text from the word file
        text = "\n".join([p.text for p in doc.paragraphs])
        return text[:max_length]
    except Exception as e:
        error_msg = f"Error reading Word file {file_path}: {str(e)}"
        raise Exception(error_msg)


def read_arbitrary_file(file_path: str) -> str:
    # check if the file exists
    file_path = handle_generic_data_file_path(file_path)
    file_path = remove_mnt_prefix(file_path)
    if not os.path.exists(file_path):
        error_msg = f"File {file_path} does not exist."
        raise FileNotFoundError(error_msg)
    
    # Try to read a short snippet of the file, handling both text and binary files
    try:
        with open(file_path, "rb") as f:
            snippet = f.read(256)
        # Try to decode as utf-8, fallback to hex if not possible
        try:
            snippet_text = snippet.decode("utf-8", errors="replace")
            content_preview = snippet_text
        except Exception:
            content_preview = snippet.hex()
        file_size = os.path.getsize(file_path)
        file_size_mb = file_size / (1024 * 1024)
        return f"File size: {file_size_mb:.2f} MB\nContent preview (first 256 bytes):\n{content_preview}"
    except Exception as e:
        error_msg = f"Could not read file {file_path}: {str(e)}"
        raise Exception(error_msg)

def text_file_truncate(file_path: str) -> str:
    if not file_path.endswith(".txt"):
        error_msg = f"File {file_path} is not a txt file."
        return error_msg
    # check if the file exists and is a text file
    if not os.path.exists(file_path):
        error_msg = f"File {file_path} does not exist."
        raise FileNotFoundError(error_msg)

    try:
        file_path = handle_generic_data_file_path(file_path)
        if not os.path.exists(file_path):
            error_msg = f"File {file_path} does not exist."
            raise FileNotFoundError(error_msg)
        
        # get file size
        file_size = os.path.getsize(file_path)
        file_size_mb = file_size / (1024 * 1024)  # convert to MB
        
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read().replace("\n", " ")
        # truncate the text to a certain length
        summary = f"File size: {file_size_mb:.2f} MB\n\n"
        summary += text[:500]
        return summary
    except Exception as e:
        error_msg = f"Error reading text file {file_path}: {str(e)}"
        raise Exception(error_msg)

def report_output(file_path: str, file_content: str) -> str:
    try:
        # read the file
        file_path = handle_sandbox_task_dir(file_path)

        # make the folder if it does not exist
        os.makedirs(os.path.dirname(file_path), exist_ok=True)

        # write the file content to the file
        with open(file_path, "w", encoding="utf-8", errors="ignore") as f:
            f.write(file_content)
        return f"File {file_path} has been written successfully."
    except Exception as e:
        error_msg = f"Error writing file {file_path}: {str(e)}"
        raise Exception(error_msg)


def read_markdown_file(file_path: str) -> str:
    try:
        file_path = remove_mnt_prefix(file_path)
        if not os.path.exists(file_path):
            error_msg = f"File {file_path} does not exist."
            raise FileNotFoundError(error_msg)

        # read the markdown file
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        return content
    except Exception as e:
        error_msg = f"Error reading markdown file {file_path}: {str(e)}"
        raise Exception(error_msg)
        return error_msg

# relative path /data/generic_data to data/generic_data
def list_files_in_folder(folder_path: str) -> str:
    try:
        # recursively list files in the folder with their sizes
        folder_path = handle_generic_data_file_path(folder_path)
        
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
                error_msg = f"Permission denied accessing: {path}"
                raise PermissionError(error_msg)
            except Exception as e:
                error_msg = f"Error accessing {path}: {str(e)}"
                raise Exception(error_msg)
            
            return file_info
        
        files_info = get_file_info(folder_path)
        return "\n".join(files_info)
    except Exception as e:
        error_msg = f"Error listing files in folder {folder_path}: {str(e)}"
        raise Exception(error_msg)


def convert_to_absolute_path(path: str) -> str:
    # Handle paths starting with /data/generic_data/
    if path.startswith("/data/generic_data/"):
        return os.path.join(DATA_GENERIC_DIR, path.split("/data/generic_data/")[1])
    
    # Handle paths starting with /sandbox/
    elif path.startswith("/sandbox/"):
        return os.path.join(SANDBOX_TASK_DIR, path.split("/sandbox/")[1])
    
    # Handle paths starting with data/generic_data/ (without leading slash)
    elif path.startswith("data/generic_data/"):
        return os.path.join(DATA_GENERIC_DIR, path.split("data/generic_data/")[1])
    
    # Handle paths starting with sandbox/ (without leading slash)
    elif path.startswith("sandbox/"):
        return os.path.join(SANDBOX_TASK_DIR, path.split("sandbox/")[1])
    
    # Return path as-is if no patterns match
    else:
        return path 