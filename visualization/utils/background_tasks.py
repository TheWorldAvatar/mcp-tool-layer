"""
Background tasks module for handling agent execution in background threads.
"""

import threading
import sys
import os
from datetime import datetime
from typing import Optional, Callable

# Add parent directory to path for imports
parent_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, parent_dir)

from src.utils.global_logger import initialize_logging, get_logger
from src.utils.resource_db_operations import ResourceDBOperator
from .config import MessageType
from .pipeline_manager import pipeline_manager

logger = get_logger("visualization", "background_tasks")


class BackgroundTaskManager:
    """Manages background task execution for agents."""
    
    def __init__(self, socketio_emit_func: Optional[Callable] = None):
        self.socketio_emit = socketio_emit_func
        self.current_thread: Optional[threading.Thread] = None
    
    def set_socketio_emit(self, emit_func: Callable) -> None:
        """Set the SocketIO emit function for communication."""
        self.socketio_emit = emit_func
    
    def _emit_conversation_update(self) -> None:
        """Emit conversation update via SocketIO."""
        if self.socketio_emit:
            self.socketio_emit('conversation_update', {
                'conversation': pipeline_manager.get_conversation()
            })
    
    def _emit_agent_status_update(self, agent: str, status: str, task: str, error: str = None) -> None:
        """Emit agent status update via SocketIO."""
        if self.socketio_emit:
            update_data = {
                'agent': agent,
                'status': status,
                'task': task
            }
            if error:
                update_data['error'] = error
            self.socketio_emit('agent_status_update', update_data)
    
    def run_agent_in_background(self, task_name: str, meta_instruction: str) -> None:
        """Run the DataSniffingAgent in a background thread."""
        def _run_agent():
            try:
                logger.info(f"Starting DataSniffingAgent in background for task: {task_name}")
                
                # Update state to show agent is running
                pipeline_manager.set_agent_running('DataSniffingAgent')
                
                # Add "Agent Started" conversation message
                pipeline_manager.add_conversation_message(
                    f'🤖 Data Sniffing Agent has started analyzing data for task: {task_name}',
                    MessageType.SYSTEM
                )
                
                # Emit updates
                self._emit_conversation_update()
                self._emit_agent_status_update('DataSniffingAgent', 'running', task_name)
                
                # Initialize logging and SQLite in this thread
                initialize_logging()
                
                # Create new resource DB connection in this thread
                resource_db_operator = ResourceDBOperator()
                
                # Import and run the agent
                from simple_agent_call import run_data_sniffing_agent_simple
                success = run_data_sniffing_agent_simple(task_name, meta_instruction)
                
                if success:
                    logger.info(f"DataSniffingAgent completed successfully for task: {task_name}")
                    pipeline_manager.set_agent_completed()
                    
                    # Add "Agent Completed" conversation message
                    pipeline_manager.add_conversation_message(
                        f'✅ Data Sniffing Agent has completed the analysis for task: {task_name}',
                        MessageType.SYSTEM
                    )
                    
                    # Emit updates
                    self._emit_conversation_update()
                    self._emit_agent_status_update('DataSniffingAgent', 'completed', task_name)
                else:
                    logger.error(f"DataSniffingAgent failed for task: {task_name}")
                    pipeline_manager.set_agent_error()
                    
                    # Add "Agent Failed" conversation message
                    pipeline_manager.add_conversation_message(
                        f'❌ Data Sniffing Agent failed for task: {task_name}',
                        MessageType.SYSTEM
                    )
                    
                    # Emit updates
                    self._emit_conversation_update()
                    self._emit_agent_status_update('DataSniffingAgent', 'error', task_name)
                    
            except Exception as e:
                logger.error(f"Error in background agent execution: {e}")
                pipeline_manager.set_agent_error()
                
                # Add "Agent Error" conversation message
                pipeline_manager.add_conversation_message(
                    f'💥 Data Sniffing Agent encountered an error for task: {task_name}: {str(e)}',
                    MessageType.SYSTEM
                )
                
                # Emit updates
                self._emit_conversation_update()
                self._emit_agent_status_update('DataSniffingAgent', 'error', task_name, str(e))
        
        # Start agent in background thread
        self.current_thread = threading.Thread(
            target=_run_agent,
            daemon=True
        )
        self.current_thread.start()
        
        logger.info(f"Started DataSniffingAgent in background for task: {task_name}")
    
    def run_agent_with_feedback_in_background(self, task_name: str, user_feedback: str, meta_instruction: str) -> None:
        """Run the DataSniffingAgent with feedback in a background thread."""
        def _run_agent_with_feedback():
            try:
                logger.info(f"Starting DataSniffingAgent with feedback in background for task: {task_name}")
                
                # Update state to show agent is running
                pipeline_manager.set_agent_running('DataSniffingAgent')
                
                # Add "Agent Restarted with Feedback" conversation message
                pipeline_manager.add_conversation_message(
                    f'🔄 Data Sniffing Agent is rerunning with your feedback for task: {task_name}',
                    MessageType.SYSTEM
                )
                
                # Emit updates
                self._emit_conversation_update()
                self._emit_agent_status_update('DataSniffingAgent', 'running', task_name)
                
                # Initialize logging and SQLite in this thread
                initialize_logging()
                
                # Create new resource DB connection in this thread
                resource_db_operator = ResourceDBOperator()
                
                # Import and run the agent with feedback
                from simple_agent_call import run_data_sniffing_agent_with_feedback
                success = run_data_sniffing_agent_with_feedback(task_name, user_feedback, meta_instruction)
                
                if success:
                    logger.info(f"DataSniffingAgent with feedback completed successfully for task: {task_name}")
                    pipeline_manager.set_agent_completed()
                    
                    # Add "Agent Completed with Feedback" conversation message
                    pipeline_manager.add_conversation_message(
                        f'✅ Data Sniffing Agent has completed the rerun with feedback for task: {task_name}',
                        MessageType.SYSTEM
                    )
                    
                    # Emit updates
                    self._emit_conversation_update()
                    self._emit_agent_status_update('DataSniffingAgent', 'completed', task_name)
                else:
                    logger.error(f"DataSniffingAgent with feedback failed for task: {task_name}")
                    pipeline_manager.set_agent_error()
                    
                    # Add "Agent Failed with Feedback" conversation message
                    pipeline_manager.add_conversation_message(
                        f'❌ Data Sniffing Agent rerun with feedback failed for task: {task_name}',
                        MessageType.SYSTEM
                    )
                    
                    # Emit updates
                    self._emit_conversation_update()
                    self._emit_agent_status_update('DataSniffingAgent', 'error', task_name)
                    
            except Exception as e:
                logger.error(f"Error in background agent execution with feedback: {e}")
                pipeline_manager.set_agent_error()
                
                # Add "Agent Error with Feedback" conversation message
                pipeline_manager.add_conversation_message(
                    f'💥 Data Sniffing Agent rerun encountered an error for task: {task_name}: {str(e)}',
                    MessageType.SYSTEM
                )
                
                # Emit updates
                self._emit_conversation_update()
                self._emit_agent_status_update('DataSniffingAgent', 'error', task_name, str(e))
        
        # Start agent in background thread
        self.current_thread = threading.Thread(
            target=_run_agent_with_feedback,
            daemon=True
        )
        self.current_thread.start()
        
        logger.info(f"Started DataSniffingAgent with feedback in background for task: {task_name}")


# Global background task manager instance
background_task_manager = BackgroundTaskManager() 