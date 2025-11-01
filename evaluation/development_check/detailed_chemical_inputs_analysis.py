#!/usr/bin/env python3
"""
Detailed analysis of chemical inputs in iter2 hint files.

Shows the actual chemical names being extracted and compares them with ground truth.
"""

import os
import json
import re
from pathlib import Path

def extract_chemical_names_from_file(file_path):
    """Extract chemical names from a hint file by finding '- Name:' patterns."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Find all lines with "- Name:" pattern
        lines = content.split('\n')
        chemical_names = []
        
        for line in lines:
            if re.search(r'-\s+Name:', line):
                # Extract the name after "Name:"
                match = re.search(r'-\s+Name:\s*(.+)', line)
                if match:
                    name = match.group(1).strip()
                    chemical_names.append(name)
        
        return chemical_names
    except Exception as e:
        print(f"Error reading {file_path}: {e}")
        return []

def get_ground_truth_chemical_names(doi):
    """Get chemical names from ground truth data for a given DOI."""
    doi_filename = doi.replace("/", "_") + ".json"
    gt_file = Path("earlier_ground_truth/chemicals1") / doi_filename
    
    if not gt_file.exists():
        return None
    
    try:
        with open(gt_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # Extract chemical names for each procedure
        procedure_chemicals = []
        for procedure in data.get("synthesisProcedures", []):
            procedure_name = procedure.get("procedureName", "")
            step_chemicals = []
            
            for step in procedure.get("steps", []):
                step_inputs = []
                for input_chem in step.get("inputChemicals", []):
                    for chem in input_chem.get("chemical", []):
                        names = chem.get("chemicalName", [])
                        if names:
                            step_inputs.extend(names)
                step_chemicals.append(step_inputs)
            
            procedure_chemicals.append({
                "procedure_name": procedure_name,
                "chemicals": step_chemicals
            })
        
        return procedure_chemicals
    except Exception as e:
        print(f"Error reading ground truth file {gt_file}: {e}")
        return None

def get_doi_from_hash(hash_value):
    """Get DOI from hash using doi_to_hash.json mapping."""
    try:
        with open("data/doi_to_hash.json", 'r') as f:
            doi_to_hash = json.load(f)
        
        for doi, h in doi_to_hash.items():
            if h == hash_value:
                return doi
        return None
    except Exception as e:
        print(f"Error reading doi_to_hash.json: {e}")
        return None

def analyze_detailed_chemical_inputs():
    """Main detailed analysis function."""
    data_dir = Path("data")
    
    if not data_dir.exists():
        print("Error: data directory not found")
        return
    
    hash_folders = [item for item in data_dir.iterdir() if item.is_dir() and not item.name.startswith('.')]
    
    print(f"Found {len(hash_folders)} hash folders to analyze")
    print("=" * 100)
    
    for hash_folder in hash_folders:
        mcp_run_path = hash_folder / "mcp_run"
        
        if not mcp_run_path.exists():
            continue
        
        doi = get_doi_from_hash(hash_folder.name)
        if not doi:
            continue
        
        print(f"\nAnalyzing {hash_folder.name} (DOI: {doi})")
        print("-" * 80)
        
        # Get ground truth data
        gt_data = get_ground_truth_chemical_names(doi)
        if gt_data is None:
            print(f"  No ground truth data found")
            continue
        
        print(f"  Ground Truth Procedures:")
        for i, proc in enumerate(gt_data):
            try:
                print(f"    {i+1}. {proc['procedure_name']}")
            except UnicodeEncodeError:
                safe_name = proc['procedure_name'].encode('ascii', 'replace').decode('ascii')
                print(f"    {i+1}. {safe_name}")
            
            for j, step_chemicals in enumerate(proc['chemicals']):
                try:
                    print(f"       Step {j+1}: {step_chemicals}")
                except UnicodeEncodeError:
                    safe_chemicals = [chem.encode('ascii', 'replace').decode('ascii') for chem in step_chemicals]
                    print(f"       Step {j+1}: {safe_chemicals}")
        
        # Find iter2 hint files
        iter2_files = list(mcp_run_path.glob("iter2_hints_*.txt"))
        
        if not iter2_files:
            print(f"  No iter2 hint files found")
            continue
        
        print(f"\n  Extracted Chemical Names:")
        for i, hint_file in enumerate(sorted(iter2_files)):
            try:
                safe_name = hint_file.name.encode('ascii', 'replace').decode('ascii')
                print(f"    {i+1}. {safe_name}")
            except UnicodeEncodeError:
                print(f"    {i+1}. {hint_file.name}")
            
            chemical_names = extract_chemical_names_from_file(hint_file)
            for j, name in enumerate(chemical_names):
                try:
                    print(f"       {j+1}. {name}")
                except UnicodeEncodeError:
                    safe_name = name.encode('ascii', 'replace').decode('ascii')
                    print(f"       {j+1}. {safe_name}")
        
        print()

if __name__ == "__main__":
    analyze_detailed_chemical_inputs()
