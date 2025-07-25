# SemanticStackEngine Pipeline Visualization

A real-time Flask-based web interface for monitoring and interacting with the SemanticStackEngine pipeline. This visualization tool provides dynamic monitoring of agent progress, resource tracking, and user feedback capabilities.

**🎉 NEW: Completely refactored with modular architecture for better maintainability and testability!**

## Features

### 🎯 **Core Components**

1. **Real-time Agent Monitoring**
   - Live tracking of which agent is currently active
   - Visual indicators for agent transitions
   - Progress tracking through pipeline steps

2. **Resource File Browser**
   - Real-time display of available files from the resource database
   - File type categorization with icons
   - Dynamic updates as new resources are created

3. **Timer & Progress Tracking**
   - Elapsed time since pipeline start
   - Step-by-step progress visualization
   - Status indicators (Pending, Running, Completed, Error)

4. **Live Log Monitoring**
   - Real-time log stream from the pipeline
   - Color-coded log levels (Info, Warning, Error)
   - Auto-scrolling log display

5. **Conversation Interface**
   - **Real-time Chat**: Interactive conversation with the Data Sniffing Agent
   - **Report Display**: Data sniffing reports shown in conversation format
   - **Approval System**: One-click approval or feedback for improvement
   - **Dynamic Rerun**: Agent reruns with user feedback incorporated
   - **Transparent Process**: See exactly what the agent is doing

### 🎨 **UI Features**

- **Modern Design**: Clean, responsive interface with gradient backgrounds
- **Real-time Updates**: WebSocket-based live updates
- **Interactive Elements**: Hover effects, animations, and smooth transitions
- **Mobile Responsive**: Works on desktop and mobile devices
- **Status Indicators**: Visual badges and progress bars

## Installation

### Prerequisites

- Python 3.8+
- Access to the main SemanticStackEngine project
- Required Python packages (see requirements.txt)

### Setup

1. **Navigate to the visualization directory:**
   ```bash
   cd visualization
   ```

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Ensure the main project structure is accessible:**
   - The visualization server needs access to the main project's modules
   - Make sure you're running from the project root directory

## Usage

### Starting the Server

1. **From the project root directory:**
   ```bash
   python visualization/app.py
   ```

2. **Or navigate to visualization directory and run:**
   ```bash
   cd visualization
   python app.py
   ```

3. **Access the web interface:**
   - Open your browser and go to: `http://localhost:5000`
   - The interface will automatically connect to the pipeline monitoring system

### Using the Interface

#### **Pipeline Control**
- **Task Name**: Enter the name of the task to monitor (default: "gaussian")
- **Meta Instruction**: Provide the main instruction for the pipeline (default provided)
- **Start Pipeline**: Begin monitoring the pipeline execution
- **Stop Pipeline**: Stop the monitoring process

#### **Monitoring Dashboard**
- **Timer**: Shows elapsed time since pipeline start
- **Current Agent**: Displays which agent is currently active
- **Pipeline Steps**: Visual progress through each step
- **Available Files**: Real-time list of resources from the database

#### **Live Logs**
- **Real-time Updates**: Logs update automatically every 2 seconds
- **Color Coding**: 
  - Blue: Info messages
  - Orange: Warning messages
  - Red: Error messages
- **Auto-scroll**: New logs automatically scroll into view

#### **Data Sniffing Conversation**
- **Real-time Chat**: Interactive conversation with the Data Sniffing Agent
- **Report Display**: Data sniffing reports shown in conversation format
- **Approval Button**: One-click approval to move to next step
- **Feedback Input**: Provide specific instructions for improvement
- **Rerun Button**: Trigger agent rerun with user feedback
- **Conversation History**: Complete chat history with timestamps

## Architecture

### **NEW: Modular Structure**

The application has been completely refactored into a modular architecture for better maintainability, testability, and code organization:

