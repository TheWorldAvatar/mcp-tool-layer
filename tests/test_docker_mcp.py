import pytest
import json
from unittest.mock import patch, AsyncMock
from src.mcp_servers.docker_mcp import check_docker_status, list_containers, create_container, DockerOperations, DockerContainer


class TestDockerMCP:
    """Test suite for Docker MCP server tools."""

    @pytest.mark.asyncio
    @patch('src.mcp_servers.docker_mcp.DockerOperations.is_docker_running')
    async def test_check_docker_status_true(self, mock_is_running):
        """Test Docker status check when Docker is running."""
        mock_is_running.return_value = True
        
        result = await check_docker_status()
        
        assert result == "Docker is running: True"
        mock_is_running.assert_called_once()

    @pytest.mark.asyncio
    @patch('src.mcp_servers.docker_mcp.DockerOperations.is_docker_running')
    async def test_check_docker_status_false(self, mock_is_running):
        """Test Docker status check when Docker is not running."""
        mock_is_running.return_value = False
        
        result = await check_docker_status()
        
        assert result == "Docker is running: False"
        mock_is_running.assert_called_once()

    @pytest.mark.asyncio
    @patch('src.mcp_servers.docker_mcp.DockerOperations.list_containers')
    async def test_list_containers(self, mock_list_containers):
        """Test listing containers."""
        mock_containers = [
            DockerContainer(
                id="abc123",
                name="test-container",
                image="nginx:alpine",
                command="nginx -g 'daemon off;'",
                status="Up 2 hours",
                ports="0.0.0.0:8080->80/tcp",
                created="2024-01-01 10:00:00"
            )
        ]
        mock_list_containers.return_value = mock_containers
        
        result = await list_containers(all_containers=False)
        
        # Parse the JSON result
        container_list = json.loads(result)
        assert len(container_list) == 1
        assert container_list[0]["id"] == "abc123"
        assert container_list[0]["name"] == "test-container"
        assert container_list[0]["image"] == "nginx:alpine"
        mock_list_containers.assert_called_once_with(False)

    @pytest.mark.asyncio
    @patch('src.mcp_servers.docker_mcp.DockerOperations.create_container')
    async def test_create_container_success(self, mock_create_container):
        """Test successful container creation."""
        mock_create_container.return_value = "abc123def456"
        
        result = await create_container(
            image="nginx:alpine",
            name="web-server",
            ports={"8080": "80"}
        )
        
        assert result == "Container created successfully: abc123def456"
        mock_create_container.assert_called_once_with(
            image="nginx:alpine",
            name="web-server",
            command=None,
            ports={"8080": "80"},
            volumes=None,
            environment=None,
            detach=True
        )

    @pytest.mark.asyncio
    @patch('src.mcp_servers.docker_mcp.DockerOperations.create_container')
    async def test_create_container_failure(self, mock_create_container):
        """Test failed container creation."""
        mock_create_container.return_value = None
        
        result = await create_container(image="invalid-image")
        
        assert result == "Failed to create container"
        mock_create_container.assert_called_once()

    @pytest.mark.asyncio
    @patch('src.mcp_servers.docker_mcp.DockerOperations.list_containers')
    async def test_list_containers_empty(self, mock_list_containers):
        """Test listing containers when no containers exist."""
        mock_list_containers.return_value = []
        
        result = await list_containers(all_containers=True)
        
        container_list = json.loads(result)
        assert container_list == []
        mock_list_containers.assert_called_once_with(True)

    @pytest.mark.asyncio
    @patch('subprocess.run')
    async def test_docker_operations_is_docker_running_true(self, mock_run):
        """Test DockerOperations.is_docker_running when Docker is running."""
        mock_run.return_value.returncode = 0
        
        result = await DockerOperations.is_docker_running()
        
        assert result is True
        mock_run.assert_called_once_with(
            ["docker", "info"],
            capture_output=True,
            text=True,
            timeout=10
        )

    @pytest.mark.asyncio
    @patch('subprocess.run')
    async def test_docker_operations_is_docker_running_false(self, mock_run):
        """Test DockerOperations.is_docker_running when Docker is not running."""
        mock_run.return_value.returncode = 1
        
        result = await DockerOperations.is_docker_running()
        
        assert result is False

    @pytest.mark.asyncio
    @patch('subprocess.run')
    async def test_docker_operations_list_containers(self, mock_run):
        """Test DockerOperations.list_containers."""
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = (
            '{"ID":"abc123","Image":"nginx:alpine","Command":"nginx","RunningFor":"2 hours ago",'
            '"Status":"Up 2 hours","Ports":"0.0.0.0:8080->80/tcp","Names":"test-container"}\n'
        )
        
        result = await DockerOperations.list_containers(all_containers=False)
        
        assert len(result) == 1
        assert result[0].id == "abc123"
        assert result[0].name == "test-container"
        assert result[0].image == "nginx:alpine"
        mock_run.assert_called_once_with(
            ["docker", "ps", "--format", "{{json .}}"],
            capture_output=True,
            text=True
        )

    @pytest.mark.asyncio
    @patch('subprocess.run')
    async def test_docker_operations_create_container(self, mock_run):
        """Test DockerOperations.create_container."""
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "abc123def456"
        
        result = await DockerOperations.create_container(
            image="nginx:alpine",
            name="test-container",
            ports={"8080": "80"}
        )
        
        assert result == "abc123def456"
        # Verify the command was built correctly
        call_args = mock_run.call_args[0][0]
        assert "docker" in call_args[0]
        assert "run" in call_args[1]
        assert "-d" in call_args
        assert "--name" in call_args
        assert "test-container" in call_args
        assert "-p" in call_args
        assert "8080:80" in call_args
        assert "nginx:alpine" in call_args 