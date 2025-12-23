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
import tarfile
from pathlib import Path
import logging
import sys

# Set up dedicated logger for file operations
logger = logging.getLogger("generic_file_ops")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter('[%(name)s] %(message)s'))
    logger.addHandler(handler)

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
    logger.info(f"=== code_output START ===")
    logger.info(f"task_meta_name={task_meta_name}, task_index={task_index}, script_name={script_name}")
    logger.info(f"code length={len(code)} bytes")
    logger.info(f"ROOT_DIR={ROOT_DIR}")
    logger.info(f"SANDBOX_CODE_DIR={SANDBOX_CODE_DIR}")
    
    # check the basic syntax of the code
    try:
        ast.parse(code)
        logger.info("✓ Code syntax valid")
    except Exception as e:
        error_msg = f"Error: {str(e)}"
        logger.error(f"✗ Code syntax error: {error_msg}")
        return error_msg

    # Compose the file URI for the code file
    # Special routing: underlying script creation writes to ai_generated_contents_candidate/scripts/<ontology>/
    if task_meta_name == "mcp_underlying_script_creation":
        logger.info("→ Using MCP script creation routing")
        # try to infer ontology from script name prefix (e.g., ontosynthesis_creation.py)
        base_name = script_name
        if base_name.endswith(".py"):
            base_name = base_name[:-3]
        
        # Special case: if script_name matches pattern "{ontology}_main.py"
        if base_name.endswith("_main"):
            # Extract ontology from pattern like "ontospecies_main"
            ontology_part = base_name[:-5]  # Remove "_main" suffix
            logger.info(f"→ Detected {ontology_part}_main.py pattern, using ontology: {ontology_part}")
            # Change the actual filename to main.py (without ontology prefix)
            script_name = "main.py"
        elif base_name == "main":
            logger.info("→ Detected main.py - searching for existing ontology folders")
            scripts_base = os.path.join(ROOT_DIR, "ai_generated_contents_candidate", "scripts")
            ontology_part = None
            
            # Look for directories with *_creation.py files
            if os.path.exists(scripts_base):
                for dir_name in os.listdir(scripts_base):
                    dir_path = os.path.join(scripts_base, dir_name)
                    if os.path.isdir(dir_path):
                        creation_file = os.path.join(dir_path, f"{dir_name}_creation.py")
                        if os.path.exists(creation_file):
                            ontology_part = dir_name
                            logger.info(f"→ Found ontology folder: {ontology_part} (has {dir_name}_creation.py)")
                            break
            
            if not ontology_part:
                logger.warning("→ Could not find ontology folder for main.py, defaulting to 'main'")
                ontology_part = "main"
        else:
            ontology_part = base_name.split("_", 1)[0] if "_" in base_name else base_name
        
        logger.info(f"→ Inferred ontology: {ontology_part} from script_name: {script_name}")
        
        scripts_dir = os.path.join(ROOT_DIR, "ai_generated_contents_candidate", "scripts", ontology_part)
        logger.info(f"→ Target scripts_dir: {scripts_dir}")
        
        try:
            os.makedirs(scripts_dir, exist_ok=True)
            logger.info(f"✓ Created/verified scripts dir: {scripts_dir}")
            logger.info(f"✓ Directory exists: {os.path.exists(scripts_dir)}")
        except Exception as e:
            logger.error(f"✗ Failed to create scripts dir {scripts_dir}: {e}")
        
        code_path = os.path.join(scripts_dir, script_name if script_name.endswith(".py") else f"{script_name}.py")
        logger.info(f"→ Final code_path: {code_path}")
    else:
        logger.info(f"→ Using sandbox routing for task: {task_meta_name}")
        output_dir = os.path.join(SANDBOX_CODE_DIR, task_meta_name, str(task_index))
        logger.info(f"→ Target output_dir: {output_dir}")
        
        try:
            os.makedirs(output_dir, exist_ok=True)
            logger.info(f"✓ Created/verified output dir: {output_dir}")
        except Exception as e:
            logger.error(f"✗ Failed to create output dir {output_dir}: {e}")
        
        # if script_name ends with .py, remove the .py
        if script_name.endswith(".py"):
            script_name = script_name[:-3]
        code_path = os.path.join(output_dir, f"{script_name}.py")
        logger.info(f"→ Final code_path: {code_path}")
    
    file_uri = f"file://{code_path}"
    logger.info(f"→ Constructed file_uri: {file_uri}")

    # Use safe_handle_file_write to write the code, as in create_new_file
    logger.info(f"→ Calling safe_handle_file_write...")
    try:
        result_uri = safe_handle_file_write(file_uri, code)
        logger.info(f"✓ safe_handle_file_write returned: {result_uri}")
        logger.info(f"✓ File exists after write: {os.path.exists(code_path)}")
        if os.path.exists(code_path):
            logger.info(f"✓ File size: {os.path.getsize(code_path)} bytes")
    except Exception as e:
        logger.error(f"✗ safe_handle_file_write failed: {e}", exc_info=True)
        raise

    # register the file to the resource db
    resource_db_operator.register_resource(Resource(type="script", relative_path=result_uri, absolute_path=result_uri, uri=result_uri, meta_task_name=task_meta_name))
    logger.info(f"✓ Registered resource in DB")
    logger.info(f"=== code_output COMPLETE ===")
    
    # Flush logger
    for handler in logger.handlers:
        handler.flush()
    
    return f"Code has been written to {result_uri}"