```
visualization/
├── app.py                    # Slim main Flask application (REFACTORED)
├── utils/                    # NEW: Modular utility components
│   ├── __init__.py
│   ├── config.py            # Configuration and constants
│   ├── pipeline_manager.py  # Pipeline state management
│   ├── file_operations.py   # File and resource operations
│   ├── background_tasks.py  # Background agent execution
│   ├── api_handlers.py      # API route logic
│   ├── socket_handlers.py   # SocketIO event handling
│   └── initialization.py    # Application initialization
├── tests/                   # NEW: Comprehensive test suite
│   ├── __init__.py
│   ├── test_config.py
│   ├── test_pipeline_manager.py
│   └── test_file_operations.py
├── run_tests.py            # NEW: Test runner script
├── templates/              # HTML templates
│   ├── base.html
│   └── dashboard.html
├── requirements.txt
└── README.md
```

### **Modular Components**

#### **1. Configuration Module (`utils/config.py`)**
- Centralized configuration management
- Constants and settings
- Pipeline step definitions
- Message types and socket events

#### **2. Pipeline Manager (`utils/pipeline_manager.py`)**
- Thread-safe state management
- Pipeline lifecycle management
- Conversation handling
- Agent status tracking

#### **3. File Operations Manager (`utils/file_operations.py`)**
- Log file reading and management
- Data sniffing report handling
- Resource database operations
- File system monitoring

#### **4. Background Task Manager (`utils/background_tasks.py`)**
- Background agent execution
- Thread management
- SocketIO communication
- Error handling and recovery

#### **5. API Handlers (`utils/api_handlers.py`)**
- Flask route logic separation
- Request/response handling
- State management integration
- Error handling

#### **6. Socket Handlers (`utils/socket_handlers.py`)**
- SocketIO event management
- Real-time communication
- Event broadcasting
- Connection handling

#### **7. Initialization Module (`utils/initialization.py`)**
- Application startup procedures
- Resource scanning and registration
- Database initialization
- Log file setup

### **Benefits of Modular Architecture**

- **🧩 Separation of Concerns**: Each module has a single, well-defined responsibility
- **🧪 Testability**: Comprehensive unit tests for all components
- **🔧 Maintainability**: Easy to modify individual components without affecting others
- **📖 Readability**: Clean, focused code that's easy to understand
- **🔄 Reusability**: Modular components can be reused across different parts of the application
- **🛡️ Thread Safety**: Proper locking mechanisms in shared state management

### **Testing**

#### **Running Tests**

1. **Run all tests:**
   ```bash
   python run_tests.py
   ```

2. **Run specific test module:**
   ```bash
   python -m unittest tests.test_config
   python -m unittest tests.test_pipeline_manager
   python -m unittest tests.test_file_operations
   ```

3. **Run with verbose output:**
   ```bash
   python -m unittest discover -v tests/
   ```

#### **Test Coverage**

- **Configuration Module**: Tests for constants, settings, and initial states
- **Pipeline Manager**: Tests for state management, thread safety, and lifecycle
- **File Operations**: Tests for file reading, resource management, and error handling
- **Integration**: Tests for component interaction and data flow

### **API Endpoints**

- `GET /` - Main dashboard page
- `POST /api/start_pipeline` - Start pipeline monitoring
- `POST /api/stop_pipeline` - Stop pipeline monitoring
- `GET /api/pipeline_state` - Current pipeline state
- `POST /api/approve_data_sniffing` - Approve data sniffing results
- `POST /api/rerun_data_sniffing` - Rerun with feedback
- `GET /api/get_conversation` - Get conversation history
- `GET /api/agent_status` - Get agent status
- `GET /api/resources` - Get current resources
- `GET /api/logs` - Get recent logs

### **WebSocket Events**

- `connect` - Client connection
- `disconnect` - Client disconnection
- `connected` - Connection confirmation
- `conversation_update` - Conversation updates
- `agent_status_update` - Agent status changes
- `data_sniffing_report` - New report available

## Configuration

### **Environment Variables**

The server uses the following paths from the main project:
- `DATA_LOG_DIR`: Log file directory
- `SANDBOX_TASK_DIR`: Task directory for progress tracking
- `RESOURCE_DB_PATH`: Resource database path

### **Customization**

