#!/usr/bin/env python3
"""
MOP Main Workflow Orchestrator

This script orchestrates the entire MOP (Metal-Organic Polyhedra) data processing workflow:
1. Section extraction and classification
2. Chemical, CBU, characterisation, and step data extraction
3. Evaluation preparation
4. Revision analysis (GPT-4o comparison)
5. Performance metrics calculation (precision, recall, F1)

Usage:
    python mop_main.py --test  # Process single test file
"""

import os
import sys
import asyncio
import argparse
import logging
from pathlib import Path
import json 
# Add the project root to the path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

# Import required modules
try:
    from src.agents.division_and_classify_agent import classify_sections_agent
    from src.agents.mops.chemical_agent import chemical_agent
    from src.agents.mops.cbu_agent import cbu_agent
    from src.agents.mops.characterisation_agent import characterisation_agent
    from src.agents.mops.step_agent import step_agent
    from playground.prepare_package import copy_and_rename_files, create_triple_comparison_structure
    from models.locations import PLAYGROUND_DATA_DIR, SANDBOX_TASK_DIR
    from src.utils.stitch_md import stitch_sections_to_markdown
    from src.agents.revision_agent import TripleComparisonReviewer
except ImportError as e:
    print(f"❌ Import error: {e}")
    print("Please ensure all required modules are available.")
    sys.exit(1)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('mop_workflow.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class MOPWorkflowOrchestrator:
    """Orchestrates the entire MOP data processing workflow."""
    
    def __init__(self, test_mode=False):
        self.test_mode = test_mode
        self.test_article = "10.1021_acs.inorgchem.4c02394"
        self.workflow_steps = []
        
        # Get all markdown files if not in test mode
        if not self.test_mode:
            self.all_articles = self._get_all_articles()
        else:
            self.all_articles = [self.test_article]
    
    def _get_all_articles(self):
        """Get all article names from markdown files in the data directory."""
        articles = set()
        for file in os.listdir(PLAYGROUND_DATA_DIR):
            if file.endswith('.md') and not file.endswith('_si.md'):
                # Remove .md extension to get article name
                article_name = file[:-3]
                articles.add(article_name)
        return sorted(list(articles))
        
    def log_step(self, step_name: str, status: str, details: str = ""):
        """Log a workflow step with status and details."""
        step_info = {
            "step": step_name,
            "status": status,
            "details": details,
            "timestamp": asyncio.get_event_loop().time() if asyncio.get_event_loop().is_running() else 0
        }
        self.workflow_steps.append(step_info)
        
        if status == "SUCCESS":
            logger.info(f"✅ {step_name}: {details}")
        elif status == "FAILED":
            logger.error(f"❌ {step_name}: {details}")
        elif status == "SKIPPED":
            logger.warning(f"⚠️  {step_name}: {details}")
        else:
            logger.info(f"🔄 {step_name}: {details}")
    
    async def step_1_extract_and_classify(self) -> bool:
        """Step 1: Extract and classify sections from markdown files."""
        try:
            logger.info("🔄 Starting Step 1: Section extraction and classification...")
            
            # Check if input files exist
            main_md = os.path.join(PLAYGROUND_DATA_DIR, f"{self.current_article}.md")
            si_md = os.path.join(PLAYGROUND_DATA_DIR, f"{self.current_article}_si.md")
            
            if not os.path.exists(main_md):
                self.log_step("Section Extraction", "FAILED", f"Main markdown file not found: {main_md}")
                return False
            
            # Run the section classification agent
            result = await classify_sections_agent(self.current_article)
            
            if result:
                self.log_step("Section Extraction", "SUCCESS", f"Processed {self.current_article}")
                
                # Now stitch the sections back together into complete markdown
                logger.info("🔄 Stitching sections into complete markdown...")
                try:
                    # load the json file written instead of directly using result
                    json_file = os.path.join(SANDBOX_TASK_DIR, self.current_article, "sections.json")
                    with open(json_file, "r") as f:
                        sections_dict = json.load(f)
                    complete_md_file = stitch_sections_to_markdown(sections_dict, self.current_article)
                    
                    if complete_md_file and os.path.exists(complete_md_file):
                        self.log_step("Section Stitching", "SUCCESS", f"Created complete markdown: {complete_md_file}")
                        return True
                    else:
                        self.log_step("Section Stitching", "FAILED", f"Failed to create complete markdown")
                        return False
                        
                except Exception as e:
                    self.log_step("Section Stitching", "FAILED", f"Error stitching sections: {str(e)}")
                    logger.exception("Error in section stitching")
                    return False
            else:
                self.log_step("Section Extraction", "FAILED", f"Failed to process {self.current_article}")
                return False
                
        except Exception as e:
            self.log_step("Section Extraction", "FAILED", f"Error: {str(e)}")
            logger.exception("Error in section extraction")
            return False
    
    async def step_2_extract_chemical_data(self) -> bool:
        """Step 2: Extract chemical synthesis, CBU, characterisation, and step data."""
        try:
            logger.info("🔄 Starting Step 2: Chemical, CBU, characterisation, and step data extraction...")
            
            # Check if sections.json exists
            sections_file = os.path.join(SANDBOX_TASK_DIR, self.current_article, "sections.json")
            if not os.path.exists(sections_file):
                self.log_step("Data Extraction", "SKIPPED", "sections.json not found, skipping data extraction")
                return True
            
            # Check if complete.md exists (needed for chemical agent)
            complete_md_path = os.path.join(SANDBOX_TASK_DIR, self.current_article, f"{self.current_article}_complete.md")
            if not os.path.exists(complete_md_path):
                self.log_step("Data Extraction", "SKIPPED", f"Complete markdown file not found: {complete_md_path}")
                return True
            
            # Read the complete markdown content for chemical agent
            try:
                with open(complete_md_path, 'r', encoding='utf-8') as f:
                    paper_content = f.read()
            except Exception as e:
                self.log_step("Data Extraction", "FAILED", f"Error reading complete markdown: {str(e)}")
                return False
            
            # Run chemical agent (requires paper_content)
            chemical_result = await chemical_agent(self.current_article, paper_content, test_mode=False)
            
            # Run CBU agent (reads file internally)
            cbu_result = await cbu_agent(self.current_article, test_mode=False)
            
            # Run characterisation agent (reads file internally)
            characterisation_result = await characterisation_agent(self.current_article, test_mode=False)
            
            # Run step agent (reads file internally)
            step_result = await step_agent(self.current_article, test_mode=False)
            
            if chemical_result and cbu_result and characterisation_result and step_result:
                self.log_step("Data Extraction", "SUCCESS", f"Extracted chemical, CBU, characterisation, and step data for {self.current_article}")
                return True
            else:
                self.log_step("Data Extraction", "FAILED", f"Failed to extract data for {self.current_article}")
                return False
                
        except Exception as e:
            self.log_step("Data Extraction", "FAILED", f"Error: {str(e)}")
            logger.exception("Error in data extraction")
            return False
    
    async def step_3_prepare_evaluation(self) -> bool:
        """Step 3: Prepare files for evaluation."""
        try:
            logger.info("🔄 Starting Step 3: Evaluation preparation...")
            
            # Create triple comparison structure
            create_triple_comparison_structure()
            
            # Copy and rename files for evaluation
            copy_and_rename_files()
            
            self.log_step("Evaluation Preparation", "SUCCESS", f"Prepared evaluation files for all articles")
            return True
            
        except Exception as e:
            self.log_step("Evaluation Preparation", "FAILED", f"Error: {str(e)}")
            logger.exception("Error in evaluation preparation")
            return False
    
    async def step_4_revision_analysis(self) -> bool:
        """Step 4: Run revision analysis using GPT-4o to compare predictions."""
        try:
            logger.info("🔄 Starting Step 4: Revision analysis...")
            
            # Initialize the revision agent
            reviewer = TripleComparisonReviewer()
            
            # Run the complete revision analysis
            if self.test_mode:
                logger.info("🧪 Running revision analysis in TEST MODE (single triple)")
                success = await reviewer.run_single_test()
            else:
                logger.info("🚀 Running complete revision analysis for all triples")
                success = await reviewer.run_complete_review()
            
            if success:
                self.log_step("Revision Analysis", "SUCCESS", f"Completed revision analysis for all triples")
                logger.info(f"📁 Revision reports generated in: {reviewer.reports_dir}")
                return True
            else:
                self.log_step("Revision Analysis", "FAILED", "Revision analysis failed")
                return False
                
        except Exception as e:
            self.log_step("Revision Analysis", "FAILED", f"Error: {str(e)}")
            logger.exception("Error in revision analysis")
            return False
    
    async def step_5_performance_metrics(self) -> bool:
        """Step 5: Calculate performance metrics (precision, recall, F1) for all data types."""
        try:
            logger.info("🔄 Starting Step 5: Performance metrics calculation...")
            
            # Import the performance metrics script
            try:
                from scripts.calc_mop_performance_metrics import main as calc_performance_metrics
            except ImportError as e:
                self.log_step("Performance Metrics", "FAILED", f"Import error: {str(e)}")
                logger.exception("Error importing performance metrics script")
                return False
            
            # Run the performance metrics calculation
            try:
                calc_performance_metrics()
                self.log_step("Performance Metrics", "SUCCESS", "Completed performance metrics calculation for all data types")
                logger.info("📊 Performance metrics reports generated in playground/data/reports")
                return True
            except Exception as e:
                self.log_step("Performance Metrics", "FAILED", f"Error running performance metrics: {str(e)}")
                logger.exception("Error in performance metrics calculation")
                return False
                
        except Exception as e:
            self.log_step("Performance Metrics", "FAILED", f"Error: {str(e)}")
            logger.exception("Error in performance metrics step")
            return False
    
    async def run_workflow(self) -> bool:
        """Run the complete MOP workflow."""
        if self.test_mode:
            logger.info(f"🧪 Starting MOP workflow in TEST MODE for article: {self.test_article}")
        else:
            logger.info(f"🚀 Starting MOP workflow for ALL articles: {len(self.all_articles)} files")
            logger.info(f"📚 Articles to process: {', '.join(self.all_articles)}")
        
        logger.info("=" * 60)
        
        overall_success = True
        
        for i, article in enumerate(self.all_articles, 1):
            logger.info(f"📖 Processing article {i}/{len(self.all_articles)}: {article}")
            logger.info("-" * 40)
            
            # Update the current article for processing
            self.current_article = article
            
            # Step 1: Extract and classify sections
            if not await self.step_1_extract_and_classify():
                logger.error(f"❌ Workflow failed for {article} at Step 1")
                overall_success = False
                continue
            
            # Step 2: Extract chemical, CBU, characterisation, and step data
            if not await self.step_2_extract_chemical_data():
                logger.error(f"❌ Workflow failed for {article} at Step 2")
                overall_success = False
                continue
            
            logger.info(f"✅ Completed processing for article: {article}")
        
        # Step 3: Prepare evaluation (only once at the end)
        if overall_success:
            logger.info("🔄 Starting final evaluation preparation...")
            if not await self.step_3_prepare_evaluation():
                logger.error("❌ Evaluation preparation failed")
                overall_success = False
        
        # Step 4: Revision analysis (only once at the end)
        if overall_success:
            logger.info("🔄 Starting revision analysis...")
            if not await self.step_4_revision_analysis():
                logger.error("❌ Revision analysis failed")
                overall_success = False
        
        # Step 5: Performance metrics calculation (only once at the end)
        # This calculates TP, FP, FN, precision, recall, and F1 scores for all data types
        if overall_success:
            logger.info("🔄 Starting performance metrics calculation...")
            if not await self.step_5_performance_metrics():
                logger.error("❌ Performance metrics calculation failed")
                overall_success = False
        
        if overall_success:
            logger.info("🎉 MOP workflow completed successfully for all articles!")
            self.log_workflow_summary()
        else:
            logger.error("💥 MOP workflow failed for some articles!")
        
        return overall_success
    
    def log_workflow_summary(self):
        """Log a summary of the workflow execution."""
        logger.info("=" * 60)
        logger.info("📊 WORKFLOW SUMMARY")
        logger.info("=" * 60)
        
        for i, step in enumerate(self.workflow_steps, 1):
            status_emoji = {
                "SUCCESS": "✅",
                "FAILED": "❌", 
                "SKIPPED": "⚠️",
                "RUNNING": "🔄"
            }.get(step["status"], "❓")
            
            logger.info(f"{i}. {status_emoji} {step['step']}: {step['details']}")
        
        logger.info("=" * 60)
        
        # Count successes and failures
        successes = sum(1 for step in self.workflow_steps if step["status"] == "SUCCESS")
        failures = sum(1 for step in self.workflow_steps if step["status"] == "FAILED")
        skipped = sum(1 for step in self.workflow_steps if step["status"] == "SKIPPED")
        
        logger.info(f"📈 Results: {successes} successful, {failures} failed, {skipped} skipped")
        
        if failures == 0:
            logger.info("🎯 All critical steps completed successfully!")
        else:
            logger.warning(f"⚠️  {failures} step(s) failed - check logs for details")

def main():
    """Main entry point for the MOP workflow."""
    parser = argparse.ArgumentParser(
        description='MOP Main Workflow Orchestrator',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python mop_main.py           # Process all files with full revision analysis
  python mop_main.py --test    # Process single test file with test revision analysis
        """
    )
    
    parser.add_argument(
        '--test', 
        action='store_true',
        help='Run workflow on single test file (10.1021_acs.inorgchem.4c02394). Without this flag, processes all files.'
    )
    
    args = parser.parse_args()
    
    # No arguments required - --test is optional
    
    # Initialize and run the workflow
    orchestrator = MOPWorkflowOrchestrator(test_mode=args.test)
    
    try:
        # Run the workflow
        success = asyncio.run(orchestrator.run_workflow())
        
        if success:
            print("\n🎉 MOP workflow completed successfully!")
            print("Check the logs for detailed information.")
            sys.exit(0)
        else:
            print("\n💥 MOP workflow failed!")
            print("Check the logs for error details.")
            sys.exit(1)
            
    except KeyboardInterrupt:
        print("\n⏹️  Workflow interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n💥 Unexpected error: {e}")
        logger.exception("Unexpected error in main workflow")
        sys.exit(1)

if __name__ == "__main__":
    main()
