"""
API handlers module for Flask route logic and request handling.
"""

import os
from datetime import datetime
from flask import jsonify, request
from typing import Dict, Any

from .config import AgentStatus, MessageType
from .pipeline_manager import pipeline_manager
from .file_operations import file_ops_manager
from .background_tasks import background_task_manager


class APIHandlers:
    """Handles API route logic for the Flask application."""
    
    def __init__(self):
        pass
    
    def start_pipeline(self) -> Dict[str, Any]:
        """Handle pipeline start request."""
        data = request.get_json()
        task_name = data.get('task_name', 'gaussian')
        meta_instruction = data.get('meta_instruction', pipeline_manager.get_state()['meta_instruction'])
        
        # Start pipeline
        pipeline_manager.start_pipeline(task_name, meta_instruction)
        
        # Start agent in background
        background_task_manager.run_agent_in_background(task_name, meta_instruction)
        
        return jsonify({
            'status': 'started',
            'task_name': task_name,
            'message': f'Pipeline started for task: {task_name}'
        })
    
    def stop_pipeline(self) -> Dict[str, Any]:
        """Handle pipeline stop request."""
        pipeline_manager.stop_pipeline()
        return jsonify({'status': 'stopped'})
    
    def get_pipeline_state(self) -> Dict[str, Any]:
        """Handle pipeline state request."""
        return jsonify(pipeline_manager.get_state())
    
    def approve_data_sniffing(self) -> Dict[str, Any]:
        """Handle data sniffing approval request."""
        # Add approval message to conversation
        pipeline_manager.add_conversation_message(
            'Data Sniffing report approved! Moving to next step...',
            MessageType.SYSTEM
        )
        
        return jsonify({'status': 'approved'})
    
    def rerun_data_sniffing(self) -> Dict[str, Any]:
        """Handle data sniffing rerun request."""
        data = request.get_json()
        feedback = data.get('feedback', '')
        
        # Add feedback message to conversation
        pipeline_manager.add_conversation_message(
            f'Rerunning Data Sniffing with feedback: {feedback}',
            MessageType.USER
        )
        
        # Start agent rerun in background
        current_task = pipeline_manager.get_current_task()
        meta_instruction = pipeline_manager.get_state()['meta_instruction']
        background_task_manager.run_agent_with_feedback_in_background(
            current_task, feedback, meta_instruction
        )
        
        return jsonify({'status': 'rerun_started'})
    
    def get_conversation(self) -> Dict[str, Any]:
        """Handle conversation request."""
        return jsonify({'conversation': pipeline_manager.get_conversation()})
    
    def get_agent_status(self) -> Dict[str, Any]:
        """Handle agent status request."""
        current_task = pipeline_manager.get_current_task()
        
        if not current_task:
            return jsonify({'status': AgentStatus.IDLE})
        
        # Check pipeline state first
        if not pipeline_manager.is_running() and pipeline_manager.get_current_agent() is None:
            # Agent has completed, check for report
            report_content = file_ops_manager.get_data_sniffing_report(current_task)
            
            if report_content:
                # Only add to conversation if not already there
                if pipeline_manager.set_data_sniffing_report(report_content):
                    # Add report to conversation
                    pipeline_manager.add_conversation_message(
                        report_content,
                        MessageType.AGENT,
                        MessageType.REPORT
                    )
                
                return jsonify({'status': AgentStatus.COMPLETED, 'report': report_content})
            else:
                # Agent completed but no report found
                return jsonify({'status': AgentStatus.COMPLETED, 'report': None})
        
        # Agent is still running
        elif pipeline_manager.is_running() or pipeline_manager.get_current_agent() is not None:
            return jsonify({'status': AgentStatus.RUNNING})
        
        # Default to idle
        else:
            return jsonify({'status': AgentStatus.IDLE})
    
    def get_resources(self) -> Dict[str, Any]:
        """Handle resources request."""
        try:
            resources = file_ops_manager.get_resources()
            return jsonify({'resources': resources})
        except Exception as e:
            return jsonify({'resources': [], 'error': str(e)})
    
    def get_logs(self) -> Dict[str, Any]:
        """Handle logs request."""
        try:
            logs = file_ops_manager.get_logs()
            return jsonify({'logs': logs})
        except Exception as e:
            return jsonify({'logs': f'Error reading logs: {e}'})


# Global API handlers instance
api_handlers = APIHandlers() 