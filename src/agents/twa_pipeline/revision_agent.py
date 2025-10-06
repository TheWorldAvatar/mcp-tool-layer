"""
Revision Agent for Triple Comparison Analysis

This agent compares triple sets of files (previous prediction, ground truth, current prediction)
and generates comprehensive review reports using GPT-4o via setup_llm.
"""

import os
import json
import asyncio
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from models.locations import PLAYGROUND_DATA_DIR
from src.utils.global_logger import get_logger
from models.LLMCreator import LLMCreator
logger = get_logger(__name__)

class TripleComparisonReviewer:
    """Reviews triple sets of files and generates comparison reports."""
    
    def __init__(self):
        self.triple_compare_dir = os.path.join(PLAYGROUND_DATA_DIR, "triple_compare")
        self.reports_dir = os.path.join(PLAYGROUND_DATA_DIR, "reports")
        self.setup_llm = None
        self.overall_report = []
        
        # Ensure main reports directory exists
        os.makedirs(self.reports_dir, exist_ok=True)
        
        # Create subfolders for each data type
        self.data_type_subfolders = ["chemicals", "cbu", "steps", "characterisation"]
        for subfolder in self.data_type_subfolders:
            subfolder_path = os.path.join(self.reports_dir, subfolder)
            os.makedirs(subfolder_path, exist_ok=True)
            logger.info(f"📁 Created/verified reports subfolder: {subfolder}")
    
    async def setup_llm_client(self):
        """Initialize the LLM client using LLMCreator."""
        try:
            # Create LLMCreator instance and get LLM
            from models.ModelConfig import ModelConfig
            
            model_config = ModelConfig()
            llm_creator = LLMCreator(
                model="gpt-4o", 
                remote_model=True, 
                model_config=model_config,
                structured_output=False
            )
            
            self.llm = llm_creator.setup_llm()
            logger.info("✅ LLM client initialized successfully")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to setup LLM client: {e}")
            return False
    
    def get_triple_sets(self) -> List[Tuple[str, str, Dict]]:
        """Get all available triple comparison sets."""
        triple_sets = []
        
        if not os.path.exists(self.triple_compare_dir):
            logger.warning(f"Triple compare directory not found: {self.triple_compare_dir}")
            return triple_sets
        
        # Iterate through subfolders (chemicals, cbu, steps, characterisation)
        for subfolder in os.listdir(self.triple_compare_dir):
            subfolder_path = os.path.join(self.triple_compare_dir, subfolder)
            if not os.path.isdir(subfolder_path):
                continue
            
            # Find article subfolders within each type
            for article_folder in os.listdir(subfolder_path):
                article_path = os.path.join(subfolder_path, article_folder)
                if not os.path.isdir(article_path):
                    continue
                
                # Check if this is a complete triple set
                triple_files = self._get_triple_files(article_path, subfolder)
                if triple_files:
                    triple_sets.append((subfolder, article_folder, triple_files))
        
        return triple_sets
    
    def _get_triple_files(self, article_path: str, data_type: str) -> Optional[Dict[str, str]]:
        """Get the three files needed for comparison."""
        files = {}
        
        # Determine the correct suffix based on data type
        if data_type == "chemicals":
            suffix = "chemical"
        elif data_type == "cbu":
            suffix = "cbu"
        elif data_type == "steps":
            suffix = "step"  # singular
        elif data_type == "characterisation":
            suffix = "characterisation"
        else:
            return None
        
        # Look for the three required files
        current_file = os.path.join(article_path, f"{os.path.basename(article_path)}_{suffix}.json")
        ground_truth_file = os.path.join(article_path, f"{os.path.basename(article_path)}_{suffix}_ground_truth.json")
        previous_file = os.path.join(article_path, f"{os.path.basename(article_path)}_{suffix}_previous.json")
        
        if os.path.exists(current_file):
            files["current"] = current_file
        if os.path.exists(ground_truth_file):
            files["ground_truth"] = ground_truth_file
        if os.path.exists(previous_file):
            files["previous"] = previous_file
        
        # Only return if we have at least current and one other file
        if len(files) >= 2:
            return files
        return None
    
    async def review_triple_set(self, data_type: str, article_name: str, triple_files: Dict[str, str]) -> str:
        """Review a single triple set and generate a comparison report."""
        logger.info(f"🔄 Reviewing {data_type} for article: {article_name}")
        
        try:
            # Load the JSON files
            file_contents = {}
            for file_type, file_path in triple_files.items():
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        file_contents[file_type] = json.load(f)
                except Exception as e:
                    logger.warning(f"Could not load {file_type} file {file_path}: {e}")
                    file_contents[file_type] = {"error": f"Failed to load file: {str(e)}"}
            
            # Create the comparison prompt
            comparison_prompt = self._create_comparison_prompt(data_type, article_name, file_contents)
            
            # Get LLM response
            if hasattr(self, 'llm') and self.llm:
                response = await self._get_llm_response(comparison_prompt)
            else:
                response = "LLM client not available"
            
            # Generate the report
            report = self._generate_report(data_type, article_name, file_contents, response)
            
            # Create subfolder for data type and save report
            data_type_dir = os.path.join(self.reports_dir, data_type)
            os.makedirs(data_type_dir, exist_ok=True)
            
            report_filename = f"{article_name}_{data_type}_review.md"
            report_path = os.path.join(data_type_dir, report_filename)
            
            with open(report_path, 'w', encoding='utf-8') as f:
                f.write(report)
            
            logger.info(f"✅ Generated review report: {report_filename} in {data_type}/ subfolder")
            # Attempt to render PDF in reports root for easy access (best-effort)
            try:
                pdf_path = os.path.join(self.reports_dir, f"{article_name}_{data_type}_review.pdf")
                self._md_to_pdf(report_path, pdf_path)
            except Exception:
                pass
            
            # Add to overall report
            self.overall_report.append({
                "data_type": data_type,
                "article_name": article_name,
                "report_filename": f"{data_type}/{report_filename}",
                "summary": self._extract_summary(response)
            })
            
            return report_path
            
        except Exception as e:
            error_msg = f"Error reviewing {data_type} for {article_name}: {str(e)}"
            logger.error(error_msg)
            return error_msg
    
    def _create_comparison_prompt(self, data_type: str, article_name: str, file_contents: Dict) -> str:
        """Create a comprehensive prompt for the LLM comparison."""
        
        prompt = f"""
# TRIPLE COMPARISON ANALYSIS (Focused on What Went Well vs. What Went Wrong)

You are an expert reviewer. Compare both CURRENT and PREVIOUS predictions against the GROUND TRUTH for article: {article_name}.

Primary objective:
- For each of CURRENT and PREVIOUS, clearly identify:
  1) What went well (correct matches to ground truth)
  2) What went wrong (mismatches vs ground truth), with severity and fixes
  3) Overall coverage and accuracy scores
- Then compare CURRENT vs PREVIOUS to highlight improvements and regressions.

## DATA TYPE: {data_type.upper()}

## IMPORTANT ASSUMPTION
Ground truth is 100% correct. Treat it as the reference standard.

## FILES TO COMPARE

### 1) CURRENT PREDICTION
```json
{json.dumps(file_contents.get('current', {}), indent=2)}
```

### 2) GROUND TRUTH (Reference)
```json
{json.dumps(file_contents.get('ground_truth', {}), indent=2)}
```

### 3) PREVIOUS PREDICTION
```json
{json.dumps(file_contents.get('previous', {}), indent=2)}
```

## EVALUATION CRITERIA
- Coverage: % of required fields from ground truth present in the prediction
- Accuracy: correctness of values, structure, and data types compared to ground truth

## SCORING (0-10)
10: perfect; 8-9: excellent/minor issues; 6-7: adequate/noticeable issues; 4-5: poor/significant issues; 0-3: very poor.

## REQUIRED OUTPUT FORMAT

### SCORES
- Current: X/10
- Previous: Y/10

### CURRENT PREDICTION
- Coverage Score: X/10
- Accuracy Score: X/10
- What went well (bullet list):
  - JSON path: <path> — correct value '<value>' matches ground truth
  - JSON path: <path> — structure or list length matches ground truth
- What went wrong (bullet list; categorize each as Critical/Major/Minor):
  - [Severity] JSON path: <path>
    - Expected: <ground truth value>
    - Actual: <current value>
    - Why wrong: <brief reason>
    - Suggested fix: <brief fix>

### PREVIOUS PREDICTION
- Coverage Score: Y/10
- Accuracy Score: Y/10
- What went well:
  - JSON path: <path> — correct value '<value>' matches ground truth
- What went wrong (with severity, expected vs actual, reason, fix) as above

### CURRENT VS PREVIOUS
- Improvements (from previous to current):
  - JSON path: <path> — issue resolved or accuracy improved (explain)
- Regressions:
  - JSON path: <path> — new or worse issue introduced (explain)
- Net summary: did CURRENT improve overall vs PREVIOUS? In what areas?

### RECOMMENDATIONS
- Immediate fixes (prioritized list)
- Systematic improvements (process/prompt/data validations)

## EVALUATION INSTRUCTIONS
1) Always cite exact JSON paths when referencing fields (e.g., $.Synthesis[0].steps[2].Add.addedChemical[0].chemicalName)
2) Quote exact values for expected vs actual
3) Be concise but specific; prefer bullet points
4) Be constructive: each “wrong” item should have a suggested fix
"""
        return prompt

    def _md_to_pdf(self, md_path: str, pdf_path: str) -> bool:
        """Best-effort Markdown → PDF conversion using pypandoc (pandoc backend).

        Requires:
        - pip install pypandoc
        - Install pandoc (system) and optionally wkhtmltopdf or LaTeX (for pdf engines)
        """
        try:
            import pypandoc  # type: ignore
            extra_args = ["--pdf-engine=wkhtmltopdf"]
            pypandoc.convert_file(md_path, to="pdf", outputfile=pdf_path, extra_args=extra_args)
            logger.info(f"🖨️  PDF written: {pdf_path}")
            return True
        except Exception as e:
            logger.warning(f"PDF conversion skipped for {md_path}: {e}")
            return False
    
    async def _get_llm_response(self, prompt: str) -> str:
        """Get response from the LLM using the LLM instance."""
        try:
            if hasattr(self, 'llm') and self.llm:
                # Use the LLM instance to get response
                response = self.llm.invoke(prompt)
                # Extract the content from the response
                if hasattr(response, 'content'):
                    return response.content
                else:
                    return str(response)
            else:
                return "Error: LLM client not available"
        except Exception as e:
            logger.error(f"Error getting LLM response: {e}")
            return f"Error: Could not get LLM response - {str(e)}"
    
    def _generate_report(self, data_type: str, article_name: str, file_contents: Dict, llm_response: str) -> str:
        """Generate a formatted markdown report."""
        
        report = f"""# {data_type.upper()} Review Report

**Article:** {article_name}  
**Data Type:** {data_type}  
**Review Date:** {asyncio.get_event_loop().time() if asyncio.get_event_loop().is_running() else 'N/A'}

---

## Executive Summary

This report evaluates the quality of extracted {data_type} data by comparing **previous** and **current** predictions against the **ground truth** (assumed to be 100% correct).

---

## LLM Analysis

{llm_response}

---

## File Information

### Current Prediction
- **File:** {file_contents.get('current', {}).get('_file_path', 'N/A')}
- **Status:** {'Available' if 'current' in file_contents else 'Missing'}

### Ground Truth (Reference Standard)
- **File:** {file_contents.get('ground_truth', {}).get('_file_path', 'N/A')}
- **Status:** {'Available' if 'ground_truth' in file_contents else 'Missing'}

### Previous Prediction
- **File:** {file_contents.get('previous', {}).get('_file_path', 'N/A')}
- **Status:** {'Available' if 'previous' in file_contents else 'Missing'}

---

## Evaluation Context

- **Evaluation Standard**: Ground truth is considered 100% correct
- **Focus Areas**: Coverage (completeness) and Accuracy (correctness)
- **Scoring Scale**: 0-10 (10 = perfect match with ground truth)
- **Comparison Basis**: Previous vs. Current predictions against ground truth

---

## Key Insights

This analysis identifies:
1. **Coverage gaps** - what data is missing from predictions
2. **Accuracy issues** - what data is incorrect compared to ground truth  
3. **Improvement trends** - progress from previous to current predictions
4. **Actionable recommendations** - specific steps for improvement

---
*Generated by Triple Comparison Review Agent*
"""
        return report
    
    def _extract_summary(self, llm_response: str) -> str:
        """Extract a brief summary from the LLM response, focusing on scores."""
        # Look for scoring information first
        import re
        
        # Try to find scores in the response
        score_pattern = r'(\w+\s+Prediction.*?:\s*(\d+)/10)'
        scores = re.findall(score_pattern, llm_response, re.IGNORECASE)
        
        if scores:
            score_summary = " | ".join([f"{score[0]}: {score[1]}/10" for score in scores])
            return f"Scores: {score_summary}"
        
        # Fallback to first 200 characters
        summary = llm_response[:200].strip()
        if len(llm_response) > 200:
            summary += "..."
        return summary
    
    async def generate_overall_report(self) -> str:
        """Generate an overall summary report combining all individual reviews."""
        if not self.overall_report:
            return "No reviews completed yet."
        
        overall_content = f"""# Overall Triple Comparison Summary Report

**Total Reviews Completed:** {len(self.overall_report)}  
**Generated Date:** {asyncio.get_event_loop().time() if asyncio.get_event_loop().is_running() else 'N/A'}

---

## Executive Summary

This report summarizes the evaluation of **previous** and **current** predictions against **ground truth** across all {len(self.overall_report)} data extractions. Ground truth is considered the 100% correct reference standard.

---

## Review Summary

"""
        
        # Group by data type
        by_type = {}
        for review in self.overall_report:
            data_type = review["data_type"]
            if data_type not in by_type:
                by_type[data_type] = []
            by_type[data_type].append(review)
        
        # Generate summary for each data type
        for data_type, reviews in by_type.items():
            overall_content += f"### {data_type.upper()} Reviews ({len(reviews)})\n\n"
            
            for review in reviews:
                overall_content += f"- **{review['article_name']}**: {review['summary']}\n"
                overall_content += f"  - Report: [{review['report_filename']}]({review['report_filename']})\n\n"
        
        overall_content += """---

## Scoring Analysis & Recommendations

Based on the completed reviews, consider the following:

### Performance Trends:
1. **Coverage Improvements**: Track how well predictions capture all required data fields
2. **Accuracy Improvements**: Monitor reduction in errors compared to ground truth
3. **Score Distribution**: Identify patterns in 0-10 scoring across different data types

### Actionable Improvements:
1. **High-Scoring Areas**: Identify what's working well and replicate those methods
2. **Low-Scoring Areas**: Focus on systematic issues that consistently cause low scores
3. **Training Priorities**: Use score breakdowns to prioritize what to fix first

### Systematic Enhancements:
1. **Validation Pipeline**: Implement automated checks against ground truth patterns
2. **Agent Training**: Use specific error examples to improve extraction prompts
3. **Quality Metrics**: Establish baseline scores and improvement targets

---

*Generated by Triple Comparison Review Agent - Overall Summary*
"""
        
        # Save overall report
        overall_filename = "overall_triple_comparison_summary.md"
        overall_path = os.path.join(self.reports_dir, overall_filename)
        
        with open(overall_path, 'w', encoding='utf-8') as f:
            f.write(overall_content)
        
        logger.info(f"✅ Generated overall summary report: {overall_filename}")
        try:
            # Write overall PDF to reports root as well
            overall_pdf = os.path.join(self.reports_dir, "overall_triple_comparison_summary.pdf")
            self._md_to_pdf(overall_path, overall_pdf)
        except Exception:
            pass
        
        # Also save a copy in each data type subfolder for easy access
        data_types = set(review["data_type"] for review in self.overall_report)
        for data_type in data_types:
            data_type_dir = os.path.join(self.reports_dir, data_type)
            if os.path.exists(data_type_dir):
                overall_copy_path = os.path.join(data_type_dir, f"overall_{data_type}_summary.md")
                with open(overall_copy_path, 'w', encoding='utf-8') as f:
                    f.write(overall_content)
                logger.info(f"✅ Generated {data_type} summary copy: {overall_copy_path}")
        
        return overall_path
    
    async def run_single_test(self) -> bool:
        """Run comparisons for all four data types for one article (test mode)."""
        logger.info("🧪 Starting multi-type triple comparison test for a single article...")

        # Setup LLM client
        if not await self.setup_llm_client():
            logger.error("❌ Failed to setup LLM client")
            return False

        # Fixed test article as requested
        article_name = "10.1021_acs.inorgchem.4c02394"
        data_types = ["chemicals", "cbu", "steps", "characterisation"]

        any_processed = False
        for data_type in data_types:
            article_path = os.path.join(self.triple_compare_dir, data_type, article_name)
            triples = self._get_triple_files(article_path, data_type)
            if not triples:
                logger.warning(f"⚠️ Missing triple files for {data_type} - {article_name} at {article_path}")
                continue

            logger.info(f"🧪 Testing with: {data_type} - {article_name}")
            await self.review_triple_set(data_type, article_name, triples)
            any_processed = True

        if any_processed:
            logger.info("🧪 Test completed across available data types for the article")
            return True
        else:
            logger.warning("⚠️ No triple files found for any data type for the test article")
            return False
    
    async def run_complete_review(self) -> bool:
        """Run the complete triple comparison review process."""
        logger.info("🚀 Starting complete triple comparison review process...")
        
        # Setup LLM client
        if not await self.setup_llm_client():
            logger.error("❌ Failed to setup LLM client")
            return False
        
        # Get all triple sets
        triple_sets = self.get_triple_sets()
        if not triple_sets:
            logger.warning("⚠️ No triple comparison sets found")
            return False
        
        logger.info(f"📚 Found {len(triple_sets)} triple comparison sets to review")
        
        # Process each triple set
        for data_type, article_name, triple_files in triple_sets:
            logger.info(f"🔄 Processing: {data_type} - {article_name}")
            await self.review_triple_set(data_type, article_name, triple_files)
        
        # Generate overall report
        logger.info("🔄 Generating overall summary report...")
        overall_report_path = await self.generate_overall_report()
        
        logger.info(f"🎉 Triple comparison review completed! Overall report: {overall_report_path}")
        return True

async def main():
    """Main function to run the revision agent."""
    import argparse
    
    # Parse command line arguments
    parser = argparse.ArgumentParser(description="Run Triple Comparison Review Agent")
    parser.add_argument("--test", action="store_true", 
                       help="Run only one triple comparison for testing")
    args = parser.parse_args()
    
    reviewer = TripleComparisonReviewer()
    
    if args.test:
        print("🧪 TEST MODE: Running only one triple comparison...")
        success = await reviewer.run_single_test()
    else:
        print("🚀 Running complete triple comparison review...")
        success = await reviewer.run_complete_review()
    
    if success:
        print("✅ Triple comparison review completed successfully!")
        print(f"📁 Check reports in: {reviewer.reports_dir}")
    else:
        print("❌ Triple comparison review failed!")
        return 1
    
    return 0

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    exit(exit_code)
