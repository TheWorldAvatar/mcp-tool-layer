"""
Tests for the file operations module.
"""

import unittest
import os
import tempfile
import shutil
from unittest.mock import patch, MagicMock
from utils.file_operations import FileOperationsManager


class TestFileOperationsManager(unittest.TestCase):
    """Test cases for file operations manager."""
    
    def setUp(self):
        """Set up test fixtures."""
        # Create temporary directories for testing
        self.test_dir = tempfile.mkdtemp()
        self.log_dir = os.path.join(self.test_dir, "logs")
        self.task_dir = os.path.join(self.test_dir, "tasks")
        os.makedirs(self.log_dir, exist_ok=True)
        os.makedirs(self.task_dir, exist_ok=True)
        
        # Mock the resource DB operator
        self.mock_db_operator = MagicMock()
        
        # Create manager instance with mocked dependencies
        with patch('utils.file_operations.ResourceDBOperator') as mock_db_class:
            mock_db_class.return_value = self.mock_db_operator
            with patch('utils.file_operations.DATA_LOG_DIR', self.log_dir):
                with patch('utils.file_operations.SANDBOX_TASK_DIR', self.task_dir):
                    self.manager = FileOperationsManager()
    
    def tearDown(self):
        """Clean up after tests."""
        # Remove temporary directory
        shutil.rmtree(self.test_dir, ignore_errors=True)
    
    def test_get_logs_file_exists(self):
        """Test getting logs when file exists."""
        log_content = "Test log content\nLine 2\n"
        log_file = os.path.join(self.log_dir, "agent.log")
        
        with open(log_file, 'w', encoding='utf-8') as f:
            f.write(log_content)
        
        with patch('utils.file_operations.DATA_LOG_DIR', self.log_dir):
            result = self.manager.get_logs()
        
        self.assertEqual(result, log_content)
    
    def test_get_logs_file_not_exists(self):
        """Test getting logs when file doesn't exist."""
        with patch('utils.file_operations.DATA_LOG_DIR', self.log_dir):
            result = self.manager.get_logs()
        
        self.assertEqual(result, 'No logs available')
    
    def test_get_logs_read_error(self):
        """Test getting logs with read error."""
        log_file = os.path.join(self.log_dir, "agent.log")
        
        # Create a file but mock open to raise an exception
        with open(log_file, 'w') as f:
            f.write("test")
        
        with patch('utils.file_operations.DATA_LOG_DIR', self.log_dir):
            with patch('builtins.open', side_effect=PermissionError("Access denied")):
                result = self.manager.get_logs()
        
        self.assertIn('Error reading logs:', result)
    
    def test_get_data_sniffing_report_exists(self):
        """Test getting data sniffing report when file exists."""
        task_name = "test_task"
        report_content = "# Data Sniffing Report\nTest content"
        
        task_path = os.path.join(self.task_dir, task_name)
        os.makedirs(task_path, exist_ok=True)
        report_file = os.path.join(task_path, "data_sniffing_report.md")
        
        with open(report_file, 'w', encoding='utf-8') as f:
            f.write(report_content)
        
        with patch('utils.file_operations.SANDBOX_TASK_DIR', self.task_dir):
            result = self.manager.get_data_sniffing_report(task_name)
        
        self.assertEqual(result, report_content)
    
    def test_get_data_sniffing_report_not_exists(self):
        """Test getting data sniffing report when file doesn't exist."""
        task_name = "nonexistent_task"
        
        with patch('utils.file_operations.SANDBOX_TASK_DIR', self.task_dir):
            result = self.manager.get_data_sniffing_report(task_name)
        
        self.assertIsNone(result)
    
    def test_check_report_exists_true(self):
        """Test checking if report exists when it does."""
        task_name = "test_task"
        
        task_path = os.path.join(self.task_dir, task_name)
        os.makedirs(task_path, exist_ok=True)
        report_file = os.path.join(task_path, "data_sniffing_report.md")
        
        with open(report_file, 'w') as f:
            f.write("test")
        
        with patch('utils.file_operations.SANDBOX_TASK_DIR', self.task_dir):
            result = self.manager.check_report_exists(task_name)
        
        self.assertTrue(result)
    
    def test_check_report_exists_false(self):
        """Test checking if report exists when it doesn't."""
        task_name = "nonexistent_task"
        
        with patch('utils.file_operations.SANDBOX_TASK_DIR', self.task_dir):
            result = self.manager.check_report_exists(task_name)
        
        self.assertFalse(result)
    
    def test_get_resources_success(self):
        """Test getting resources successfully."""
        mock_resources = [
            MagicMock(__str__=lambda self: '{"path": "/test1", "type": "file"}'),
            MagicMock(__str__=lambda self: '{"path": "/test2", "type": "dir"}')
        ]
        self.mock_db_operator.get_all_resources.return_value = mock_resources
        
        result = self.manager.get_resources()
        
        self.assertEqual(len(result), 2)
        self.mock_db_operator.get_all_resources.assert_called_once()
    
    def test_get_resources_error(self):
        """Test getting resources with error."""
        self.mock_db_operator.get_all_resources.side_effect = Exception("Database error")
        
        result = self.manager.get_resources()
        
        self.assertEqual(result, [])
    
    def test_clear_logs(self):
        """Test clearing logs."""
        log_file = os.path.join(self.log_dir, "agent.log")
        
        # Create a log file with content
        with open(log_file, 'w') as f:
            f.write("existing content")
        
        with patch('utils.file_operations.DATA_LOG_DIR', self.log_dir):
            self.manager.clear_logs()
        
        # Verify file is empty
        with open(log_file, 'r') as f:
            content = f.read()
        self.assertEqual(content, "")
    
    def test_reset_resource_database(self):
        """Test resetting resource database."""
        self.manager.reset_resource_database()
        
        self.mock_db_operator.reset_db.assert_called_once()
    
    def test_register_resources_bulk(self):
        """Test registering resources in bulk."""
        mock_resources = [MagicMock(), MagicMock()]
        
        self.manager.register_resources_bulk(mock_resources)
        
        self.mock_db_operator.register_resources_bulk.assert_called_once_with(mock_resources)
    
    def test_register_resources_bulk_error(self):
        """Test registering resources with error."""
        mock_resources = [MagicMock()]
        self.mock_db_operator.register_resources_bulk.side_effect = Exception("Register error")
        
        # Should not raise exception
        self.manager.register_resources_bulk(mock_resources)
        
        self.mock_db_operator.register_resources_bulk.assert_called_once_with(mock_resources)


if __name__ == '__main__':
    unittest.main() 