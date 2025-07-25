"""
Tests for the pipeline manager module.
"""

import unittest
from datetime import datetime
from unittest.mock import patch
from utils.pipeline_manager import PipelineManager
from utils.config import MessageType


class TestPipelineManager(unittest.TestCase):
    """Test cases for pipeline manager."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.manager = PipelineManager()
    
    def tearDown(self):
        """Clean up after tests."""
        self.manager.reset_state()
    
    def test_initial_state(self):
        """Test initial pipeline state."""
        state = self.manager.get_state()
        
        self.assertFalse(state['is_running'])
        self.assertIsNone(state['current_task'])
        self.assertIsNone(state['start_time'])
        self.assertIsNone(state['current_agent'])
        self.assertEqual(state['conversation'], [])
        self.assertIsNone(state['data_sniffing_report'])
    
    def test_start_pipeline(self):
        """Test starting the pipeline."""
        task_name = "test_task"
        meta_instruction = "test instruction"
        
        self.manager.start_pipeline(task_name, meta_instruction)
        state = self.manager.get_state()
        
        self.assertEqual(state['current_task'], task_name)
        self.assertEqual(state['meta_instruction'], meta_instruction)
        self.assertTrue(state['is_running'])
        self.assertIsInstance(state['start_time'], datetime)
        self.assertIsNone(state['current_agent'])
        self.assertIsNone(state['data_sniffing_report'])
        self.assertGreater(len(state['conversation']), 0)
    
    def test_stop_pipeline(self):
        """Test stopping the pipeline."""
        # Start first
        self.manager.start_pipeline("test_task", "test instruction")
        self.assertTrue(self.manager.is_running())
        
        # Stop
        self.manager.stop_pipeline()
        
        self.assertFalse(self.manager.is_running())
        self.assertIsNone(self.manager.get_current_agent())
    
    def test_add_conversation_message(self):
        """Test adding conversation messages."""
        message = "Test message"
        sender = MessageType.SYSTEM
        
        self.manager.add_conversation_message(message, sender)
        conversation = self.manager.get_conversation()
        
        self.assertEqual(len(conversation), 1)
        self.assertEqual(conversation[0]['message'], message)
        self.assertEqual(conversation[0]['sender'], sender)
        self.assertIn('timestamp', conversation[0])
    
    def test_add_conversation_message_with_type(self):
        """Test adding conversation messages with type."""
        message = "Test report"
        sender = MessageType.AGENT
        message_type = MessageType.REPORT
        
        self.manager.add_conversation_message(message, sender, message_type)
        conversation = self.manager.get_conversation()
        
        self.assertEqual(len(conversation), 1)
        self.assertEqual(conversation[0]['message'], message)
        self.assertEqual(conversation[0]['sender'], sender)
        self.assertEqual(conversation[0]['type'], message_type)
    
    def test_agent_lifecycle(self):
        """Test agent lifecycle management."""
        agent_name = "TestAgent"
        
        # Start agent
        self.manager.set_agent_running(agent_name)
        self.assertEqual(self.manager.get_current_agent(), agent_name)
        self.assertTrue(self.manager.is_running())
        
        # Complete agent
        self.manager.set_agent_completed()
        self.assertIsNone(self.manager.get_current_agent())
        self.assertFalse(self.manager.is_running())
    
    def test_agent_error(self):
        """Test agent error handling."""
        agent_name = "TestAgent"
        
        # Start agent
        self.manager.set_agent_running(agent_name)
        self.assertTrue(self.manager.is_running())
        
        # Set error
        self.manager.set_agent_error()
        self.assertIsNone(self.manager.get_current_agent())
        self.assertFalse(self.manager.is_running())
    
    def test_data_sniffing_report(self):
        """Test data sniffing report management."""
        report_content = "Test report content"
        
        # Set report
        result = self.manager.set_data_sniffing_report(report_content)
        self.assertTrue(result)  # Should return True for new content
        self.assertEqual(self.manager.get_data_sniffing_report(), report_content)
        
        # Set same report again
        result = self.manager.set_data_sniffing_report(report_content)
        self.assertFalse(result)  # Should return False for same content
    
    def test_reset_state(self):
        """Test resetting the state."""
        # Modify state
        self.manager.start_pipeline("test_task", "test instruction")
        self.manager.add_conversation_message("test", MessageType.SYSTEM)
        
        # Verify state was modified
        state = self.manager.get_state()
        self.assertTrue(state['is_running'])
        self.assertGreater(len(state['conversation']), 0)
        
        # Reset
        self.manager.reset_state()
        
        # Verify state was reset
        state = self.manager.get_state()
        self.assertFalse(state['is_running'])
        self.assertIsNone(state['current_task'])
        self.assertEqual(state['conversation'], [])
    
    def test_thread_safety(self):
        """Test thread safety of state operations."""
        import threading
        import time
        
        def modify_state():
            for i in range(10):
                self.manager.add_conversation_message(f"Message {i}", MessageType.SYSTEM)
                time.sleep(0.001)
        
        # Create multiple threads
        threads = []
        for _ in range(5):
            thread = threading.Thread(target=modify_state)
            threads.append(thread)
            thread.start()
        
        # Wait for all threads to complete
        for thread in threads:
            thread.join()
        
        # Verify state consistency
        conversation = self.manager.get_conversation()
        self.assertEqual(len(conversation), 50)  # 5 threads * 10 messages each


if __name__ == '__main__':
    unittest.main() 