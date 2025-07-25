"""
Tests for the configuration module.
"""

import unittest
from utils.config import (
    FLASK_SECRET_KEY, FLASK_HOST, FLASK_PORT, PIPELINE_STEPS,
    DEFAULT_META_INSTRUCTION, get_initial_pipeline_state,
    AgentStatus, MessageType, SocketEvents
)


class TestConfig(unittest.TestCase):
    """Test cases for configuration module."""
    
    def test_flask_config_constants(self):
        """Test Flask configuration constants."""
        self.assertIsInstance(FLASK_SECRET_KEY, str)
        self.assertIsInstance(FLASK_HOST, str)
        self.assertIsInstance(FLASK_PORT, int)
        self.assertEqual(FLASK_HOST, '0.0.0.0')
        self.assertEqual(FLASK_PORT, 5000)
    
    def test_pipeline_steps_structure(self):
        """Test pipeline steps configuration structure."""
        self.assertIsInstance(PIPELINE_STEPS, list)
        self.assertGreater(len(PIPELINE_STEPS), 0)
        
        for step in PIPELINE_STEPS:
            self.assertIsInstance(step, dict)
            self.assertIn('id', step)
            self.assertIn('name', step)
            self.assertIn('agent', step)
            self.assertIn('description', step)
            self.assertIn('status', step)
    
    def test_default_meta_instruction(self):
        """Test default meta instruction."""
        self.assertIsInstance(DEFAULT_META_INSTRUCTION, str)
        self.assertGreater(len(DEFAULT_META_INSTRUCTION), 0)
    
    def test_initial_pipeline_state(self):
        """Test initial pipeline state structure."""
        state = get_initial_pipeline_state()
        
        self.assertIsInstance(state, dict)
        self.assertIn('is_running', state)
        self.assertIn('current_task', state)
        self.assertIn('start_time', state)
        self.assertIn('current_agent', state)
        self.assertIn('conversation', state)
        self.assertIn('data_sniffing_report', state)
        self.assertIn('meta_instruction', state)
        
        # Test initial values
        self.assertFalse(state['is_running'])
        self.assertIsNone(state['current_task'])
        self.assertIsNone(state['start_time'])
        self.assertIsNone(state['current_agent'])
        self.assertEqual(state['conversation'], [])
        self.assertIsNone(state['data_sniffing_report'])
        self.assertEqual(state['meta_instruction'], DEFAULT_META_INSTRUCTION)
    
    def test_agent_status_constants(self):
        """Test agent status constants."""
        self.assertEqual(AgentStatus.IDLE, 'idle')
        self.assertEqual(AgentStatus.RUNNING, 'running')
        self.assertEqual(AgentStatus.COMPLETED, 'completed')
        self.assertEqual(AgentStatus.ERROR, 'error')
    
    def test_message_type_constants(self):
        """Test message type constants."""
        self.assertEqual(MessageType.SYSTEM, 'system')
        self.assertEqual(MessageType.USER, 'user')
        self.assertEqual(MessageType.AGENT, 'agent')
        self.assertEqual(MessageType.REPORT, 'report')
    
    def test_socket_events_constants(self):
        """Test socket events constants."""
        self.assertEqual(SocketEvents.CONNECT, 'connect')
        self.assertEqual(SocketEvents.DISCONNECT, 'disconnect')
        self.assertEqual(SocketEvents.CONNECTED, 'connected')
        self.assertEqual(SocketEvents.CONVERSATION_UPDATE, 'conversation_update')
        self.assertEqual(SocketEvents.AGENT_STATUS_UPDATE, 'agent_status_update')
        self.assertEqual(SocketEvents.DATA_SNIFFING_REPORT, 'data_sniffing_report')


if __name__ == '__main__':
    unittest.main() 