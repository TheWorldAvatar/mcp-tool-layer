#!/usr/bin/env python3
"""
Run Revision Agent for Triple Comparison Analysis

This script runs the revision agent to analyze triple sets of files
and generate comprehensive review reports.
"""

import asyncio
import sys
from pathlib import Path

# Add the project root to the path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

async def main():
    """Run the revision agent."""
    try:
        import argparse
        from src.agents.revision_agent import TripleComparisonReviewer
        
        # Parse command line arguments
        parser = argparse.ArgumentParser(description="Run Triple Comparison Review Agent")
        parser.add_argument("--test", action="store_true", 
                           help="Run only one triple comparison for testing")
        args = parser.parse_args()
        
        print("🚀 Starting Triple Comparison Review Process...")
        if args.test:
            print("🧪 TEST MODE: Running only one triple comparison...")
        print("=" * 60)
        
        reviewer = TripleComparisonReviewer()
        
        if args.test:
            success = await reviewer.run_single_test()
        else:
            success = await reviewer.run_complete_review()
        
        if success:
            print("\n🎉 Triple comparison review completed successfully!")
            if args.test:
                print(f"🧪 Test completed - check individual report in: {reviewer.reports_dir}")
            else:
                print(f"📁 Check individual reports in: {reviewer.reports_dir}")
                print("📊 Overall summary report generated")
                print("\n📁 Reports are organized by data type:")
                print("   - chemicals/     → Chemical extraction reviews")
                print("   - cbu/          → CBU extraction reviews") 
                print("   - steps/        → Step extraction reviews")
                print("   - characterisation/ → Characterisation reviews")
        else:
            print("\n❌ Triple comparison review failed!")
            return 1
            
    except ImportError as e:
        print(f"❌ Import error: {e}")
        print("Please ensure all required modules are available.")
        return 1
    except Exception as e:
        print(f"💥 Unexpected error: {e}")
        return 1
    
    return 0

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
