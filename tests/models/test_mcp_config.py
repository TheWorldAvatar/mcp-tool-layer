import pytest
import json
import os
import asyncio
from unittest.mock import patch, MagicMock, mock_open, AsyncMock
from models.MCPConfig import MCPConfig


class TestMCPConfig:
    """Test suite for the MCPConfig class."""

    @patch('models.MCPConfig.CONFIGS_DIR', '/test/configs')
    @patch('builtins.open', new_callable=mock_open)
    @patch('json.load')
    def test_init_successful(self, mock_json_load, mock_file_open):
        """Test successful initialization with valid config file."""
        mock_config = {
            "github": {"server": "github-server", "port": 8080},
            "filesystem": {"server": "fs-server", "port": 8081}
        }
        mock_json_load.return_value = mock_config
        
        config = MCPConfig()
        
        assert config.mcp_configs == mock_config
        # Use os.path.join to handle cross-platform path separators
        expected_path = os.path.join('/test/configs', 'mcp_configs.json')
        mock_file_open.assert_called_once_with(expected_path, 'r')
        mock_json_load.assert_called_once()

    @patch('models.MCPConfig.CONFIGS_DIR', '/test/configs')
    @patch('builtins.open', side_effect=FileNotFoundError())
    def test_init_file_not_found(self, mock_file_open):
        """Test initialization when config file is not found."""
        with pytest.raises(FileNotFoundError) as excinfo:
            MCPConfig()
        
        # Use os.path.join to handle cross-platform path separators
        expected_path = os.path.join('/test/configs', 'mcp_configs.json')
        assert f"MCP config file not found at {expected_path}" in str(excinfo.value)
        assert "Please copy mcp_configs.json.example to mcp_configs.json" in str(excinfo.value)

    @patch('models.MCPConfig.CONFIGS_DIR', '/test/configs')
    @patch('builtins.open', new_callable=mock_open, read_data='invalid json')
    def test_init_invalid_json(self, mock_file_open):
        """Test initialization with invalid JSON format."""
        with pytest.raises(ValueError) as excinfo:
            MCPConfig()
        
        assert "Invalid JSON format in MCP config file" in str(excinfo.value)

    @patch('models.MCPConfig.CONFIGS_DIR', '/test/configs')
    @patch('builtins.open', side_effect=PermissionError("Permission denied"))
    def test_init_permission_error(self, mock_file_open):
        """Test initialization with permission error."""
        with pytest.raises(Exception) as excinfo:
            MCPConfig()
        
        assert "Error loading MCP config file" in str(excinfo.value)
        assert "Permission denied" in str(excinfo.value)

    @pytest.mark.asyncio
    @patch('asyncio.create_subprocess_exec')
    async def test_is_docker_running_true(self, mock_create_subprocess):
        """Test Docker running check when Docker is running."""
        # Mock successful Docker info command
        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"docker info output", b""))
        mock_create_subprocess.return_value = mock_process
        
        config = MCPConfig()
        # Mock the config loading to avoid file dependency
        config.mcp_configs = {}
        
        result = await config.is_docker_running()
        
        assert result is True
        mock_create_subprocess.assert_called_once_with(
            'docker', 'info',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

    @pytest.mark.asyncio
    @patch('asyncio.create_subprocess_exec')
    async def test_is_docker_running_false(self, mock_create_subprocess):
        """Test Docker running check when Docker is not running."""
        # Mock failed Docker info command
        mock_process = AsyncMock()
        mock_process.returncode = 1
        mock_process.communicate = AsyncMock(return_value=(b"", b"docker: command not found"))
        mock_create_subprocess.return_value = mock_process
        
        config = MCPConfig()
        # Mock the config loading to avoid file dependency
        config.mcp_configs = {}
        
        result = await config.is_docker_running()
        
        assert result is False
        mock_create_subprocess.assert_called_once_with(
            'docker', 'info',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

    @pytest.mark.asyncio
    @patch('asyncio.create_subprocess_exec')
    async def test_is_docker_running_exception(self, mock_create_subprocess):
        """Test Docker running check when subprocess creation fails."""
        # The current implementation doesn't handle FileNotFoundError from create_subprocess_exec
        # So we expect it to raise the exception
        mock_create_subprocess.side_effect = FileNotFoundError("docker command not found")
        
        config = MCPConfig()
        # Mock the config loading to avoid file dependency
        config.mcp_configs = {}
        
        with pytest.raises(FileNotFoundError) as excinfo:
            await config.is_docker_running()
        
        assert "docker command not found" in str(excinfo.value)

    def test_get_config_single_tool(self):
        """Test getting config for a single MCP tool."""
        config = MCPConfig()
        # Mock the config loading to avoid file dependency
        config.mcp_configs = {
            "github": {"server": "github-server", "port": 8080},
            "filesystem": {"server": "fs-server", "port": 8081},
            "terminal": {"server": "term-server", "port": 8082}
        }
        
        result = config.get_config(["github"])
        
        assert result == {"github": {"server": "github-server", "port": 8080}}

    def test_get_config_multiple_tools(self):
        """Test getting config for multiple MCP tools."""
        config = MCPConfig()
        # Mock the config loading to avoid file dependency
        config.mcp_configs = {
            "github": {"server": "github-server", "port": 8080},
            "filesystem": {"server": "fs-server", "port": 8081},
            "terminal": {"server": "term-server", "port": 8082}
        }
        
        result = config.get_config(["github", "filesystem"])
        
        expected = {
            "github": {"server": "github-server", "port": 8080},
            "filesystem": {"server": "fs-server", "port": 8081}
        }
        assert result == expected

    def test_get_config_nonexistent_tool(self):
        """Test getting config for a tool that doesn't exist."""
        config = MCPConfig()
        # Mock the config loading to avoid file dependency
        config.mcp_configs = {
            "github": {"server": "github-server", "port": 8080},
            "filesystem": {"server": "fs-server", "port": 8081}
        }
        
        result = config.get_config(["nonexistent"])
        
        assert result == {}

    def test_get_config_mixed_existent_nonexistent(self):
        """Test getting config with mix of existing and non-existing tools."""
        config = MCPConfig()
        # Mock the config loading to avoid file dependency
        config.mcp_configs = {
            "github": {"server": "github-server", "port": 8080},
            "filesystem": {"server": "fs-server", "port": 8081}
        }
        
        result = config.get_config(["github", "nonexistent", "filesystem"])
        
        expected = {
            "github": {"server": "github-server", "port": 8080},
            "filesystem": {"server": "fs-server", "port": 8081}
        }
        assert result == expected

    def test_get_config_empty_list(self):
        """Test getting config with empty tool list."""
        config = MCPConfig()
        # Mock the config loading to avoid file dependency
        config.mcp_configs = {
            "github": {"server": "github-server", "port": 8080},
            "filesystem": {"server": "fs-server", "port": 8081}
        }
        
        result = config.get_config([])
        
        assert result == {}

    def test_get_config_empty_configs(self):
        """Test getting config when no configs are loaded."""
        config = MCPConfig()
        # Mock the config loading to avoid file dependency
        config.mcp_configs = {}
        
        result = config.get_config(["github", "filesystem"])
        
        assert result == {}

    @patch('models.MCPConfig.CONFIGS_DIR', '/test/configs')
    @patch('builtins.open', new_callable=mock_open)
    @patch('json.load')
    def test_full_integration(self, mock_json_load, mock_file_open):
        """Test full integration of config loading and retrieval."""
        mock_config = {
            "github": {
                "server": "github-server",
                "port": 8080,
                "auth": {"token": "test-token"}
            },
            "filesystem": {
                "server": "fs-server", 
                "port": 8081,
                "root": "/data"
            },
            "terminal": {
                "server": "term-server",
                "port": 8082,
                "shell": "bash"
            }
        }
        mock_json_load.return_value = mock_config
        
        # Test initialization
        config = MCPConfig()
        assert config.mcp_configs == mock_config
        
        # Test config retrieval
        result = config.get_config(["github", "terminal"])
        expected = {
            "github": {
                "server": "github-server",
                "port": 8080,
                "auth": {"token": "test-token"}
            },
            "terminal": {
                "server": "term-server",
                "port": 8082,
                "shell": "bash"
            }
        }
        assert result == expected
