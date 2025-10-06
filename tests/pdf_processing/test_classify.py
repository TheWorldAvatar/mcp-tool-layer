"""
Unit tests for document classification operations.

This module tests all functions in src.mcp_servers.document.operations.classify
using mock data and isolated test environments.
"""

import unittest
import tempfile
import json
import os
import shutil
from unittest.mock import patch, MagicMock
import sys

# Add the project root to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from src.mcp_servers.document.operations.classify import (
    classify_section,
    load_sections,
    get_classification_status
)


class TestClassifySection(unittest.TestCase):
    """Test cases for the classify_section function."""
    
    def setUp(self):
        """Set up test environment with temporary directory and mock data."""
        # Create temporary directory
        self.temp_dir = tempfile.mkdtemp()
        self.test_doi = "test_doi_001"
        self.doi_dir = os.path.join(self.temp_dir, self.test_doi)
        os.makedirs(self.doi_dir, exist_ok=True)
        
        # Create test unclassified data
        self.test_unclassified_data = {
            "Section 0": {
                "title": "Introduction",
                "content": "This is the introduction to the paper.",
                "source": "main"
            },
            "Section 1": {
                "title": "Results",
                "content": "This is the results section of the paper.",
                "source": "main"
            },
            "Section 2": {
                "title": "References",
                "content": "Reference 1, Reference 2, etc.",
                "source": "main"
            }
        }
        
        # Save test sections data
        sections_file = os.path.join(self.doi_dir, "sections.json")
        with open(sections_file, 'w', encoding='utf-8') as f:
            json.dump(self.test_unclassified_data, f, indent=2)
        
        # Mock DATA_DIR
        self.data_dir_patcher = patch('src.mcp_servers.document.operations.classify.DATA_DIR', self.temp_dir)
        self.data_dir_patcher.start()
    
    def tearDown(self):
        """Clean up test environment."""
        self.data_dir_patcher.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_classify_section_keep_success(self):
        """Test successful classification of a section as 'keep'."""
        result = classify_section(
            section_index=0,
            option="keep",
            doi=self.test_doi
        )
        
        self.assertTrue(result['success'])
        self.assertIn("Successfully updated Section 0 with option: keep", result['message'])
        
        # Verify file was created
        classified_file = os.path.join(self.doi_dir, "sections.json")
        self.assertTrue(os.path.exists(classified_file))
        
        # Verify content
        with open(classified_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        self.assertEqual(data['Section 0']['keep_or_discard'], 'keep')
        self.assertEqual(data['Section 0']['title'], 'Introduction')
    
    def test_classify_section_discard_success(self):
        """Test successful classification of a section as 'discard'."""
        result = classify_section(
            section_index=1,
            option="discard",
            doi=self.test_doi
        )
        
        self.assertTrue(result['success'])
        self.assertIn("Successfully updated Section 1 with option: discard", result['message'])
        
        # Verify content
        classified_file = os.path.join(self.doi_dir, "sections.json")
        with open(classified_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        self.assertEqual(data['Section 1']['keep_or_discard'], 'discard')
    
    def test_classify_section_invalid_option(self):
        """Test classification with invalid option."""
        result = classify_section(
            section_index=0,
            option="invalid_option",
            doi=self.test_doi
        )
        
        self.assertFalse(result['success'])
        self.assertIn("Invalid option 'invalid_option'. Must be 'keep' or 'discard'", result['message'])
    
    def test_classify_section_nonexistent_section(self):
        """Test classification of non-existent section."""
        result = classify_section(
            section_index=999,
            option="keep",
            doi=self.test_doi
        )
        
        self.assertFalse(result['success'])
        self.assertIn("Section 999 not found", result['message'])
    
    def test_classify_section_nonexistent_doi(self):
        """Test classification with non-existent DOI."""
        result = classify_section(
            section_index=0,
            option="keep",
            doi="nonexistent_doi"
        )
        
        self.assertFalse(result['success'])
        self.assertIn("sections.json not found", result['message'])
    
    def test_classify_section_load_existing_classified(self):
        """Test that existing classified data is loaded and updated."""
        # First, create some classified data (include Section 2 from unclassified data)
        existing_classified = {
            "Section 0": {
                "title": "Introduction",
                "content": "This is the introduction to the paper.",
                "source": "main",
                "keep_or_discard": "keep"
            },
            "Section 1": {
                "title": "Results",
                "content": "This is the results section of the paper.",
                "source": "main",
                "keep_or_discard": "discard"
            },
            "Section 2": {
                "title": "References",
                "content": "Reference 1, Reference 2, etc.",
                "source": "main",
                "keep_or_discard": "discard"
            }
        }
        
        classified_file = os.path.join(self.doi_dir, "sections.json")
        with open(classified_file, 'w', encoding='utf-8') as f:
            json.dump(existing_classified, f, indent=2)
        
        # Now classify a new section (Section 2 exists in unclassified data)
        result = classify_section(
            section_index=2,
            option="keep",
            doi=self.test_doi
        )
        
        self.assertTrue(result['success'])
        
        # Verify all sections are present
        with open(classified_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        self.assertEqual(len(data), 3)
        self.assertEqual(data['Section 0']['keep_or_discard'], 'keep')
        self.assertEqual(data['Section 1']['keep_or_discard'], 'discard')
        self.assertEqual(data['Section 2']['keep_or_discard'], 'keep')
    
    def test_classify_section_convert_string_content(self):
        """Test classification when section content is just a string."""
        # Create data where section content is just a string
        string_content_data = {
            "Section 0": "This is just string content without title/source"
        }
        
        unclassified_file = os.path.join(self.doi_dir, "sections.json")
        with open(unclassified_file, 'w', encoding='utf-8') as f:
            json.dump(string_content_data, f, indent=2)
        
        result = classify_section(
            section_index=0,
            option="keep",
            doi=self.test_doi
        )
        
        self.assertTrue(result['success'])
        
        # Verify the structure was converted
        classified_file = os.path.join(self.doi_dir, "sections.json")
        with open(classified_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        self.assertIsInstance(data['Section 0'], dict)
        self.assertEqual(data['Section 0']['content'], 'This is just string content without title/source')
        self.assertEqual(data['Section 0']['keep_or_discard'], 'keep')


class TestLoadSections(unittest.TestCase):
    """Test cases for the load_sections function."""
    
    def setUp(self):
        """Set up test environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.test_doi = "test_doi_002"
        self.doi_dir = os.path.join(self.temp_dir, self.test_doi)
        os.makedirs(self.doi_dir, exist_ok=True)
        
        # Create test data
        self.test_data = {
            "Section 0": {"title": "Test Section", "content": "Test content"}
        }
        
        # Mock DATA_DIR
        self.data_dir_patcher = patch('src.mcp_servers.document.operations.classify.DATA_DIR', self.temp_dir)
        self.data_dir_patcher.start()
    
    def tearDown(self):
        """Clean up test environment."""
        self.data_dir_patcher.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_load_sections_unclassified_success(self):
        """Test successful loading of unclassified sections."""
        # Create unclassified file
        unclassified_file = os.path.join(self.doi_dir, "sections.json")
        with open(unclassified_file, 'w', encoding='utf-8') as f:
            json.dump(self.test_data, f, indent=2)
        
        result = load_sections(self.test_doi, classified=False)
        
        self.assertIsNotNone(result)
        self.assertEqual(result, self.test_data)
    
    def test_load_sections_classified_success(self):
        """Test successful loading of classified sections."""
        # Create classified file
        classified_file = os.path.join(self.doi_dir, "sections.json")
        with open(classified_file, 'w', encoding='utf-8') as f:
            json.dump(self.test_data, f, indent=2)
        
        result = load_sections(self.test_doi, classified=True)
        
        self.assertIsNotNone(result)
        self.assertEqual(result, self.test_data)
    
    def test_load_sections_file_not_found(self):
        """Test loading when file doesn't exist."""
        result = load_sections(self.test_doi, classified=False)
        
        self.assertIsNone(result)
    
    def test_load_sections_invalid_json(self):
        """Test loading with invalid JSON file."""
        # Create invalid JSON file
        invalid_file = os.path.join(self.doi_dir, "sections.json")
        with open(invalid_file, 'w', encoding='utf-8') as f:
            f.write("invalid json content")
        
        result = load_sections(self.test_doi, classified=False)
        
        self.assertIsNone(result)


class TestGetClassificationStatus(unittest.TestCase):
    """Test cases for the get_classification_status function."""
    
    def setUp(self):
        """Set up test environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.test_doi = "test_doi_003"
        self.doi_dir = os.path.join(self.temp_dir, self.test_doi)
        os.makedirs(self.doi_dir, exist_ok=True)
        
        # Mock DATA_DIR
        self.data_dir_patcher = patch('src.mcp_servers.document.operations.classify.DATA_DIR', self.temp_dir)
        self.data_dir_patcher.start()
    
    def tearDown(self):
        """Clean up test environment."""
        self.data_dir_patcher.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_get_classification_status_no_data(self):
        """Test status when no classified data exists."""
        result = get_classification_status(self.test_doi)
        
        self.assertFalse(result['success'])
        self.assertEqual(result['message'], 'No classified data found')
        self.assertEqual(result['total_sections'], 0)
        self.assertEqual(result['classified'], 0)
        self.assertEqual(result['keep'], 0)
        self.assertEqual(result['discard'], 0)
    
    def test_get_classification_status_partial_classification(self):
        """Test status with partially classified data."""
        classified_data = {
            "Section 0": {
                "title": "Introduction",
                "content": "Content",
                "keep_or_discard": "keep"
            },
            "Section 1": {
                "title": "Results",
                "content": "Content",
                "keep_or_discard": "discard"
            },
            "Section 2": {
                "title": "References",
                "content": "Content"
                # No keep_or_discard field
            }
        }
        
        classified_file = os.path.join(self.doi_dir, "sections.json")
        with open(classified_file, 'w', encoding='utf-8') as f:
            json.dump(classified_data, f, indent=2)
        
        result = get_classification_status(self.test_doi)
        
        self.assertTrue(result['success'])
        self.assertEqual(result['total_sections'], 3)
        self.assertEqual(result['classified'], 2)
        self.assertEqual(result['keep'], 1)
        self.assertEqual(result['discard'], 1)
        self.assertAlmostEqual(result['percentage_complete'], 66.66666666666667, places=10)
    
    def test_get_classification_status_complete_classification(self):
        """Test status with completely classified data."""
        classified_data = {
            "Section 0": {
                "title": "Introduction",
                "content": "Content",
                "keep_or_discard": "keep"
            },
            "Section 1": {
                "title": "Results",
                "content": "Content",
                "keep_or_discard": "discard"
            },
            "Section 2": {
                "title": "References",
                "content": "Content",
                "keep_or_discard": "discard"
            }
        }
        
        classified_file = os.path.join(self.doi_dir, "sections.json")
        with open(classified_file, 'w', encoding='utf-8') as f:
            json.dump(classified_data, f, indent=2)
        
        result = get_classification_status(self.test_doi)
        
        self.assertTrue(result['success'])
        self.assertEqual(result['total_sections'], 3)
        self.assertEqual(result['classified'], 3)
        self.assertEqual(result['keep'], 1)
        self.assertEqual(result['discard'], 2)
        self.assertEqual(result['percentage_complete'], 100.0)
    
    def test_get_classification_status_mixed_data_types(self):
        """Test status with mixed data types (dict and string)."""
        classified_data = {
            "Section 0": {
                "title": "Introduction",
                "content": "Content",
                "keep_or_discard": "keep"
            },
            "Section 1": "Just string content",  # String content
            "Section 2": {
                "title": "Results",
                "content": "Content",
                "keep_or_discard": "discard"
            }
        }
        
        classified_file = os.path.join(self.doi_dir, "sections.json")
        with open(classified_file, 'w', encoding='utf-8') as f:
            json.dump(classified_data, f, indent=2)
        
        result = get_classification_status(self.test_doi)
        
        self.assertTrue(result['success'])
        self.assertEqual(result['total_sections'], 3)
        self.assertEqual(result['classified'], 2)  # Only dict entries with keep_or_discard
        self.assertEqual(result['keep'], 1)
        self.assertEqual(result['discard'], 1)


class TestIntegration(unittest.TestCase):
    """Integration tests using real test data."""
    
    def setUp(self):
        """Set up test environment with real test data."""
        self.temp_dir = tempfile.mkdtemp()
        self.test_doi = "test_doi_integration"
        self.doi_dir = os.path.join(self.temp_dir, self.test_doi)
        os.makedirs(self.doi_dir, exist_ok=True)
        
        # Copy real test data
        test_data_file = os.path.join(os.path.dirname(__file__), 'test_data', 'sections.json')
        if os.path.exists(test_data_file):
            shutil.copy(test_data_file, os.path.join(self.doi_dir, 'sections.json'))
        
        # Mock DATA_DIR
        self.data_dir_patcher = patch('src.mcp_servers.document.operations.classify.DATA_DIR', self.temp_dir)
        self.data_dir_patcher.start()
    
    def tearDown(self):
        """Clean up test environment."""
        self.data_dir_patcher.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_full_classification_workflow(self):
        """Test the complete classification workflow using real test data."""
        # Load unclassified data
        unclassified = load_sections(self.test_doi, classified=False)
        self.assertIsNotNone(unclassified)
        
        # Classify sections
        classifications = [
            (0, "keep"),
            (1, "discard")
        ]
        
        for section_idx, option in classifications:
            result = classify_section(
                section_index=section_idx,
                option=option,
                doi=self.test_doi
            )
            self.assertTrue(result['success'])
        
        # Check status
        status = get_classification_status(self.test_doi)
        self.assertTrue(status['success'])
        self.assertEqual(status['total_sections'], 2)
        self.assertEqual(status['classified'], 2)
        self.assertEqual(status['keep'], 1)
        self.assertEqual(status['discard'], 1)
        self.assertEqual(status['percentage_complete'], 100.0)
        
        # Verify classified data
        classified = load_sections(self.test_doi, classified=True)
        self.assertIsNotNone(classified)
        self.assertEqual(classified['Section 0']['keep_or_discard'], 'keep')
        self.assertEqual(classified['Section 1']['keep_or_discard'], 'discard')


if __name__ == '__main__':
    # Create test suite
    test_suite = unittest.TestSuite()
    
    # Add test cases
    test_suite.addTest(unittest.makeSuite(TestClassifySection))
    test_suite.addTest(unittest.makeSuite(TestLoadSections))
    test_suite.addTest(unittest.makeSuite(TestGetClassificationStatus))
    test_suite.addTest(unittest.makeSuite(TestIntegration))
    
    # Run tests
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(test_suite)
    
    # Print summary
    print(f"\n{'='*60}")
    print(f"TEST SUMMARY")
    print(f"{'='*60}")
    print(f"Tests run: {result.testsRun}")
    print(f"Failures: {len(result.failures)}")
    print(f"Errors: {len(result.errors)}")
    print(f"Success rate: {((result.testsRun - len(result.failures) - len(result.errors)) / result.testsRun * 100):.1f}%")
    
    if result.failures:
        print(f"\nFAILURES:")
        for test, traceback in result.failures:
            print(f"  - {test}: {traceback}")
    
    if result.errors:
        print(f"\nERRORS:")
        for test, traceback in result.errors:
            print(f"  - {test}: {traceback}")
