#!/usr/bin/env python3
"""
Calculates performance metrics (precision, recall, F1) for MOP data extraction
by comparing current predictions against ground truth.

This script analyzes four data types:
- Chemicals (synthesis procedures)
- CBU (Chemical Building Units)
- Characterisation (analytical data)
- Steps (synthesis step procedures)

Outputs detailed reports to the playground/data/reports folder.
"""

import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Tuple, Set, Any
import pandas as pd
from datetime import datetime

# Add the project root to the path for imports
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from models.locations import PLAYGROUND_DATA_DIR, SANDBOX_TASK_DIR

# Define paths
DATA_DIR = PLAYGROUND_DATA_DIR
TRIPLE_COMPARE_DIR = os.path.join(DATA_DIR, "triple_compare")
REPORTS_DIR = os.path.join(DATA_DIR, "reports")

# Data types to analyze
DATA_TYPES = ["chemicals", "cbu", "characterisation", "steps"]

def flatten_json(obj: Any, parent_key: str = '') -> List[Tuple[str, Any]]:
    """
    Flatten a nested JSON object into a list of (key, value) pairs.
    
    Args:
        obj: The JSON object to flatten
        parent_key: The parent key for nested objects
        
    Returns:
        List of (key, value) tuples representing the flattened structure
    """
    items = []
    
    if isinstance(obj, dict):
        for k, v in obj.items():
            new_key = f"{parent_key}.{k}" if parent_key else k
            items.extend(flatten_json(v, new_key))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if isinstance(v, dict):
                new_key = f"{parent_key}[{i}]"
                items.extend(flatten_json(v, new_key))
            else:
                items.append((parent_key, v))
    else:
        items.append((parent_key, obj))
    
    return items

def compute_counts(ref_pairs: Set[Tuple[str, Any]], pred_pairs: Set[Tuple[str, Any]]) -> Tuple[int, int, int]:
    """
    Compute true positives, false positives, and false negatives.
    
    Args:
        ref_pairs: Set of reference (ground truth) pairs
        pred_pairs: Set of predicted pairs
        
    Returns:
        Tuple of (true_positives, false_positives, false_negatives)
    """
    tp = len(ref_pairs & pred_pairs)
    fp = len(pred_pairs - ref_pairs)
    fn = len(ref_pairs - pred_pairs)
    return tp, fp, fn