1. **Configuration**: Edit `utils/config.py` to modify settings
2. **Port & Host**: Modify constants in `utils/config.py`
3. **Pipeline Steps**: Update `PIPELINE_STEPS` in config
4. **Styling**: Modify CSS in `templates/base.html` for custom appearance

## Development

### **Adding New Features**

1. **New API Endpoint**: Add handler method to `APIHandlers` class
2. **New SocketIO Event**: Add handler method to `SocketHandlers` class
3. **New State Management**: Extend `PipelineManager` class
4. **New File Operations**: Extend `FileOperationsManager` class

### **Code Quality**

- **Type Hints**: All modules use comprehensive type hints
- **Documentation**: Detailed docstrings for all classes and methods
- **Error Handling**: Robust error handling throughout the application
- **Logging**: Comprehensive logging for debugging and monitoring

## Troubleshooting

### **Common Issues**

1. **Import Errors**
   - Ensure you're running from the correct directory
   - Check that all main project modules are accessible
   - Verify Python path includes the project root

2. **Test Failures**
   - Run tests individually to identify specific issues
   - Check for missing dependencies
   - Verify mock configurations in test setup

3. **Module Import Issues**
   - Ensure all `__init__.py` files are present
   - Check relative import paths in modules
   - Verify parent directory is in Python path

4. **Connection Issues**
   - Verify the server is running on the correct port
   - Check firewall settings if accessing remotely
   - Ensure SocketIO connections are properly established

### **Debug Mode**

Run the server in debug mode for detailed error messages:
```python
# In app.py, change the debug parameter to True:
socketio.run(app, host=FLASK_HOST, port=FLASK_PORT, debug=True)
```

## Integration with DataSniffingAgent

### **How it Works**

1. **Background Execution**: Uses `BackgroundTaskManager` for agent execution
2. **State Management**: `PipelineManager` tracks agent status and progress
3. **File Monitoring**: `FileOperationsManager` monitors for report generation
4. **Real-time Updates**: SocketIO broadcasts progress to connected clients
5. **User Feedback**: Integrated feedback system for agent improvement

### **Agent Integration Flow**

1. **Start Pipeline**: `api_handlers.start_pipeline()` initiates execution
2. **Background Task**: `background_task_manager.run_agent_in_background()`
3. **Monitor Progress**: File system monitoring for progress indicators
4. **Status Updates**: Real-time status broadcasts via SocketIO
5. **Report Generation**: Automatic detection of generated reports
6. **User Interaction**: Approval/feedback system for iterative improvement

## Security Considerations

- **Local Access Only**: By default, the server runs on localhost
- **No Authentication**: Designed for local development use
- **File System Access**: Only reads from specified directories
- **Database Access**: Read-only access to resource database
- **Thread Safety**: Proper locking mechanisms for concurrent access

## Performance

- **Lightweight**: Minimal resource usage with efficient modular design
- **Thread Safe**: Concurrent operation support with proper locking
- **Scalable**: Modular architecture supports easy scaling
- **Memory Efficient**: Efficient state management and resource handling
- **Testable**: Comprehensive test coverage ensures reliability

## Future Enhancements

- **Authentication System**: User login and role-based access
- **Advanced Analytics**: Performance metrics and charts
- **Export Functionality**: Download logs and reports
- **Mobile App**: Native mobile application
- **Plugin System**: Extensible monitoring capabilities
- **Configuration UI**: Web-based configuration management

## Contributing

### **Development Workflow**

1. **Add Features**: Follow the modular architecture patterns
2. **Write Tests**: Add comprehensive tests for new functionality
3. **Update Documentation**: Keep README and docstrings current
4. **Run Tests**: Ensure all tests pass before committing

### **Code Standards**

- Follow existing code style and patterns
- Add type hints for all new functions and methods
- Include comprehensive docstrings
- Add unit tests for new functionality

## Support

For issues or questions:
1. Check the troubleshooting section above
2. Run the test suite to identify specific issues
3. Review module documentation and docstrings
4. Check log files for error messages
5. Ensure all dependencies are properly installed

---

**Note**: This visualization server uses a modular architecture for better maintainability and testability. The refactored design separates concerns, improves code organization, and provides comprehensive test coverage while maintaining all original functionality. 