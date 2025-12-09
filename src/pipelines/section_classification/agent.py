"""Section classification agent using MCP tools"""

import os
import sys
import json
import asyncio

# Add project root to path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from models.BaseAgent import BaseAgent
from models.ModelConfig import ModelConfig


async def classify_sections_with_agent(sections_dict: dict, doi_hash: str, sections_json_path: str) -> dict:
    """
    Use LLM agent to classify sections as keep/discard.
    Processes sections one by one, similar to the original stable implementation.
    
    Args:
        sections_dict: Dictionary of sections
        doi_hash: DOI hash identifier
        sections_json_path: Path to sections JSON file
        
    Returns:
        Updated sections dictionary with classification
    """
    # Create agent with document MCP server
    agent = BaseAgent(
        model_name="gpt-4.1",
        model_config=ModelConfig(temperature=0.2, top_p=0.02),
        remote_model=True,
        mcp_tools=["document"],
        mcp_set_name="mops_mcp.json"
    )
    
    # Process each section individually
    for section_key, section_data in sections_dict.items():
        section_index = section_key.split()[-1]  # Extract number from "Section X"
        
        # Check if section is already classified
        if isinstance(section_data, dict) and 'keep_or_discard' in section_data:
            print(f"    ⏭️  {section_key} already classified as: {section_data['keep_or_discard']}")
            continue
        
        # Get section details
        if isinstance(section_data, dict):
            title = section_data.get('title', 'Untitled')
            content = section_data.get('content', '')
            source = section_data.get('source', 'unknown')
        else:
            title = 'Untitled'
            content = str(section_data)
            source = 'unknown'
        
        # Create prompt for this section
        section_prompt = f"""Please analyze the following section from a scientific paper and decide whether to KEEP or DISCARD it.

Section Title: {title}
Source: {source}

Section Content:
{content[:2000]}{"..." if len(content) > 2000 else ""}

Decision Criteria:
- KEEP: Sections related to synthesis, characterization, properties, experimental methods, results, discussion of MOPs
- DISCARD: References, acknowledgements, author information, copyright notices, table of contents

Use the keep_or_discard_section tool to mark this section as either "keep" or "discard".

IMPORTANT: When calling the tool, use these exact parameters:
- section_index: {section_index}
- option: either "keep" or "discard"
- doi: "{doi_hash}"

Make your decision and call the tool immediately.
"""
        
        print(f"    🔍 Classifying {section_key}: {title[:50]}...")
        
        # Retry mechanism for agent execution
        max_retries = 3
        retry_delays = [5, 10, 15]  # Progressive backoff in seconds
        
        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    print(f"    🔄 Retry attempt {attempt + 1}/{max_retries} for {section_key}")
                
                # Run the agent to classify this section
                response, metadata = await agent.run(section_prompt, recursion_limit=50)
                print(f"    ✓ {section_key} classified on attempt {attempt + 1}")
                break  # Success, exit retry loop
                
            except Exception as e:
                print(f"    ✗ Error classifying {section_key} (attempt {attempt + 1}/{max_retries}): {e}")
                
                if attempt < max_retries - 1:
                    delay = retry_delays[attempt]
                    print(f"    ⏳ Waiting {delay}s before retry...")
                    await asyncio.sleep(delay)
                else:
                    print(f"    ❌ All {max_retries} attempts failed for {section_key}, skipping...")
                    continue
    
    # Reload updated sections
    with open(sections_json_path, 'r', encoding='utf-8') as f:
        updated_sections = json.load(f)
    
    return updated_sections

