"""
Pipeline manager module for handling pipeline state and operations.
"""

import os
import threading
from datetime import datetime
from typing import Optional, Dict, Any

from .config import get_initial_pipeline_state, MessageType, AgentStatus


class PipelineManager:
    """Manages pipeline state and operations."""
    
    def __init__(self):
        self.state = get_initial_pipeline_state()
        self.agent_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
    
    def get_state(self) -> Dict[str, Any]:
        """Get current pipeline state."""
        with self._lock:
            return self.state.copy()
    
    def update_state(self, updates: Dict[str, Any]) -> None:
        """Update pipeline state with given updates."""
        with self._lock:
            self.state.update(updates)
    
    def is_running(self) -> bool:
        """Check if pipeline is currently running."""
        return self.state.get('is_running', False)
    
    def get_current_task(self) -> Optional[str]:
        """Get current task name."""
        return self.state.get('current_task')
    
    def get_current_agent(self) -> Optional[str]:
        """Get current agent name."""
        return self.state.get('current_agent')
    
    def start_pipeline(self, task_name: str, meta_instruction: str) -> None:
        """Start the pipeline with given task and instruction."""
        with self._lock:
            self.state.update({
                'current_task': task_name,
                'start_time': datetime.now(),
                'meta_instruction': meta_instruction,
                'is_running': True,
                'current_agent': None,
                'data_sniffing_report': None
            })
            
            # Add initial conversation message
            initial_message = {
                'message': f'🚀 Starting Data Sniffing Agent for task: {task_name}',
                'sender': MessageType.SYSTEM,
                'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            self.state['conversation'].append(initial_message)
    
    def stop_pipeline(self) -> None:
        """Stop the pipeline."""
        with self._lock:
            self.state.update({
                'is_running': False,
                'current_agent': None
            })
    
    def add_conversation_message(self, message: str, sender: str, 
                               message_type: str = None) -> None:
        """Add a message to the conversation."""
        with self._lock:
            conversation_message = {
                'message': message,
                'sender': sender,
                'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            if message_type:
                conversation_message['type'] = message_type
            
            self.state['conversation'].append(conversation_message)
    
    def get_conversation(self) -> list:
        """Get conversation history."""
        return self.state.get('conversation', [])
    
    def set_agent_running(self, agent_name: str) -> None:
        """Set agent as running."""
        with self._lock:
            self.state.update({
                'current_agent': agent_name,
                'is_running': True
            })
    
    def set_agent_completed(self) -> None:
        """Set agent as completed."""
        with self._lock:
            self.state.update({
                'current_agent': None,
                'is_running': False
            })
    
    def set_agent_error(self) -> None:
        """Set agent as errored."""
        with self._lock:
            self.state.update({
                'current_agent': None,
                'is_running': False
            })
    
    def set_data_sniffing_report(self, report_content: str) -> None:
        """Set the data sniffing report content."""
        with self._lock:
            # Only update if content is different
            if self.state.get('data_sniffing_report') != report_content:
                self.state['data_sniffing_report'] = report_content
                return True
        return False
    
    def get_data_sniffing_report(self) -> Optional[str]:
        """Get the data sniffing report content."""
        return self.state.get('data_sniffing_report')
    
    def reset_state(self) -> None:
        """Reset pipeline state to initial state."""
        with self._lock:
            self.state = get_initial_pipeline_state()


# Global pipeline manager instance
pipeline_manager = PipelineManager() 