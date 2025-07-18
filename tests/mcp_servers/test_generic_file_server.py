from src.mcp_servers.generic.operations.file_operations import list_files_in_folder, create_new_file
import os
from src.utils.file_management import file_path_handling

def test_list_files_in_real_jiying_folder():
    # Use the actual folder path as specified
    folder_path = "file:///mnt/c/Users/xz378/Documents/GitHub/mcp-tool-layer/data/generic_data/jiying"
    files = list_files_in_folder(folder_path)
    print(files)



    assert "Non-Domestic EPC.csv" in files


def test_file_path_handling():
    file_path = "file:///mnt/c/Users/xz378/Documents/GitHub/mcp-tool-layer/data/generic_data/jiying/Non-Domestic EPC.csv"
    print(file_path_handling(file_path))
    assert file_path_handling(file_path) == "/mnt/c/Users/xz378/Documents/GitHub/mcp-tool-layer/data/generic_data/jiying/Non-Domestic EPC.csv"

def test_create_arbitrary_file():
    file_uri = "file:///mnt/c/Users/xz378/Documents/GitHub/mcp-tool-layer/data/test/data_sniffing_report.md"
    content = "This is a test file"
    result = create_new_file(file_uri, content)
    # the general output function should reject the attempt to write to the data_sniffing_report.md and suggest using report_output tool instead
    assert "Attempt to write to the data_sniffing_report.md is not allowed. For data sniffing, you should only use report_output tool instead." in result
 

if __name__ == "__main__":
    test_list_files_in_real_jiying_folder()
    test_create_arbitrary_file()
    test_file_path_handling()