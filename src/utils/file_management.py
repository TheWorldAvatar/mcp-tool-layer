import os
import fsspec
from models.locations import DATA_DIR, SANDBOX_DIR, ROOT_DIR
from fuzzywuzzy import fuzz
from models.Resource import Resource
from pathlib import Path
from src.utils.resource_db_operations import ResourceDBOperator
import json 
import re

db_operator = ResourceDBOperator()


def fuzzy_repo_file_search(query: str) -> Resource:
    # Use fuzzywuzzy Levenshtein distance to search for the best-matching resource.
    # - Iterates through all resources registered in the database.
    # - Compares both the relative_path and absolute_path of each resource to the query.
    # - Removes path separators from both the query and resource paths before comparison.
    # - Calculates similarity for both relative and absolute paths, using the higher value.
    # - If the highest similarity is greater than 0.8, returns the corresponding resource object.
    # - If no resource meets the threshold, returns None.
    # - If multiple resources are above the threshold, returns the one with the highest similarity.

    best_match = None
    best_similarity = 0
    threshold = 0.8
    resources = db_operator.get_all_resources()     

    # Remove path separators from query for comparison
    query_no_path_sep = query.replace(os.path.sep, "").replace("/", "")

    for resource in resources:
        # Remove path separators from both relative and absolute paths
        rel_no_path_sep = resource.relative_path.replace(os.path.sep, "").replace("/", "")
        abs_no_path_sep = resource.absolute_path.replace(os.path.sep, "").replace("/", "")

        # Compare similarity for both relative and absolute paths
        sim_rel = fuzz.ratio(query_no_path_sep, rel_no_path_sep) / 100.0
        sim_abs = fuzz.ratio(query_no_path_sep, abs_no_path_sep) / 100.0

        similarity = max(sim_rel, sim_abs)

        if similarity > best_similarity:
            best_similarity = similarity
            best_match = resource

    if best_similarity >= threshold:
        return best_match
    else:
        return None


def read_file_content_from_uri(file_uri: str) -> str:
    fs, file_path = fsspec.core.url_to_fs(file_uri)
    with fs.open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def safe_write_json(file_uri: str, obj: dict, indent: int = 4) -> str:
    """
    Serialize `obj` as JSON and write to `file_uri` using safe write mechanism.
    """
    try:
        json_str = json.dumps(obj, indent=indent, ensure_ascii=False)
    except TypeError as e:
        return f"Failed to serialize JSON: {e}"

    return safe_handle_file_write(file_uri, json_str)

def safe_handle_file_write(file_uri: str, content: str) -> str:
    """
    Safely write content to a file URI. Folder will be created only if:
    - It's at most `max_depth` levels below a registered folder resource.
    - The folder lies within the whitelisted 'sandbox' directory.
    """

    max_depth = 3
    fs, file_path = fsspec.core.url_to_fs(file_uri)
    file_path = Path(file_path).resolve()

    # Enforce whitelist: must be inside "sandbox"
    if "sandbox" not in file_path.parts:
        return f"Write denied: '{file_path}' is outside the whitelisted 'sandbox' directory."

    # Step 1: Get parent folder Resource
    folder_path = file_path.parent.resolve()

    # Step 2: Check if folder exists
    if not fs.exists(str(folder_path)):
        # Step 3: Check for known base folder and enforce depth limit
        known_dirs = {
            Path(r.absolute_path).resolve()
            for r in db_operator.get_all_resources()
            if r.type == "folder"
        }

        # Walk up to find matching base folder
        current = folder_path
        depth = 0
        while current != current.parent:
            if current in known_dirs:
                break
            current = current.parent
            depth += 1
        else:
            # raise RuntimeError(f"No known base folder found for {folder_path}. Aborting write.")
            return f"No known base folder found for {folder_path}. Aborting write. You are operating outside the sandbox directory."

        if depth > max_depth:
            return f"Refused to create folder '{folder_path}': exceeds allowed depth ({depth} > {max_depth}) below known resource."

        # Step 4: Create the folder
        fs.mkdirs(str(folder_path), exist_ok=True)

    # Step 5: Write the file
    with fs.open(str(file_path), "w", encoding="utf-8", errors="ignore") as f:
        f.write(content)

    return f"File {file_uri} has been written successfully."

def check_if_folder_or_file_exists(resource: Resource) -> bool:
    if resource.type not in {"folder", "file", "script", "report"}:
        raise ValueError(f"Invalid resource type: {resource.type}")

    fs, path = fsspec.core.url_to_fs(resource.uri)

    # Check if it exists on the corresponding file system
    return fs.exists(path)

def get_file_folder_from_uri(uri: str) -> Resource:
    """
    Given a file URI, return a Resource representing its parent folder.
    """
    fs, file_path = fsspec.core.url_to_fs(uri)

    # Get the parent directory of the file path
    folder_path = Path(file_path).parent

    # Reconstruct folder URI correctly
    folder_uri = f"file://{folder_path.as_posix()}"

    # Avoid leading slash removal issues by ensuring consistent relative path handling
    try:
        relative_path = folder_path.relative_to(Path.cwd()).as_posix()
    except ValueError:
        relative_path = folder_path.as_posix()  # fallback to full path if not under cwd

    return Resource(
        type="folder",
        relative_path=relative_path,
        absolute_path=str(folder_path),
        uri=folder_uri
    )

