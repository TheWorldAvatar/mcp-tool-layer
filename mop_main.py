"""
MOPs Extraction Pipeline

Default run will process all DOI folders in the data directory.
--test mode will only run the test DOI and complete the full pipeline.
--file <doi> mode will only run the specified DOI and complete the full pipeline.

# Full Pipeline

1. Conversion from PDF formats to markdown formats
   - input: data/<doi>/<doi>.pdf + data/<doi>/<doi>_si.pdf  
   - output: data/<doi>/<doi>.md + data/<doi>/<doi>_si.md
   - Script used: scripts/pdf_to_markdown.py

2. Division and classification of markdown sections
   - input: data/<doi>/<doi>.md + data/<doi>/<doi>_si.md
   - output: data/<doi>/sections.json + data/<doi>/<doi>_stitched.md
   - Agent: src/agents/division_and_classify_agent.py + MCP server: src/mcp_servers/document/main.py

3. [Additional pipeline steps to be added...]
"""

import argparse
import sys
from src.utils.pipeline import run_pipeline_for_doi, run_pipeline_for_all

TEST_FILE_DOI = "10.1021.acs.chemmater.0c01965"

def main():
    """Main function with command line argument handling."""
    parser = argparse.ArgumentParser(description='MOPs Extraction Pipeline')
    parser.add_argument('--test', action='store_true', 
                       help='Run pipeline for test DOI only')
    parser.add_argument('--file', type=str, 
                       help='Run pipeline for specific DOI')
    
    args = parser.parse_args()
    
    if args.test:
        print(f"Running in test mode with DOI: {TEST_FILE_DOI}")
        success = run_pipeline_for_doi(TEST_FILE_DOI)
    elif args.file:
        print(f"Running pipeline for specified DOI: {args.file}")
        success = run_pipeline_for_doi(args.file)
    else:
        print("Running pipeline for all DOIs")
        success = run_pipeline_for_all()
    
    if success:
        print("\n🎉 Pipeline execution completed successfully!")
        sys.exit(0)
    else:
        print("\n❌ Pipeline execution failed!")
        sys.exit(1)

if __name__ == "__main__":
    main()



