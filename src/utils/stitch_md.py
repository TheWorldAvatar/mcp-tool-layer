import os
import json
from models.locations import SANDBOX_TASK_DIR
from src.utils.global_logger import get_logger

logger = get_logger("utils", "stitch_md")   


def stitch_sections_to_markdown(sections_dict: dict, task_name: str, output_dir: str = None):
    """
    Stitch the classified sections back together into a complete markdown file.
    Only includes sections marked as "keep".
    """
    if output_dir is None:
        output_dir = SANDBOX_TASK_DIR
    
    # Create subfolder for this task
    task_output_dir = os.path.join(output_dir, task_name)
    os.makedirs(task_output_dir, exist_ok=True)
    
    # Create the complete markdown content
    markdown_content = []
    
    # Add header
    markdown_content.append(f"# {task_name}")
    markdown_content.append("")
    
    logger.info(f"Stitching {len(sections_dict)} sections into complete markdown...")
    
    # Process sections in order, only keeping those marked as "keep"
    # Sort by section number (extract number from "Section X")
    sorted_sections = sorted(sections_dict.items(), key=lambda x: int(x[0].split()[-1]))
    
    for section_key, section_data in sorted_sections:
        classification = section_data.get("keep_or_discard", "keep")
        if classification == "keep":
            # Add section title and content
            markdown_content.append(f"## {section_data['title']}")
            markdown_content.append("")
            if section_data["content"]:
                markdown_content.append(section_data["content"])
            markdown_content.append("")
            logger.info(f"  - ✅ Added {section_key} to markdown")
        else:
            logger.info(f"  - ❌ Discarded {section_key}")
    
    # Save the complete markdown file
    output_file = os.path.join(task_output_dir, f"{task_name}_complete.md")
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write('\n'.join(markdown_content))
    
    logger.info(f"Saved complete markdown to: {output_file}")
    
    return output_file

if __name__ == "__main__":
    sections_dict = json.load(open("sandbox/tasks/10.1021_acs.inorgchem.4c02394/sections.json", "r"))
    stitch_sections_to_markdown(sections_dict, "10.1021_acs.inorgchem.4c02394")