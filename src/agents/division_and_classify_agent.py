"""
Read both manuscript and supporting information markdown files, section by section, decide whether to keep them or not. 

Stitch the sections together to form a complete manuscript. 
"""

import os
import json
import asyncio
from models.BaseAgent import BaseAgent
from models.ModelConfig import ModelConfig
from models.locations import SANDBOX_TASK_DIR, PLAYGROUND_DATA_DIR
from src.utils.global_logger import get_logger
from src.utils.stitch_md import stitch_sections_to_markdown

logger = get_logger("agent", "DivisionAndClassifyAgent")

def divide_md_by_subsection(md_file_path: str, si_file_path: str = None):
    """
    Read the markdown file and SI file, divide them into subsections, and return a JSON object.
    """
    sections_dict = {}
    
    # Read main markdown file
    with open(md_file_path, 'r', encoding='utf-8') as file:
        content = file.read()
    
    # Split by ## headers (markdown level 2 headers)
    sections = content.split('## ')
    
    # Create sections dictionary for main file, skipping the first empty section if it exists
    for i, section in enumerate(sections):
        if section.strip():
            # Extract the section title (first line) and content
            lines = section.strip().split('\n')
            if lines:
                title = lines[0].strip()
                content = '\n'.join(lines[1:]).strip()
                sections_dict[f"Section {i}"] = {
                    "title": title,
                    "content": content,
                    "source": "main"
                }
    
    # Read SI file if it exists
    if si_file_path and os.path.exists(si_file_path):
        with open(si_file_path, 'r', encoding='utf-8') as file:
            si_content = file.read()
        
        # Split SI by ## headers
        si_sections = si_content.split('## ')
        
        # Add SI sections to the dictionary
        si_start_index = len(sections_dict)
        for i, section in enumerate(si_sections):
            if section.strip():
                lines = section.strip().split('\n')
                if lines:
                    title = lines[0].strip()
                    content = '\n'.join(lines[1:]).strip()
                    sections_dict[f"Section {si_start_index + i}"] = {
                        "title": title,
                        "content": content,
                        "source": "si"
                    }
    
    return sections_dict

def save_sections_json(sections_dict: dict, task_name: str, output_dir: str = None):
    """
    Save the sections dictionary to a JSON file in the specified output directory.
    Creates subfolder named after the task_name if output_dir is provided.
    """
    if output_dir is None:
        output_dir = SANDBOX_TASK_DIR
    
    # Create subfolder for this task
    task_output_dir = os.path.join(output_dir, task_name)
    os.makedirs(task_output_dir, exist_ok=True)
    
    # Save the sections JSON file
    output_file = os.path.join(task_output_dir, "sections.json")
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(sections_dict, f, indent=2, ensure_ascii=False)
    
    logger.info(f"Saved sections JSON to: {output_file}")
    return output_file



async def classify_sections_agent(task_name: str, md_file_path: str = None):
    """
    Main agent function that:
    1. Divides markdown into sections and saves JSON
    2. Iterates through sections to classify as keep/discard
    3. Uses MCP tool to update the JSON file
    4. Stitches sections back together into complete markdown
    """
    logger.info(f"Starting section classification for task: {task_name}")
    
    # If no md_file_path provided, construct it from task_name
    if md_file_path is None:
        md_file_path = os.path.join(PLAYGROUND_DATA_DIR, f"{task_name}.md")
    
    # Construct SI file path
    si_file_path = os.path.join(PLAYGROUND_DATA_DIR, f"{task_name}_si.md")
    
    # Check if markdown file exists
    if not os.path.exists(md_file_path):
        logger.error(f"Markdown file not found: {md_file_path}")
        return None
    
    # Step 1: Divide markdown into sections and save JSON
    logger.info("Step 1: Dividing markdown into sections...")
    sections_dict = divide_md_by_subsection(md_file_path, si_file_path)
    logger.info(f"Found {len(sections_dict)} sections (main + SI)")
    
    # Save the initial sections JSON
    output_file = save_sections_json(sections_dict, task_name)
    
    # Step 2: Initialize the agent with MCP tools
    model_config = ModelConfig(temperature=0.2, top_p=0.02)
    mcp_tools = ["document"]
    agent = BaseAgent(
        model_name="gpt-4.1", 
        model_config=model_config, 
        remote_model=True, 
        mcp_tools=mcp_tools, 
        mcp_set_name="mops_mcp.json"
    )
    
    # Step 3: Iterate through sections for classification
    logger.info("Step 3: Classifying sections...")
    for section_key, section_data in sections_dict.items():
        section_index = section_key.split()[-1]  # Extract number from "Section X"
        
        # Create prompt for this section
        section_prompt = f"""
        Please analyze the following section from a scientific paper and decide whether to KEEP or DISCARD it.

        Section Title: {section_data['title']}
        Source: {section_data['source']}
        
        Section Content:
        {section_data['content']}
        
        Decision Criteria:
        - KEEP: Sections related to synthesis, characterization, properties, experimental methods, results, discussion of MOPs
        - DISCARD: References, acknowledgements, author information, or other non-scientific content
        
        Use the keep_or_discard_section tool to mark this section as either "keep" or "discard".
        
        IMPORTANT: When calling the tool, use these exact parameters:
        - file_path: "sections.json"
        - section_index: {section_index}
        - option: either "keep" or "discard"
        - task_name: "{task_name}"
        
        Make your decision and call the tool immediately.
        """
        
        logger.info(f"Classifying {section_key}: {section_data['title'][:50]}...")
        
        try:
            # Run the agent to classify this section
            response, metadata = await agent.run(section_prompt, recursion_limit=50)
            logger.info(f"Classification result for {section_key}: {response}")
            
        except Exception as e:
            logger.error(f"Error classifying {section_key}: {e}")
            continue

    return sections_dict    
    
    # # Step 4: Stitch sections back together into complete markdown
    # logger.info("Step 4: Stitching sections into complete markdown...")
    # complete_md_file = stitch_sections_to_markdown(sections_dict, task_name)
    
    # logger.info(f"Section classification completed for task: {task_name}")
    # logger.info(f"Output files: {output_file} and {complete_md_file}")
    # return output_file

if __name__ == "__main__":
    # Only keep --test argument, which runs through single file
    import argparse
    
    parser = argparse.ArgumentParser(description='Section Classification Agent')
    parser.add_argument('--test', action='store_true', help='Test with a single file')
    
    args = parser.parse_args()
    
    if args.test:
        # Test mode: process a single test file
        test_task = "10.1021.acs.chemmater.0c01965"
        print(f"Running in test mode with: {test_task}")
        asyncio.run(classify_sections_agent(test_task))
    else:
        # Go through all the files in PLAYGROUND_DATA_DIR and process each one
        for file in os.listdir(PLAYGROUND_DATA_DIR):
            file_path = os.path.join(PLAYGROUND_DATA_DIR, file)
            if file.endswith('.md') and "_si" not in file:
                print(f"Processing PDF file: {file}")
                asyncio.run(classify_sections_agent(file.replace('.md', '')))

 