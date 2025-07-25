#!/usr/bin/env python3
"""
Startup script for the SemanticStackEngine Pipeline Visualization Server.
This script ensures proper setup and starts the Flask server.
"""

import os
import sys
import subprocess
import time

def check_dependencies():
    """Check if required dependencies are installed."""
    required_packages = [
        'flask',
        'flask_socketio',
        'eventlet'
    ]
    
    missing_packages = []
    for package in required_packages:
        try:
            __import__(package.replace('-', '_'))
        except ImportError:
            missing_packages.append(package)
    
    if missing_packages:
        print(f"❌ Missing required packages: {', '.join(missing_packages)}")
        print("Please install them using:")
        print(f"pip install -r {os.path.join(os.path.dirname(__file__), 'requirements.txt')}")
        return False
    
    print("✅ All dependencies are installed")
    return True

def check_project_structure():
    """Check if the main project structure is accessible."""
    # Add parent directory to path
    parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, parent_dir)
    
    try:
        from models.locations import DATA_LOG_DIR, SANDBOX_TASK_DIR
        from src.utils.resource_db_operations import ResourceDBOperator
        print("✅ Main project modules are accessible")
        return True
    except ImportError as e:
        print(f"❌ Cannot access main project modules: {e}")
        print("Make sure you're running from the project root directory")
        return False

def create_directories():
    """Create necessary directories if they don't exist."""
    parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    
    directories = [
        os.path.join(parent_dir, "data", "log"),
        os.path.join(parent_dir, "sandbox", "tasks"),
        os.path.join(parent_dir, "configs")
    ]
    
    for directory in directories:
        os.makedirs(directory, exist_ok=True)
        print(f"✅ Directory ready: {directory}")

def start_server():
    """Start the Flask server."""
    print("\n🚀 Starting SemanticStackEngine Pipeline Visualization Server...")
    print("📍 Server will be available at: http://localhost:5000")
    print("📊 Dashboard will show real-time pipeline monitoring")
    print("⏹️  Press Ctrl+C to stop the server\n")
    
    try:
        # Import and run the app
        from app import app, socketio
        socketio.run(app, host='0.0.0.0', port=5000, debug=False)
    except KeyboardInterrupt:
        print("\n🛑 Server stopped by user")
    except Exception as e:
        print(f"❌ Error starting server: {e}")
        return False
    
    return True

def main():
    """Main startup function."""
    print("=" * 60)
    print("🔧 SemanticStackEngine Pipeline Visualization Server")
    print("=" * 60)
    
    # Check if we're in the right directory
    if not os.path.exists('app.py'):
        print("❌ Please run this script from the visualization directory")
        print("   cd visualization")
        print("   python start_server.py")
        return False
    
    # Run checks
    if not check_dependencies():
        return False
    
    if not check_project_structure():
        return False
    
    create_directories()
    
    # Start the server
    return start_server()

if __name__ == "__main__":
    success = main()
    if not success:
        sys.exit(1) 