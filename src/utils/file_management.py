def handle_generic_data_file_path(file_path: str):
    # if /data/generic_data
    # convert it to data/generic_data
    if file_path.startswith("/data/generic_data"):
        file_path = file_path.replace("/data/generic_data", "data/generic_data")
    return file_path


def remove_mnt_prefix(file_path: str):
    # if /mnt/c/Users/xz378/Documents/GitHub/mcp-tool-layer/data/generic_data/jinfeng/data_sniffing_report.md
    # convert it to data/generic_data/jinfeng/data_sniffing_report.md
    if file_path.startswith("/mnt/c/Users/xz378/Documents/GitHub/mcp-tool-layer/"):
        file_path = file_path.replace("/mnt/c/Users/xz378/Documents/GitHub/mcp-tool-layer/", "")
    return file_path


def handle_sandbox_task_dir(file_path: str):
    # if /sandbox/tasks/jinfeng/data_sniffing_report.md
    # convert it to data/generic_data/jinfeng/data_sniffing_report.md
    if file_path.startswith("/sandbox/tasks/"):
        file_path = file_path.replace("/sandbox/tasks/", "sandbox/tasks/")
    return file_path

if __name__ == "__main__":
    print(remove_mnt_prefix("/mnt/c/Users/xz378/Documents/GitHub/mcp-tool-layer/data/generic_data/jinfeng/data_sniffing_report.md"))