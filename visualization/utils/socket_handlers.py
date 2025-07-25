"""
Socket handlers module for SocketIO event handling.
"""

from flask_socketio import emit
from src.utils.global_logger import get_logger
from .config import SocketEvents

logger = get_logger("visualization", "socket_handlers")


class SocketHandlers:
    """Handles SocketIO events for the application."""
    
    def __init__(self):
        pass
    
    def handle_connect(self):
        """Handle client connection."""
        logger.info("Client connected to visualization")
        emit(SocketEvents.CONNECTED, {'data': 'Connected to visualization server'})
    
    def handle_disconnect(self):
        """Handle client disconnection."""
        logger.info("Client disconnected from visualization")
    
    def emit_conversation_update(self, socketio, conversation):
        """Emit conversation update to clients."""
        socketio.emit(SocketEvents.CONVERSATION_UPDATE, {
            'conversation': conversation
        })
    
    def emit_agent_status_update(self, socketio, agent, status, task, error=None):
        """Emit agent status update to clients."""
        update_data = {
            'agent': agent,
            'status': status,
            'task': task
        }
        if error:
            update_data['error'] = error
        
        socketio.emit(SocketEvents.AGENT_STATUS_UPDATE, update_data)
    
    def emit_data_sniffing_report(self, socketio, report, task):
        """Emit data sniffing report to clients."""
        socketio.emit(SocketEvents.DATA_SNIFFING_REPORT, {
            'report': report,
            'task': task
        })


# Global socket handlers instance
socket_handlers = SocketHandlers() 