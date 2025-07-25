"""
Configuration module for the visualization server.
Contains constants, settings, and pipeline configuration.
"""

from datetime import datetime

# Flask configuration
FLASK_SECRET_KEY = 'your-secret-key'
FLASK_HOST = '0.0.0.0'
FLASK_PORT = 5000

# Pipeline steps configuration (simplified for Data Sniffing only)
PIPELINE_STEPS = [
    {
        'id': 'data_sniffing',
        'name': 'Data Sniffing',
        'agent': 'DataSniffingAgent',
        'description': 'Analyzing and understanding the input data structure',
        'status': 'pending'
    }
]

# Default meta instruction
DEFAULT_META_INSTRUCTION = """You are provided a folder loaded with some data files. I want you to integrate the data from the data folder into my system stack. Make sure the data is integrated into the stack. Consider all the data provided, all the information need to be integrated into the stack."""

# Initial pipeline state
def get_initial_pipeline_state():
    """Get the initial pipeline state."""
    return {
        'is_running': False,
        'current_task': None,
        'start_time': None,
        'current_agent': None,
        'conversation': [],
        'data_sniffing_report': None,
        'meta_instruction': DEFAULT_META_INSTRUCTION
    }

# Agent status constants
class AgentStatus:
    IDLE = 'idle'
    RUNNING = 'running'
    COMPLETED = 'completed'
    ERROR = 'error'

# Message types
class MessageType:
    SYSTEM = 'system'
    USER = 'user'
    AGENT = 'agent'
    REPORT = 'report'

# Socket events
class SocketEvents:
    CONNECT = 'connect'
    DISCONNECT = 'disconnect'
    CONNECTED = 'connected'
    CONVERSATION_UPDATE = 'conversation_update'
    AGENT_STATUS_UPDATE = 'agent_status_update'
    DATA_SNIFFING_REPORT = 'data_sniffing_report' 