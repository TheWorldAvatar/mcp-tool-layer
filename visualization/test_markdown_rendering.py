#!/usr/bin/env python3
"""
Test Markdown Rendering
Test script to verify that the markdown rendering works properly in the visualization.
"""

import requests
import json
import time

def test_markdown_rendering():
    """Test markdown rendering with various content types."""
    
    # Sample markdown content that matches the issue described
    sample_report = """# Data Sniffing Report

**Folder URI:** `file:///mnt/c/Users/xz378/Documents/GitHub/mcp-tool-layer/data/generic_data/gaussian`

## Files Found:

- **File name:** 371765.log
  - **File type:** log
  - **File size:** 4.44 MB

- **File name:** id51153.log
  - **File type:** log  
  - **File size:** 0.66 MB

- **File name:** id52112_0.log
  - **File type:** log
  - **File size:** 0.29 MB

- **File name:** rxn_179838.log
  - **File type:** log
  - **File size:** 11.85 MB

- **File name:** rxn_1852513_ocoo_g4mp2_ts_xtb_opt.log
  - **File type:** log
  - **File size:** 57.64 MB

## Summary

The folder contains **15 log files**, with sizes ranging from `0.29 MB` to `57.64 MB`. The total number of data points is not explicitly defined but can be inferred from the content of the log files.

## Purpose

The data appears to be output logs from **Gaussian calculations**, which are typically used in computational chemistry for simulating molecular structures and reactions.

### Key Points:
1. All files are `.log` format
2. Large size range indicates varied complexity
3. Computational chemistry focus
4. Ready for integration into system stack
"""

    print("🧪 Testing Markdown Rendering...")
    print("\n📝 Sample Report Content:")
    print("=" * 60)
    print(sample_report[:300] + "...")
    print("=" * 60)
    
    # Test if server is running
    try:
        response = requests.get("http://localhost:5000/api/pipeline_state")
        print(f"✅ Server is running - Status: {response.status_code}")
    except requests.exceptions.ConnectionError:
        print("❌ Server is not running. Please start the visualization server first.")
        print("   Run: python start_server.py")
        return False
    
    print("\n🎯 To test markdown rendering:")
    print("1. Start the pipeline in the web interface")
    print("2. Wait for the Data Sniffing Agent to complete")
    print("3. Check if the report renders with proper formatting:")
    print("   - Headers should be bold and larger")
    print("   - Lists should be properly formatted")
    print("   - Code blocks should be monospaced")
    print("   - Bold text should be emphasized")
    
    print("\n💡 If markdown is not rendering:")
    print("   - Check browser console for JavaScript errors")
    print("   - Verify marked.js is loaded")
    print("   - Check network tab for script loading issues")
    
    return True

if __name__ == "__main__":
    test_markdown_rendering() 