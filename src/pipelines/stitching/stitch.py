"""Stitching step implementation"""

import os
import sys
import json

# Add project root to path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)


def stitch_sections_to_markdown(sections_dict: dict, output_path: str) -> str:
    """
    Stitch classified sections into complete markdown file.
    
    Args:
        sections_dict: Dictionary of sections with classification
        output_path: Path to save stitched markdown
        
    Returns:
        Path to stitched markdown file
    """
    markdown_content = []
    
    # Sort sections by number
    sorted_sections = sorted(sections_dict.items(), key=lambda x: int(x[0].split()[-1]))
    
    kept_count = 0
    discarded_count = 0
    
    for section_key, section_data in sorted_sections:
        classification = section_data.get("keep_or_discard", "keep")
        if classification == "keep":
            # Add section title and content
            markdown_content.append(f"## {section_data['title']}")
            markdown_content.append("")
            if section_data.get("content"):
                markdown_content.append(section_data["content"])
            markdown_content.append("")
            kept_count += 1
        else:
            discarded_count += 1
    
    # Save stitched markdown
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(markdown_content))
    
    print(f"    ✓ Kept {kept_count} sections, discarded {discarded_count}")
    
    return output_path


def stitch_markdown(doi_hash: str, data_dir: str) -> bool:
    """
    Stitch sections for a specific DOI hash.
    
    Args:
        doi_hash: The DOI hash identifier
        data_dir: Base data directory
        
    Returns:
        True if stitching succeeded
    """
    doi_folder = os.path.join(data_dir, doi_hash)
    
    # Check for sections.json
    sections_json_path = os.path.join(doi_folder, "sections.json")
    if not os.path.exists(sections_json_path):
        print(f"  ✗ sections.json not found: {sections_json_path}")
        return False
    
    # Check if stitched file already exists
    stitched_path = os.path.join(doi_folder, f"{doi_hash}_stitched.md")
    if os.path.exists(stitched_path):
        print(f"  ⏭️  {doi_hash}_stitched.md already exists")
        return True
    
    print(f"  🔗 Stitching sections...")
    
    # Load sections
    try:
        with open(sections_json_path, 'r', encoding='utf-8') as f:
            sections_dict = json.load(f)
    except Exception as e:
        print(f"    ✗ Failed to load sections.json: {e}")
        return False
    
    # Stitch sections
    try:
        stitch_sections_to_markdown(sections_dict, stitched_path)
        print(f"    ✓ Created {doi_hash}_stitched.md")
        return True
    except Exception as e:
        print(f"    ✗ Stitching failed: {e}")
        return False


def run_step(doi_hash: str, config: dict) -> bool:
    """
    Main entry point for stitching step.
    
    Args:
        doi_hash: The DOI hash to process
        config: Pipeline configuration dictionary
        
    Returns:
        True if stitching succeeded
    """
    data_dir = config.get("data_dir", "data")
    
    print(f"▶️  Stitching: {doi_hash}")
    success = stitch_markdown(doi_hash, data_dir)
    
    if success:
        print(f"✅ Stitching completed: {doi_hash}")
    else:
        print(f"❌ Stitching failed: {doi_hash}")
    
    return success


if __name__ == "__main__":
    # Test mode
    if len(sys.argv) > 1:
        test_hash = sys.argv[1]
        test_config = {"data_dir": "data"}
        run_step(test_hash, test_config)
    else:
        print("Usage: python stitch.py <doi_hash>")