def calculate_metrics(tp: int, fp: int, fn: int) -> Tuple[float, float, float]:
    """
    Calculate precision, recall, and F1 score.
    
    Args:
        tp: True positives
        fp: False positives
        fn: False negatives
        
    Returns:
        Tuple of (precision, recall, f1)
    """
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = (2 * precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0
    
    return precision, recall, f1

def get_triple_comparison_files() -> Dict[str, Dict[str, List[str]]]:
    """
    Get all triple comparison files organized by data type and article.
    
    Returns:
        Dictionary mapping data types to articles to file lists
    """
    triple_files = {}
    
    if not os.path.exists(TRIPLE_COMPARE_DIR):
        print(f"❌ Triple comparison directory not found: {TRIPLE_COMPARE_DIR}")
        return triple_files
    
    for data_type in DATA_TYPES:
        data_type_dir = os.path.join(TRIPLE_COMPARE_DIR, data_type)
        if not os.path.exists(data_type_dir):
            continue
            
        triple_files[data_type] = {}
        
        for article_dir in os.listdir(data_type_dir):
            article_path = os.path.join(data_type_dir, article_dir)
            if os.path.isdir(article_path):
                files = []
                for file in os.listdir(article_path):
                    if file.endswith('.json'):
                        files.append(file)
                
                if files:
                    triple_files[data_type][article_dir] = files
    
    return triple_files

def analyze_article_performance(article: str, data_type: str, files: List[str]) -> Dict[str, Any]:
    """
    Analyze performance for a specific article and data type.
    
    Args:
        article: Article identifier
        data_type: Type of data being analyzed
        files: List of JSON files for this article/data type
        
    Returns:
        Dictionary containing performance metrics and analysis
    """
    article_dir = os.path.join(TRIPLE_COMPARE_DIR, data_type, article)
    
    # Find the three required files
    current_file = None
    ground_truth_file = None
    previous_file = None
    
    for file in files:
        if file.endswith(f'_{data_type.rstrip("s")}.json'):
            current_file = file
        elif file.endswith('_ground_truth.json'):
            ground_truth_file = file
        elif file.endswith('_previous.json'):
            previous_file = file
    
    if not current_file or not ground_truth_file:
        return {
            "article": article,
            "data_type": data_type,
            "status": "missing_files",
            "current_file": current_file,
            "ground_truth_file": ground_truth_file,
            "previous_file": previous_file
        }
    
    try:
        # Load current prediction
        current_path = os.path.join(article_dir, current_file)
        with open(current_path, 'r', encoding='utf-8') as f:
            current_data = json.load(f)
        
        # Load ground truth
        gt_path = os.path.join(article_dir, ground_truth_file)
        with open(gt_path, 'r', encoding='utf-8') as f:
            gt_data = json.load(f)
        
        # Load previous prediction if available
        previous_data = None
        if previous_file:
            prev_path = os.path.join(article_dir, previous_file)
            with open(prev_path, 'r', encoding='utf-8') as f:
                previous_data = json.load(f)
        
        # Flatten JSON structures for comparison
        current_pairs = set(flatten_json(current_data))
        gt_pairs = set(flatten_json(gt_data))
        previous_pairs = set(flatten_json(previous_data)) if previous_data else set()
        
        # Calculate metrics for current vs ground truth
        tp_current, fp_current, fn_current = compute_counts(gt_pairs, current_pairs)
        precision_current, recall_current, f1_current = calculate_metrics(tp_current, fp_current, fn_current)
        
        # Calculate metrics for previous vs ground truth (if available)
        if previous_data:
            tp_previous, fp_previous, fn_previous = compute_counts(gt_pairs, previous_pairs)
            precision_previous, recall_previous, f1_previous = calculate_metrics(tp_previous, fp_previous, fn_previous)
        else:
            tp_previous = fp_previous = fn_previous = 0
            precision_previous = recall_previous = f1_previous = 0.0
        
        return {
            "article": article,
            "data_type": data_type,
            "status": "success",
            "current_metrics": {
                "tp": tp_current,
                "fp": fp_current,
                "fn": fn_current,
                "precision": precision_current,
                "recall": recall_current,
                "f1": f1_current
            },
            "previous_metrics": {
                "tp": tp_previous,
                "fp": fp_previous,
                "fn": fn_previous,
                "precision": precision_previous,
                "recall": recall_previous,
                "f1": f1_previous
            },
            "improvement": {
                "precision": precision_current - precision_previous,
                "recall": recall_current - recall_previous,
                "f1": f1_current - f1_previous
            }
        }
        
    except Exception as e:
        return {
            "article": article,
            "data_type": data_type,
            "status": "error",
            "error": str(e)
        }

def generate_performance_report(triple_files: Dict[str, Dict[str, List[str]]]) -> Dict[str, Any]:
    """
    Generate comprehensive performance report for all data types and articles.
    
    Args:
        triple_files: Dictionary of triple comparison files
        
    Returns:
        Dictionary containing the complete performance report
    """
    report = {
        "timestamp": datetime.now().isoformat(),
        "summary": {},
        "detailed_results": {},
        "data_type_summaries": {}
    }
    
    total_articles = 0
    total_successful = 0
    
    for data_type in DATA_TYPES:
        if data_type not in triple_files:
            continue
            
        data_type_results = []
        data_type_summary = {
            "total_articles": 0,
            "successful_analyses": 0,
            "avg_precision": 0.0,
            "avg_recall": 0.0,
            "avg_f1": 0.0,
            "total_tp": 0,
            "total_fp": 0,
            "total_fn": 0
        }
        
        for article, files in triple_files[data_type].items():
            result = analyze_article_performance(article, data_type, files)
            data_type_results.append(result)
            
            if result["status"] == "success":
                data_type_summary["successful_analyses"] += 1
                current_metrics = result["current_metrics"]
                data_type_summary["total_tp"] += current_metrics["tp"]
                data_type_summary["total_fp"] += current_metrics["fp"]
                data_type_summary["total_fn"] += current_metrics["fn"]
                data_type_summary["avg_precision"] += current_metrics["precision"]
                data_type_summary["avg_recall"] += current_metrics["recall"]
                data_type_summary["avg_f1"] += current_metrics["f1"]
            
            data_type_summary["total_articles"] += 1
            total_articles += 1
        
        # Calculate averages
        if data_type_summary["successful_analyses"] > 0:
            data_type_summary["avg_precision"] /= data_type_summary["successful_analyses"]
            data_type_summary["avg_recall"] /= data_type_summary["successful_analyses"]
            data_type_summary["avg_f1"] /= data_type_summary["successful_analyses"]
            total_successful += data_type_summary["successful_analyses"]
        
        report["detailed_results"][data_type] = data_type_results
        report["data_type_summaries"][data_type] = data_type_summary
    
    # Overall summary
    report["summary"] = {
        "total_articles": total_articles,
        "successful_analyses": total_successful,
        "success_rate": (total_successful / total_articles * 100) if total_articles > 0 else 0.0
    }
    
    return report

def save_reports(report: Dict[str, Any]) -> None:
    """
    Save performance reports to the reports directory.
    
    Args:
        report: The performance report to save
    """
    os.makedirs(REPORTS_DIR, exist_ok=True)
    
    # Save comprehensive JSON report
    json_report_path = os.path.join(REPORTS_DIR, "performance_metrics_comprehensive.json")
    with open(json_report_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"📊 Comprehensive report saved: {json_report_path}")
    
    # Generate CSV summary for easy analysis
    csv_data = []
    for data_type, results in report["detailed_results"].items():
        for result in results:
            if result["status"] == "success":
                row = {
                    "data_type": data_type,
                    "article": result["article"],
                    "tp": result["current_metrics"]["tp"],
                    "fp": result["current_metrics"]["fp"],
                    "fn": result["current_metrics"]["fn"],
                    "precision": result["current_metrics"]["precision"],
                    "recall": result["current_metrics"]["recall"],
                    "f1": result["current_metrics"]["f1"]
                }
                
                if "previous_metrics" in result and result["previous_metrics"]["tp"] > 0:
                    row.update({
                        "prev_precision": result["previous_metrics"]["precision"],
                        "prev_recall": result["previous_metrics"]["recall"],
                        "prev_f1": result["previous_metrics"]["f1"],
                        "precision_improvement": result["improvement"]["precision"],
                        "recall_improvement": result["improvement"]["recall"],
                        "f1_improvement": result["improvement"]["f1"]
                    })
                
                csv_data.append(row)
    
    if csv_data:
        df = pd.DataFrame(csv_data)
        csv_report_path = os.path.join(REPORTS_DIR, "performance_metrics_summary.csv")
        df.to_csv(csv_report_path, index=False)
        print(f"📈 CSV summary saved: {csv_report_path}")
    
    # Generate markdown summary report
    markdown_report_path = os.path.join(REPORTS_DIR, "performance_metrics_summary.md")
    generate_markdown_summary(report, markdown_report_path)
    print(f"📝 Markdown summary saved: {markdown_report_path}")

def generate_markdown_summary(report: Dict[str, Any], output_path: str) -> None:
    """
    Generate a human-readable markdown summary of the performance metrics.
    
    Args:
        report: The performance report
        output_path: Path to save the markdown file
    """
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write("# MOP Performance Metrics Summary\n\n")
        f.write(f"**Generated:** {report['timestamp']}\n\n")
        
        # Overall summary
        f.write("## Overall Summary\n\n")
        summary = report["summary"]
        f.write(f"- **Total Articles:** {summary['total_articles']}\n")
        f.write(f"- **Successful Analyses:** {summary['successful_analyses']}\n")
        f.write(f"- **Success Rate:** {summary['success_rate']:.1f}%\n\n")
        
        # Data type summaries
        f.write("## Performance by Data Type\n\n")
        for data_type, data_summary in report["data_type_summaries"].items():
            f.write(f"### {data_type.title()}\n\n")
            f.write(f"- **Articles Analyzed:** {data_summary['total_articles']}\n")
            f.write(f"- **Successful:** {data_summary['successful_analyses']}\n")
            f.write(f"- **Average Precision:** {data_summary['avg_precision']:.4f}\n")
            f.write(f"- **Average Recall:** {data_summary['avg_recall']:.4f}\n")
            f.write(f"- **Average F1:** {data_summary['avg_f1']:.4f}\n")
            f.write(f"- **Total TP:** {data_summary['total_tp']}\n")
            f.write(f"- **Total FP:** {data_summary['total_fp']}\n")
            f.write(f"- **Total FN:** {data_summary['total_fn']}\n\n")
        
        # Detailed results table
        f.write("## Detailed Results\n\n")
        f.write("| Data Type | Article | TP | FP | FN | Precision | Recall | F1 |\n")
        f.write("|-----------|---------|----|----|----|-----------|--------|----|\n")
        
        for data_type, results in report["detailed_results"].items():
            for result in results:
                if result["status"] == "success":
                    metrics = result["current_metrics"]
                    f.write(f"| {data_type} | {result['article']} | {metrics['tp']} | {metrics['fp']} | {metrics['fn']} | {metrics['precision']:.4f} | {metrics['recall']:.4f} | {metrics['f1']:.4f} |\n")

def print_summary(report: Dict[str, Any]) -> None:
    """
    Print a summary of the performance metrics to the console.
    
    Args:
        report: The performance report
    """
    print("\n" + "="*80)
    print("🎯 MOP PERFORMANCE METRICS SUMMARY")
    print("="*80)
    
    summary = report["summary"]
    print(f"📊 Overall Results:")
    print(f"   Total Articles: {summary['total_articles']}")
    print(f"   Successful Analyses: {summary['successful_analyses']}")
    print(f"   Success Rate: {summary['success_rate']:.1f}%")
    
    print(f"\n📈 Performance by Data Type:")
    for data_type, data_summary in report["data_type_summaries"].items():
        print(f"\n   {data_type.upper()}:")
        print(f"     Articles: {data_summary['total_articles']} | Successful: {data_summary['successful_analyses']}")
        print(f"     Avg Precision: {data_summary['avg_precision']:.4f}")
        print(f"     Avg Recall: {data_summary['avg_recall']:.4f}")
        print(f"     Avg F1: {data_summary['avg_f1']:.4f}")
    
    print("\n" + "="*80)

def main():
    """Main function to run the performance metrics calculation."""
    print("🚀 Starting MOP Performance Metrics Calculation...")
    
    # Check if triple comparison directory exists
    if not os.path.exists(TRIPLE_COMPARE_DIR):
        print(f"❌ Triple comparison directory not found: {TRIPLE_COMPARE_DIR}")
        print("Please run the MOP workflow first to generate comparison data.")
        return
    
    # Get triple comparison files
    print("📁 Scanning triple comparison files...")
    triple_files = get_triple_comparison_files()
    
    if not triple_files:
        print("❌ No triple comparison files found.")
        return
    
    print(f"✅ Found data for {len(triple_files)} data types")
    for data_type, articles in triple_files.items():
        print(f"   {data_type}: {len(articles)} articles")
    
    # Generate performance report
    print("\n🔍 Analyzing performance metrics...")
    report = generate_performance_report(triple_files)
    
    # Save reports
    print("\n💾 Saving performance reports...")
    save_reports(report)
    
    # Print summary
    print_summary(report)
    
    print("\n🎉 Performance metrics calculation completed!")
    print(f"📁 Reports saved in: {REPORTS_DIR}")

if __name__ == "__main__":
    main()
