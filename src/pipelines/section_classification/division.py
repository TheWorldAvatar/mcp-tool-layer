"""Markdown division utilities"""

import os


def divide_md_by_subsection(md_file_path: str, si_file_path: str = None) -> dict:
    """
    Read markdown file and SI file, divide them into subsections.
    
    Args:
        md_file_path: Path to main markdown file
        si_file_path: Path to SI markdown file (optional)
        
    Returns:
        Dictionary of sections with metadata
    """
    sections_dict = {}
    
    # Read main markdown file
    with open(md_file_path, 'r', encoding='utf-8') as file:
        content = file.read()
    
    # Split by ## headers (markdown level 2 headers)
    sections = content.split('## ')
    
    # Create sections dictionary for main file
    for i, section in enumerate(sections):
        if section.strip():
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

