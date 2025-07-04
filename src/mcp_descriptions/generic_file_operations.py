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