def csv_file_summary(file_uri: str) -> str:
    if not file_uri.endswith(".csv"):
        return f"File {file_uri} is not a csv file."
    
    fs, path = fsspec.core.url_to_fs(file_uri)
    file_size_mb = fs.size(path) / (1024 * 1024)

    with fs.open(path, "r", encoding_errors="ignore") as f:
        df = pd.read_csv(f)

    summary = f"File size: {file_size_mb:.2f} MB\n\n"
    summary += df.head(5).to_string(index=False)

    return summary[:2000]

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
    logger.info(f"=== report_output START ===")
    logger.info(f"file_uri={file_uri}")
    logger.info(f"content length={len(file_content)} bytes")

    # reject attempt to write to the sandbox/data or sandbox/code
    if "sandbox/data" in file_uri or "sandbox/code" in file_uri:
        logger.warning(f"✗ Rejected: attempt to write to sandbox/data or sandbox/code")
        return "Attempt to write to the sandbox/data or sandbox/code is not allowed. For code generation, you should only use code_output. Direct writing to sandbox/data or sandbox/code is not allowed in all cases.  "
    
    """
    Write file_content to the file specified by file_uri using fsspec.
    """
    try:
        # Ensure parent directory exists for local file URIs
        try:
            fs, path = fsspec.core.url_to_fs(file_uri)
            logger.info(f"→ Parsed URI: fs={type(fs).__name__}, path={path}")
            parent = os.path.dirname(path)
            if parent:
                try:
                    fs.makedirs(parent, exist_ok=True)
                    logger.info(f"✓ Ensured parent dir: {parent}")
                except Exception as e:
                    logger.error(f"✗ Failed to create parent dir {parent}: {e}")
        except Exception as e:
            logger.error(f"✗ Could not parse URI {file_uri} for dir ensure: {e}")

        logger.info(f"→ Calling safe_handle_file_write...")
        result = safe_handle_file_write(file_uri, file_content)
        if result.startswith("Write denied"):
            logger.error(f"✗ Write denied: {result}")
            raise Exception(result)
        logger.info(f"✓ safe_handle_file_write returned: {result}")
        logger.info(f"✓ File exists: {os.path.exists(path) if 'path' in locals() else 'unknown'}")
        logger.info(f"=== report_output COMPLETE ===")
        
        # Flush logger
        for handler in logger.handlers:
            handler.flush()
        
        return f"File {file_uri} has been written successfully."
    except Exception as e:
        error_msg = f"Error writing file {file_uri}: {str(e)}"
        logger.error(f"✗ report_output FAILED: {error_msg}", exc_info=True)
        
        # Flush logger
        for handler in logger.handlers:
            handler.flush()
        
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


def extract_tar_gz(archive_path: str) -> None:
    import shutil
    """Extract a .tar.gz (or .tgz) archive to the same path as the archive"""
    relative_path_with_out_protocol = file_path_handling(archive_path)

    # check if the archive path is a file
    if not os.path.isfile(relative_path_with_out_protocol):
        error_msg = f"Archive {archive_path} is not a file."
        raise FileNotFoundError(error_msg)



    dest = os.path.dirname(relative_path_with_out_protocol)
    shutil.unpack_archive(relative_path_with_out_protocol, dest)
    # remove the archive file
    os.remove(relative_path_with_out_protocol)
    return f"Archive {archive_path} has been extracted to {dest} and the archive file has been removed."


if __name__ == "__main__":
    # Test code_output for MCP script creation
    print("\n" + "="*60)
    print("Testing code_output for MCP script creation")
    print("="*60 + "\n")
    
    test_code = """#!/usr/bin/env python3
\"\"\"Test OntoSpecies Creation Script\"\"\"

def create_species(name: str) -> str:
    \"\"\"Create a species entity.\"\"\"
    return f"Created species: {name}"

if __name__ == "__main__":
    print(create_species("TestSpecies"))
"""
    
    try:
        result = code_output(
            code=test_code,
            task_meta_name="mcp_underlying_script_creation",
            task_index=1,
            script_name="ontospecies_creation.py"
        )
        print(f"\n✓ SUCCESS: {result}\n")
    except Exception as e:
        print(f"\n✗ FAILED: {e}\n")
        import traceback
        traceback.print_exc()
    
    print("="*60 + "\n")