def extract_meta_task_name(rel_path):
    """
    Extracts the meta task name from a relative path.
    Handles both:
      - data/generic_data/<xxx>/...
      - sandbox/tasks/<xxx>/...
    Returns <xxx> if matched, else "".
    """
    match = re.match(r"data/generic_data/([^/\\]+)", rel_path)
    if match:
        return match.group(1)
    match = re.match(r"sandbox/tasks/([^/\\]+)", rel_path)
    if match:
        return match.group(1)
    return ""


def scan_base_folders_recursively():
    BASE_FOLDERS = [DATA_DIR, SANDBOX_DIR]
    resources = []

    import re

    def is_data_log_folder(rel_path):
        """
        Returns True if rel_path is under data/log/ (but not the data/log folder itself).
        E.g. "data/log/jiying/0", "data/log/jiying/0/foo.py", etc.
        """
        # Normalize path separators
        rel_path_norm = rel_path.replace("\\", "/")
        # Match data/log/ at the start or anywhere in the path, but not exactly 'data/log'
        return bool(re.search(r"(^|/)data/log(/|$)", rel_path_norm)) and rel_path_norm != "data/log"

    def is_archive_folder(rel_path):
        """
        Returns True if rel_path is under archive/ (but not the archive folder itself).
        E.g. "archive/jiying/0", "archive/jiying/0/foo.py", etc.
        """
        rel_path_norm = rel_path.replace("\\", "/")
        # Match archive/ at the start or anywhere in the path, but not exactly 'archive'
        return bool(re.search(r"(^|/)archive(/|$)", rel_path_norm)) and rel_path_norm != "archive"

    def is_sandbox_task_subdir(rel_path):
        """
        Returns True if rel_path is under sandbox/tasks/<anything>/ (but not the <anything> folder itself).
        E.g. "sandbox/tasks/jiying/0", "sandbox/tasks/jiying/0/foo.py", etc.
        """
        # Match sandbox/tasks/<anything>/<something...>
        rel_path_norm = rel_path.replace("\\", "/")
        match = re.match(r"sandbox/tasks/[^/\\]+[/\\].+", rel_path_norm)
        return match is not None

    for BASE_FOLDER in BASE_FOLDERS:
        abs_base = os.path.abspath(BASE_FOLDER)
        rel_base = os.path.relpath(abs_base, ROOT_DIR)
        # Exclude BASE_FOLDER if it is or is under 'archive' or 'data/log'
        if is_archive_folder(rel_base) or is_data_log_folder(rel_base):
            continue

        # Register the BASE_FOLDER itself
        uri = f"file://{abs_base}"
        meta_task_name = extract_meta_task_name(rel_base)
        resources.append(Resource(
            type="folder",
            relative_path=rel_base,
            absolute_path=abs_base,
            uri=uri,
            meta_task_name=meta_task_name,
            iteration=-1
        ))

        for root, dirs, files in os.walk(BASE_FOLDER):
            # Compute rel_root for filtering
            abs_root = os.path.abspath(root)
            rel_root = os.path.relpath(abs_root, ROOT_DIR)
            # Filter out dirs containing 'archive' or 'data/log' in their path
            dirs[:] = [
                d for d in dirs
                if not is_archive_folder(os.path.join(rel_root, d))
                and not is_data_log_folder(os.path.join(rel_root, d))
            ]
            # Register each directory
            for dir_name in dirs:
                dir_path = os.path.join(root, dir_name)
                abs_path = os.path.abspath(dir_path)
                rel_path = os.path.relpath(abs_path, ROOT_DIR)
                # Skip if matches archive, data/log, or sandbox/tasks/<anything>/<something>
                if (
                    is_archive_folder(rel_path)
                    or is_data_log_folder(rel_path)
                    or is_sandbox_task_subdir(rel_path)
                ):
                    continue
                uri = f"file://{abs_path}"
                meta_task_name = extract_meta_task_name(rel_path)
                resources.append(Resource(
                    type="folder",
                    relative_path=rel_path,
                    absolute_path=abs_path,
                    uri=uri,
                    meta_task_name=meta_task_name,
                    iteration=-1
                ))
            # Register each file
            for file in files:
                file_path = os.path.join(root, file)
                abs_path = os.path.abspath(file_path)
                rel_path = os.path.relpath(abs_path, ROOT_DIR)
                # Skip if matches archive, data/log, or sandbox/tasks/<anything>/<something>
                if (
                    is_archive_folder(rel_path)
                    or is_data_log_folder(rel_path)
                    or is_sandbox_task_subdir(rel_path)
                ):
                    continue
                uri = f"file://{abs_path}"
                meta_task_name = extract_meta_task_name(rel_path)
                resources.append(Resource(
                    type="file",
                    relative_path=rel_path,
                    absolute_path=abs_path,
                    uri=uri,
                    meta_task_name=meta_task_name,
                    iteration=-1
                ))

    return resources



if __name__ == "__main__":
    # resources = scan_base_folders_recursively()
    # print(fuzzy_repo_file_search("data/jiying/data_sniffing_report.md", resources))
    # print(fuzzy_repo_file_search("/data/generic/jiying", resources))

    print(safe_handle_file_write("file:///mnt/c/Users/xz378/Documents/GitHub/mcp-tool-layer/sandbox/tasks/jiying/data_sniffing_report.md", "test"))
    # print(read_file_content_from_uri("file:///mnt/c/Users/xz378/Documents/GitHub/mcp-tool-layer/data/generic_data/xiaochi/data_sniffing_report.md"))

    # print(extract_meta_task_name("data/generic_data/patrick/MetalOrganicPolyhedron_91b0d0ca-c8b1-4dcc-8a4e-954899070a60_log.txt"))
    # print(extract_meta_task_name("sandbox/tasks/jiying/data_sniffing_report.md"))
