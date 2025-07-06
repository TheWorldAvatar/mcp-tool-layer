LIST_FILES_IN_FOLDER_DESCRIPTION = """
    This function allows listing the files in a folder. Recursively list the files in the folder with their sizes. 

    Args:
        folder_path: The path to the folder.

    Returns:
        A string containing the list of files in the folder.
    """

CODE_OUTPUT_DESCRIPTION = """
    This function allows writing a code to a file.

    Args:
        code: The code to write to the file.
        task_meta_name: The name of the task.
        iteration_index: The index of the iteration.
        script_name: The name of the script.

    Returns:
        A string indicating the code has been written successfully and the path to the file.
    """

REPORT_OUTPUT_DESCRIPTION = """
    This function allows writing a file with the given file path and file content.

    Args:
        file_path: The path to the file.
        file_content: The content to write to the file.

    Returns:
        A string indicating the file has been written successfully.
    """

CSV_FILE_SUMMARY_DESCRIPTION = """
    This function allows reading a csv file, which returns the head and a few sample rows of the csv file.
    
    Args:
        file_path: The path to the csv file.

    Returns:
        A string containing the head and a few sample rows of the csv file.
    """


WORD_FILE_SUMMARY_DESCRIPTION = """
    This function allows reading a word file, which returns the head and a few sample paragraphs of the word file.

    Args:
        file_path: The path to the word file.
        max_length: The maximum length of the word file.

    Returns:
        A string containing the head and a few sample paragraphs of the word file.
    """

TEXT_FILE_TRUNCATE_DESCRIPTION =  """
This function allows reading a text file, and truncate the file to a certain length.
    
    Args:
        file_path: The path to the text file.
        max_length: The maximum length of the text file.

    Returns:
        A string containing the truncated text file.
    """