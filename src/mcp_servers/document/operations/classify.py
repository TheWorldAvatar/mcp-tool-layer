"""
Classification operations for document sections.

This module contains the core logic for classifying document sections
as "keep" or "discard" and managing the transition from unclassified
to classified JSON files.
"""

import json
import os
from models.locations import DATA_DIR
from src.utils.global_logger import get_logger

logger = get_logger("document_operations", "classify")


def classify_section(section_index: int, option: str, doi: str) -> dict:
    """
    Classify a section as keep or discard.
    
    This function loads sections from sections.json, updates the specified section 
    with the classification, and saves the result back to sections.json.
    
    Args:
        section_index: The section number to update
        option: Either "keep" or "discard"
        doi: The task name (DOI identifier)
    
    Returns:
        dict: Result with 'success' boolean and 'message' string
    """
    try:
        # Construct the full file path
        sections_file = os.path.join(DATA_DIR, doi, "sections.json")
        
        logger.info(f"Classifying section {section_index} with option '{option}' for task '{doi}'")
        logger.info(f"Sections file: {sections_file}")

        # Ensure the directory exists
        os.makedirs(os.path.dirname(sections_file), exist_ok=True)

        # Load from sections.json file
        if os.path.exists(sections_file):
            with open(sections_file, 'r', encoding='utf-8') as file:
                data = json.load(file)
                logger.info(f"Loaded sections data from {sections_file}")
        else:
            error_msg = f"Sections file not found: {sections_file}"
            logger.error(error_msg)
            return {
                'success': False,
                'message': f"Error: sections.json not found in {os.path.dirname(sections_file)}"
            }

        # Validate option
        if option not in ["keep", "discard"]:
            error_msg = f"Invalid option '{option}'. Must be 'keep' or 'discard'"
            logger.error(error_msg)
            return {
                'success': False,
                'message': error_msg
            }

        # Find and update the section
        section_key = f"Section {section_index}"
        if section_key in data:
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
            available_sections = ', '.join(data.keys())
            error_msg = f"Section {section_index} not found. Available sections: {available_sections}"
            logger.error(error_msg)
            return {
                'success': False,
                'message': error_msg
            }

        # Save the updated data back to sections.json
        with open(sections_file, 'w', encoding='utf-8') as file:
            json.dump(data, file, indent=4, ensure_ascii=False)
        
        success_msg = f"Successfully updated {section_key} with option: {option}"
        logger.info(f"Saved updated sections data to {sections_file}")
        logger.info(success_msg)
        
        return {
            'success': True,
            'message': success_msg
        }
        
    except Exception as e:
        error_msg = f"Error classifying section {section_index}: {str(e)}"
        logger.error(error_msg)
        logger.exception("Full exception details:")
        return {
            'success': False,
            'message': error_msg
        }


def load_sections(doi: str, classified: bool = False) -> dict:
    """
    Load sections from JSON file.
    
    Args:
        doi: The task name (DOI identifier)
        classified: If True, load classified sections; otherwise load unclassified
                  (Note: Both now use the same sections.json file)
    
    Returns:
        dict: The sections dictionary, or None if file not found
    """
    try:
        filename = "sections.json"
        file_path = os.path.join(DATA_DIR, doi, filename)
        
        if os.path.exists(file_path):
            with open(file_path, 'r', encoding='utf-8') as file:
                data = json.load(file)
                logger.info(f"Loaded {len(data)} sections from {file_path}")
                return data
        else:
            logger.warning(f"File not found: {file_path}")
            return None
            
    except Exception as e:
        logger.error(f"Error loading sections: {str(e)}")
        return None


def get_classification_status(doi: str) -> dict:
    """
    Get the classification status for a task.
    
    Args:
        doi: The task name (DOI identifier)
    
    Returns:
        dict: Status information including counts and percentages
    """
    try:
        sections_data = load_sections(doi, classified=True)
        
        if sections_data is None:
            return {
                'success': False,
                'message': 'No classified data found',
                'total_sections': 0,
                'classified': 0,
                'keep': 0,
                'discard': 0
            }
        
        total = len(sections_data)
        classified_count = 0
        keep_count = 0
        discard_count = 0
        
        for section_key, section_data in sections_data.items():
            if isinstance(section_data, dict) and 'keep_or_discard' in section_data:
                classified_count += 1
                if section_data['keep_or_discard'] == 'keep':
                    keep_count += 1
                elif section_data['keep_or_discard'] == 'discard':
                    discard_count += 1
        
        return {
            'success': True,
            'total_sections': total,
            'classified': classified_count,
            'keep': keep_count,
            'discard': discard_count,
            'percentage_complete': (classified_count / total * 100) if total > 0 else 0
        }
        
    except Exception as e:
        logger.error(f"Error getting classification status: {str(e)}")
        return {
            'success': False,
            'message': str(e)
        }


if __name__ == "__main__":
    classify_section(5, "keep", "10.1021.acs.chemmater.0c01965")


