# math_server.py
from fastmcp import FastMCP
import logging
import pandas as pd
import os
from models.locations import ROOT_DIR, DATA_GENERIC_DIR
from docx import Document
from src.mcp_descriptions.generic_file_operations import CSV_FILE_SUMMARY_DESCRIPTION, WORD_FILE_SUMMARY_DESCRIPTION, TEXT_FILE_TRUNCATE_DESCRIPTION


mcp = FastMCP("generic_file_operations")


def convert_to_absolute_path(file_path: str) -> str:
    """
    This function converts a relative path to an absolute path.
    """
    # remove the first / if the path starts with it
    if file_path.startswith("/"):
        file_path = file_path[1:]
    return os.path.join(ROOT_DIR, file_path)

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

if __name__ == "__main__":
    mcp.run(transport="stdio")
    # test_path = "/data/generic_data/feroz/Coastal_flooding.docx"
    # print(word_file_summary(test_path))
    # test_path = "/data/generic_data/feroz/Coastal_flooding.docx"