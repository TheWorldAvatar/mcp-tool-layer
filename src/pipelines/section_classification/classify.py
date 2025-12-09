"""Section classification step implementation"""

import os
import sys
import json
import asyncio

# Add project root to path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from .division import divide_md_by_subsection
from .agent import classify_sections_with_agent


def save_sections_json(sections_dict: dict, output_path: str) -> str:
    """Save sections dictionary to JSON file with validation."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    # First, validate that we can serialize it
    try:
        json_str = json.dumps(sections_dict, indent=2, ensure_ascii=False)
    except Exception as e:
        raise ValueError(f"Failed to serialize sections to JSON: {e}")
    
    # Write to file
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(json_str)
    
    # Verify the file is valid JSON by reading it back
    try:
        with open(output_path, 'r', encoding='utf-8') as f:
            json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"Saved JSON file is invalid: {e}")
    
    return output_path


def classify_sections(doi_hash: str, data_dir: str) -> bool:
    """
    Classify sections for a specific DOI hash.
    
    Args:
        doi_hash: The DOI hash identifier
        data_dir: Base data directory
        
    Returns:
        True if classification succeeded
    """
    doi_folder = os.path.join(data_dir, doi_hash)
    
    # Check for markdown files
    md_path = os.path.join(doi_folder, f"{doi_hash}.md")
    si_path = os.path.join(doi_folder, f"{doi_hash}_si.md")
    
    if not os.path.exists(md_path):
        print(f"  ✗ Main markdown not found: {md_path}")
        return False
    
    # Check if sections.json already exists with classification
    sections_json_path = os.path.join(doi_folder, "sections.json")
    if os.path.exists(sections_json_path):
        # Check if classification is already done
        try:
            with open(sections_json_path, 'r', encoding='utf-8') as f:
                existing_sections = json.load(f)
        except json.JSONDecodeError as e:
            print(f"  ⚠️  sections.json is corrupted: {e}")
            print(f"  🔧 Regenerating sections.json...")
            # Delete the corrupted file and regenerate
            os.remove(sections_json_path)
            # Fall through to regeneration below
        else:
            # Check if any section has the keep_or_discard field
            has_classification = any(
                'keep_or_discard' in section_data 
                for section_data in existing_sections.values()
                if isinstance(section_data, dict)
            )
            
            if has_classification:
                print(f"  ⏭️  sections.json already exists with classification")
                return True
            else:
                print(f"  📝 sections.json exists but needs classification")
                sections_dict = existing_sections
                # Skip to classification
                print(f"  🤖 Classifying sections with LLM...")
                try:
                    updated_sections = asyncio.run(
                        classify_sections_with_agent(sections_dict, doi_hash, sections_json_path)
                    )
                    print(f"    ✓ Classification completed")
                    return True
                except Exception as e:
                    print(f"    ✗ Classification failed: {e}")
                    return False
    
    # If we reach here, we need to divide and classify from scratch
    print(f"  📄 Dividing markdown into sections...")
    
    # Divide markdown into sections
    sections_dict = divide_md_by_subsection(md_path, si_path if os.path.exists(si_path) else None)
    print(f"    ✓ Found {len(sections_dict)} sections")
    
    # Save initial sections JSON
    save_sections_json(sections_dict, sections_json_path)
    print(f"    ✓ Saved sections.json")
    
    # Classify sections using agent
    print(f"  🤖 Classifying sections with LLM...")
    try:
        updated_sections = asyncio.run(
            classify_sections_with_agent(sections_dict, doi_hash, sections_json_path)
        )
        print(f"    ✓ Classification completed")
        return True
    except Exception as e:
        print(f"    ✗ Classification failed: {e}")
        return False


def run_step(doi_hash: str, config: dict) -> bool:
    """
    Main entry point for section classification step.
    
    Args:
        doi_hash: The DOI hash to process
        config: Pipeline configuration dictionary
        
    Returns:
        True if classification succeeded
    """
    data_dir = config.get("data_dir", "data")
    
    print(f"▶️  Section Classification: {doi_hash}")
    success = classify_sections(doi_hash, data_dir)
    
    if success:
        print(f"✅ Section Classification completed: {doi_hash}")
    else:
        print(f"❌ Section Classification failed: {doi_hash}")
    
    return success


if __name__ == "__main__":
    # Test mode
    if len(sys.argv) > 1:
        test_hash = sys.argv[1]
        test_config = {"data_dir": "data"}
        run_step(test_hash, test_config)
    else:
        print("Usage: python classify.py <doi_hash>")

