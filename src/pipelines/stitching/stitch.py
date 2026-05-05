"""Stitching step implementation"""

import os
import sys
import json
import shutil

# Add project root to path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)


def _is_medical_pipeline(config: dict) -> bool:
    """Check if this is the medical pipeline based on meta_task_config."""
    meta_task_config_path = config.get("meta_task_config")
    if not meta_task_config_path:
        return False
    try:
        abs_path = os.path.join(project_root, meta_task_config_path)
        if not os.path.exists(abs_path):
            return False
        with open(abs_path, "r", encoding="utf-8") as f:
            meta_cfg = json.load(f)
        main_ontology = meta_cfg.get("ontologies", {}).get("main", {})
        ontology_name = main_ontology.get("name", "")
        return ontology_name == "medical"
    except Exception:
        return False


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

    # Fallback: never emit an empty stitched file.
    # If the classifier discarded everything, keep all sections instead.
    if kept_count == 0 and discarded_count > 0:
        markdown_content = []
        kept_count = 0
        discarded_count = 0
        for _, section_data in sorted_sections:
            markdown_content.append(f"## {section_data['title']}")
            markdown_content.append("")
            if section_data.get("content"):
                markdown_content.append(section_data["content"])
            markdown_content.append("")
            kept_count += 1
    
    # Save stitched markdown
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(markdown_content))
    
    print(f"    [OK] Kept {kept_count} sections, discarded {discarded_count}")
    
    return output_path


def stitch_markdown(doi_hash: str, data_dir: str, config: dict) -> bool:
    """
    Stitch sections for a specific DOI hash.
    
    For medical pipeline: section selection is disabled; _stitched.md is a copy of .md (all content kept).
    For other pipelines: uses sections.json keep/discard to produce _stitched.md.
    
    Args:
        doi_hash: The DOI hash identifier
        data_dir: Base data directory
        config: Pipeline configuration (used to detect medical mode)
        
    Returns:
        True if stitching succeeded
    """
    doi_folder = os.path.join(data_dir, doi_hash)
    stitched_path = os.path.join(doi_folder, f"{doi_hash}_stitched.md")
    source_md_path = os.path.join(doi_folder, f"{doi_hash}.md")
    
    # Check if stitched file already exists.
    # In medical mode: if _vision.md is newer than _stitched.md, regenerate from vision.
    if os.path.exists(stitched_path):
        try:
            if os.path.getsize(stitched_path) == 0:
                print(f"  [REGEN] {doi_hash}_stitched.md exists but is empty; regenerating")
            else:
                # In medical mode, regenerate if _vision.md is newer than _stitched.md
                if _is_medical_pipeline(config):
                    vision_md_path = os.path.join(doi_folder, f"{doi_hash}_vision.md")
                    if os.path.exists(vision_md_path):
                        vision_mtime = os.path.getmtime(vision_md_path)
                        stitch_mtime = os.path.getmtime(stitched_path)
                        if vision_mtime > stitch_mtime:
                            print(
                                f"  [REGEN] {doi_hash}_vision.md is newer than _stitched.md; regenerating"
                            )
                        else:
                            print(f"  [SKIP] {doi_hash}_stitched.md already exists (up-to-date)")
                            return True
                    else:
                        print(f"  [SKIP] {doi_hash}_stitched.md already exists")
                        return True
                else:
                    print(f"  [SKIP] {doi_hash}_stitched.md already exists")
                    return True
        except Exception:
            print(f"  [SKIP] {doi_hash}_stitched.md already exists")
            return True
    
    # Medical pipeline: no section selection; stitched = full document.
    # Prefer _vision.md (vision LLM transcription) over plain .md if available.
    if _is_medical_pipeline(config):
        vision_md_path = os.path.join(doi_folder, f"{doi_hash}_vision.md")
        if os.path.exists(vision_md_path) and os.path.getsize(vision_md_path) > 0:
            chosen = vision_md_path
            label = "_vision.md"
        elif os.path.exists(source_md_path):
            chosen = source_md_path
            label = ".md"
        else:
            print(f"  [ERROR] Medical mode: neither {doi_hash}_vision.md nor {doi_hash}.md found")
            return False

        print(f"  [MEDICAL] Section selection disabled; copying {label} to _stitched.md")
        try:
            shutil.copy2(chosen, stitched_path)
            print(f"  [OK] Created {doi_hash}_stitched.md (source: {label})")
            return True
        except Exception as e:
            print(f"  [ERROR] Copy failed: {e}")
            return False
    
    # Standard pipeline: stitch from sections.json
    sections_json_path = os.path.join(doi_folder, "sections.json")
    if not os.path.exists(sections_json_path):
        print(f"  [ERROR] sections.json not found: {sections_json_path}")
        return False
    
    print(f"  [STITCH] Stitching sections...")
    try:
        with open(sections_json_path, 'r', encoding='utf-8') as f:
            sections_dict = json.load(f)
    except Exception as e:
        print(f"  [ERROR] Failed to load sections.json: {e}")
        return False
    
    try:
        stitch_sections_to_markdown(sections_dict, stitched_path)
        print(f"  [OK] Created {doi_hash}_stitched.md")
        return True
    except Exception as e:
        print(f"  [ERROR] Stitching failed: {e}")
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
    
    print(f">> Stitching: {doi_hash}")
    success = stitch_markdown(doi_hash, data_dir, config)
    
    if success:
        print(f"[OK] Stitching completed: {doi_hash}")
    else:
        print(f"[FAIL] Stitching failed: {doi_hash}")
    
    return success


if __name__ == "__main__":
    # Test mode
    if len(sys.argv) > 1:
        test_hash = sys.argv[1]
        test_config = {"data_dir": "data"}
        run_step(test_hash, test_config)
    else:
        print("Usage: python stitch.py <doi_hash>")

