# File Input/Output Requirements Roadmap

This document provides a comprehensive overview of all file input/output operations in the mcp-tool-layer codebase. It serves as a roadmap for upgrading the file input and output mechanisms.

## Table of Contents

1. [MCP Servers](#mcp-servers)
2. [Engines](#engines)
3. [Agents](#agents)
4. [Models](#models)
5. [Utils](#utils)
6. [Scripts](#scripts)
7. [Configuration Files](#configuration-files)
8. [Data Directories](#data-directories)
9. [File Path Handling](#file-path-handling)
10. [Upgrade Recommendations](#upgrade-recommendations)

## MCP Servers

### Generic File Operations (`src/mcp_servers/generic_file_operations.py`)

**File Input Operations:**
- `read_arbitrary_file(file_path: str)` - Reads arbitrary files (non-CSV, non-Word, non-text)
- `read_markdown_file(file_path: str)` - Reads markdown files
- `list_files_in_folder(folder_path: str)` - Lists files in folder with sizes
- CSV file reading via pandas (`pd.read_csv`)
- Word document reading via python-docx

**File Output Operations:**
- `create_new_file(file_path: str, content: str)` - Creates arbitrary files
- `code_output(code: str, task_meta_name: str, task_index: int, script_name: str)` - Outputs Python code to sandbox
- `report_output(file_path: str, file_content: str)` - Writes report files
- `text_file_truncate(file_path: str)` - Reads and truncates text files

**Path Handling:**
- Uses `handle_generic_data_file_path()` for path conversion
- Uses `remove_mnt_prefix()` for WSL path handling
- Uses `handle_sandbox_task_dir()` for sandbox task directory handling

### Resource Registration (`src/mcp_servers/resource_registration_server.py`)

**File Input Operations:**
- Reads existing `resources.json` files
- Handles JSON file loading with error handling

**File Output Operations:**
- Writes to `resources.json` in task directories
- Appends new resources to existing ones
- Creates directory structure if needed

**Path Conversion:**
- `convert_to_absolute_path()` - Converts various path formats to absolute paths
- Handles `/data/generic_data/`, `/sandbox/`, `data/generic_data/`, `sandbox/` prefixes

### Task Generation Coarse (`src/mcp_servers/task_generation_coarse_server.py`)

**File Output Operations:**
- Creates task files in `{SANDBOX_TASK_DIR}/{task_meta_name}/{iteration_number}/{task_id}.json`
- Writes `selected_task_index.json` files
- Uses file locking for concurrent access

### Task Refinement (`src/mcp_servers/task_refinement_server.py`)

**File Output Operations:**
- Creates refined task group files: `{iteration_index}_refined_task_group.json`
- Writes individual refined task files (commented out)

### Postgres Server (`src/mcp_servers/postgres_server.py`)

**File Input Operations:**
- Reads CSV files via `pd.read_csv(data_path)`
- Path conversion: `/projects/data` → `data`

**Database Operations:**
- Uploads data to PostgreSQL via `df.to_sql()`

### Stack Operations (`src/mcp_servers/stack_operations.py`)

**File Output Operations:**
- Creates `ontocompchem.json` configuration files
- Copies OBDA files to target directories
- Copies CSV files to subdirectories

### Docker MCP (`src/mcp_servers/docker_mcp.py`)

**Path Handling:**
- `correct_wsl_path()` - Corrects WSL path formatting
- Mounts volumes for sandbox and data directories

### Tool Analyzer (`src/mcp_servers/tool_analyzer.py`)

**File Input Operations:**
- Reads Python files to analyze MCP tools

**File Output Operations:**
- Writes `mcp_tools.json` configuration files

### OBDA Creation (`src/mcp_servers/obda_creation_server.py`)

**Path Handling:**
- Environment variable configuration for data paths
- Local vs MCP data root path conversion

### TTL Validation (`src/mcp_servers/ttl_validation_server.py`)

**File Input Operations:**
- Reads TTL files for validation
- Path conversion: `/projects/data` → `data`

**File Output Operations:**
- Creates ontology files in TTL format

### OBDA Validation (`src/mcp_servers/obda_validataion_server.py`)

**File Input Operations:**
- Reads mapping files, ontology files, and properties files
- Path conversion for MCP paths

### Path Conversion (`src/mcp_servers/path_conversion.py`)

**Path Conversion Operations:**
- `convert_to_wsl_path()` - Converts Windows paths to WSL paths
- Handles drive letters and backslash conversion

### LLM Generation (`src/mcp_servers/llm_generation_server.py`)

**File Output Operations:**
- Creates files in sandbox task directories

## Engines

### Task Files Utils (`src/engines/utils/task_files.py`)

**File Input Operations:**
- `load_selected_task_index()` - Reads `selected_task_index.json`
- `load_task_files()` - Loads task files with refined version preference
- `build_overall_reports()` - Reads markdown files from task directories

**File Output Operations:**
- `summarize_refined_task_files()` - Creates `refined_task_group.json` files
- `clear_task_dir()` - Clears task directories
- `delete_task_tracing_file()` - Deletes tracing files

**Directory Operations:**
- Creates directory structures
- Archives old task files

### Code Generation Engine (`src/engines/CodeGenerationEngine.py`)

**File Input Operations:**
- Reads `resources.json` files
- Reads refined task group files
- Reads task group files from task directories

**Directory Operations:**
- Removes and recreates code directories
- Lists files in task directories

### Task Revision Engine (`src/engines/TaskRevisionEngine.py`)

**File Input Operations:**
- Reads `resources.json` files
- Reads refined task group files
- Reads task summary files

**File Output Operations:**
- Writes task tracing files
- Creates workflow examination reports

### Semantic Stack Engine (`src/engines/SemanticStackEngine.py`)

**File Operations:**
- Orchestrates file operations across multiple engines
- Manages task file lifecycle

## Agents

### Task Execution Agent (`src/agents/TasKExecutionAgent.py`)

**File Input Operations:**
- Reads `selected_task_index.json`
- Reads `resources.json` files
- Reads refined task group files

**File Output Operations:**
- Writes full response collection files: `full_response_collection_{iteration_index}.md`
- Appends task responses to collection files

### Task Decomposition Agent (`src/agents/TaskDecompositionAgent.py`)

**File Input Operations:**
- Reads `data_sniffing_report.md` files

**File Output Operations:**
- Writes task summary files: `task_summary.md`
- Creates task directories and files

### Task Decomposition Coarse Agent (`src/agents/TaskDecompositionCoarseAgent.py`)

**File Output Operations:**
- Writes task decomposition reports: `{iteration}.md`
- Creates output directories

### Code Generation Agent (`src/agents/CodeGenerationAgent.py`)

**File Operations:**
- Generates Python scripts via MCP tools
- Uses resource registration for file management

### Sandbox Operation Agent (`src/agents/SandboxOperationAgent.py`)

**File Operations:**
- Tests sandbox file operations
- Creates test files in sandbox

### Meta Agent (`src/agents/MetaAgent.py`)

**File Operations:**
- Creates task decomposition markdown files

## Models

### Locations (`models/locations.py`)

**Directory Structure:**
- Defines all major directory paths
- `check_dir_exists()` - Validates directory existence
- Creates directory structure on import

**Key Directories:**
- `ROOT_DIR`, `DATA_DIR`, `SANDBOX_DIR`
- `SANDBOX_TASK_DIR`, `SANDBOX_CODE_DIR`, `SANDBOX_DATA_DIR`
- `DATA_GENERIC_DIR`, `DATA_TEST_DIR`, `DATA_LOG_DIR`

### MCP Config (`models/MCPConfig.py`)

**File Input Operations:**
- Reads MCP configuration files from `CONFIGS_DIR`
- Handles JSON configuration loading

**Path Conversion:**
- Converts Windows paths to Linux paths
- Handles path conversion for different platforms

### MCP Config Dynamic (`models/MCPConfigDynamic.py`)

**Path Handling:**
- Uses `pathlib.Path` for path operations
- Converts paths to POSIX format

### Workflow Factory (`models/WorkFlowFactory.py`)

**File Input Operations:**
- Reads DAG specification files
- `read_config()` - Reads configuration files

**File Output Operations:**
- Writes DAG Python files
- Creates static Python scripts

### Ontology (`models/Ontology.py`)

**File Output Operations:**
- Creates TTL ontology files
- Serializes RDF graphs to files

## Utils

### File Management (`src/utils/file_management.py`)

**Path Conversion Functions:**
- `handle_generic_data_file_path()` - Converts `/data/generic_data` paths
- `remove_mnt_prefix()` - Removes `/mnt/` prefixes
- `handle_sandbox_task_dir()` - Converts sandbox task paths

## Scripts

### Clean Task Directory (`scripts/clean_task_dir.py`)

**File Operations:**
- Archives existing task directories
- Creates timestamped archive folders
- Moves files to archive with timestamps

## Configuration Files

### MCP Configurations
- `configs/mcp_configs.json` - Main MCP configuration
- `configs/pretask_mcp_configs.json` - Pre-task configurations
- `configs/task_decomposition_mcp_configs.json` - Task decomposition configs
- `configs/task_refinement_mcp_configs.json` - Task refinement configs
- `configs/tas_execution_mcp_configs.json` - Task execution configs
- `configs/python_generation_mcp_configs.json` - Python generation configs

### Example Configurations
- `configs/mcp_configs.json.example`
- `configs/pretask_mcp_configs.json.example`

## Data Directories

### Input Data Locations
- `data/generic_data/` - Generic input data
- `data/test/` - Test data files
- `data/log/` - Log files

### Sandbox Directories
- `sandbox/tasks/` - Task files and outputs
- `sandbox/code/` - Generated code files
- `sandbox/data/` - Sandbox data outputs
- `sandbox/configs/` - Sandbox configurations

### Archive Directories
- `sandbox/tasks/archive/` - Archived task files

## File Path Handling

### Path Conversion Patterns
1. **MCP Paths**: `/projects/data` → `data`
2. **WSL Paths**: Windows paths → `/mnt/{drive}/{path}`
3. **Generic Data**: `/data/generic_data/` → `data/generic_data/`
4. **Sandbox Paths**: `/sandbox/tasks/` → `sandbox/tasks/`

### Path Handling Functions
- `convert_to_absolute_path()` - Resource registration
- `correct_wsl_path()` - Docker operations
- `handle_generic_data_file_path()` - Generic file operations
- `remove_mnt_prefix()` - WSL path handling
- `handle_sandbox_task_dir()` - Sandbox task paths

## Upgrade Recommendations

### 1. Centralized Path Management
- Create a unified path management system
- Standardize path conversion across all components
- Implement path validation and sanitization

### 2. File I/O Abstraction Layer
- Create a common file I/O interface
- Implement consistent error handling
- Add file type validation and security checks

### 3. Configuration Management
- Centralize configuration file handling
- Implement configuration validation
- Add configuration versioning

### 4. File Security
- Implement file access permissions
- Add file integrity checks
- Implement secure file deletion

### 5. Logging and Monitoring
- Add comprehensive file operation logging
- Implement file operation metrics
- Add file operation error tracking

### 6. Backup and Recovery
- Implement automatic file backups
- Add file recovery mechanisms
- Implement file versioning

### 7. Performance Optimization
- Implement file caching mechanisms
- Add file compression for large files
- Optimize file I/O operations

### 8. Testing Infrastructure
- Add file I/O unit tests
- Implement file operation integration tests
- Add file security tests

### 9. Documentation
- Document all file I/O operations
- Create file operation guidelines
- Add troubleshooting guides

### 10. Migration Strategy
- Plan gradual migration to new file I/O system
- Maintain backward compatibility
- Implement rollback mechanisms 