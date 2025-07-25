"""
Flask server for visualizing the SemanticStackEngine pipeline.
Provides real-time monitoring of agent progress, resource tracking, and user feedback capabilities.
"""

from flask import Flask, render_template
from flask_socketio import SocketIO

# Import modular components
from utils.config import (
    FLASK_SECRET_KEY, FLASK_HOST, FLASK_PORT, PIPELINE_STEPS
)
from utils.initialization import initialize_application
from utils.api_handlers import api_handlers
from utils.socket_handlers import socket_handlers
from utils.background_tasks import background_task_manager

# Initialize application
logger = initialize_application()

# Create Flask app and SocketIO
app = Flask(__name__)
app.config['SECRET_KEY'] = FLASK_SECRET_KEY
socketio = SocketIO(app, cors_allowed_origins="*")

# Set up SocketIO for background tasks
background_task_manager.set_socketio_emit(socketio.emit)

# Routes
@app.route('/')
def dashboard():
    """Render the main dashboard."""
    return render_template('dashboard.html', pipeline_steps=PIPELINE_STEPS)

# API Routes
@app.route('/api/start_pipeline', methods=['POST'])
def start_pipeline():
    """Start the pipeline."""
    return api_handlers.start_pipeline()

@app.route('/api/stop_pipeline', methods=['POST'])
def stop_pipeline():
    """Stop the pipeline."""
    return api_handlers.stop_pipeline()

@app.route('/api/pipeline_state')
def get_pipeline_state():
    """Get pipeline state."""
    return api_handlers.get_pipeline_state()

@app.route('/api/approve_data_sniffing', methods=['POST'])
def approve_data_sniffing():
    """Approve data sniffing results."""
    return api_handlers.approve_data_sniffing()

@app.route('/api/rerun_data_sniffing', methods=['POST'])
def rerun_data_sniffing():
    """Rerun data sniffing with feedback."""
    return api_handlers.rerun_data_sniffing()

@app.route('/api/get_conversation')
def get_conversation():
    """Get conversation history."""
    return api_handlers.get_conversation()

@app.route('/api/agent_status')
def get_agent_status():
    """Get agent status."""
    return api_handlers.get_agent_status()

@app.route('/api/resources')
def get_resources():
    """Get resources."""
    return api_handlers.get_resources()

@app.route('/api/logs')
def get_logs():
    """Get logs."""
    return api_handlers.get_logs()

# SocketIO Events
@socketio.on('connect')
def handle_connect():
    """Handle client connection."""
    socket_handlers.handle_connect()

@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection."""
    socket_handlers.handle_disconnect()

if __name__ == '__main__':
    socketio.run(app, host=FLASK_HOST, port=FLASK_PORT, debug=False) 