from fastmcp import FastMCP
from src.utils.global_logger import get_logger, mcp_tool_logger 
import json
from models.locations import SANDBOX_TASK_DIR
import os
    
from threading import Lock

logger = get_logger("mcp_server", "document_main")
mcp = FastMCP(name="document")

@mcp.tool(name="keep_or_discard_section", description="Mark a section as keep or discard in the sections.json file")
@mcp_tool_logger
def keep_or_discard_section(file_path: str, section_index: int, option: str, task_name: str) -> str:
    """
    Load a JSON file, update a section with "keep" or "discard", and save the JSON.
    
    Args:
        file_path: The filename (should be "sections.json")
        section_index: The section number to update
        option: Either "keep" or "discard"
        task_name: The task name (creates subfolder)
    
    Returns:
        Success message or error description
    """
    try:
        # Construct the full file path
        full_file_path = os.path.join(SANDBOX_TASK_DIR, task_name, file_path)
        logger.info(f"Updating section {section_index} in {full_file_path} with option {option}")

        # Ensure the directory exists
        os.makedirs(os.path.dirname(full_file_path), exist_ok=True)

        # Load existing data or create new if file doesn't exist
        if os.path.exists(full_file_path):
            with open(full_file_path, 'r', encoding='utf-8') as file:
                data = json.load(file)
        else:
            logger.warning(f"File {full_file_path} not found, creating new structure")
            data = {}

        section_key = f"Section {section_index}"
        if section_key in data:
            if option in ["keep", "discard"]:
                # Update the section with keep/discard label
                if isinstance(data[section_key], dict):
                    data[section_key]["keep_or_discard"] = option
                else:
                    # If it's just content, convert to dict structure
                    data[section_key] = {
                        "content": data[section_key],
                        "keep_or_discard": option
                    }
                logger.info(f"Updated {section_key} with option: {option}")
            else:
                return f"Invalid option '{option}'. Must be 'keep' or 'discard'"
        else:
            available_sections = ', '.join(data.keys())
            logger.error(f"Section {section_index} not found. Available sections are: {available_sections}")
            return f"Section {section_index} not found. Available sections: {available_sections}"

        # Save the updated data
        with open(full_file_path, 'w', encoding='utf-8') as file:
            json.dump(data, file, indent=4, ensure_ascii=False)
        
        return f"Successfully updated {section_key} with option: {option}"
        
    except Exception as e:
        error_msg = f"Error updating section {section_index}: {str(e)}"
        logger.error(error_msg)
        return error_msg

 
if __name__ == "__main__":
    mcp.run(transport="stdio")





