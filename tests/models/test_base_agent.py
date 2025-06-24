import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from models.BaseAgent import BaseAgent
from models.ModelConfig import ModelConfig


class TestBaseAgent:
    """Test suite for the BaseAgent class."""

    def test_init_default_params(self):
        """Test initialization with default parameters."""
        agent = BaseAgent()
        
        assert agent.model_name == "gpt-4o-mini"
        assert agent.remote_model is True
        assert isinstance(agent.model_config, ModelConfig)
        assert agent.mcp_tools == ["github", "filesystem"]

    def test_init_custom_params(self):
        """Test initialization with custom parameters."""
        custom_model_config = ModelConfig(temperature=0.7)
        agent = BaseAgent(
            model_name="claude-3-opus",
            remote_model=False,
            model_config=custom_model_config,
            mcp_tools=["github", "filesystem", "terminal"],
            structured_output=True,
            structured_output_schema={
                "title": "TestSchema",
                "description": "A test schema for structured output",
                "type": "object",
                "properties": {
                    "result": {
                        "type": "string",
                        "description": "The result of the operation"
                    }
                },
                "required": ["result"]
            }
        )
        
        assert agent.model_name == "claude-3-opus"
        assert agent.remote_model is False
        assert agent.model_config == custom_model_config
        assert agent.model_config.temperature == 0.7
        assert agent.mcp_tools == ["github", "filesystem", "terminal"]

    @pytest.mark.asyncio
    @patch('models.BaseAgent.LLMCreator')
    @patch('models.BaseAgent.MultiServerMCPClient')
    @patch('models.BaseAgent.create_react_agent')
    async def test_run_successful(self, mock_create_agent, mock_mcp_client, mock_llm_creator):
        """Test successful execution of the run method."""
        # Setup mocks
        mock_llm = MagicMock()
        mock_llm_creator.return_value.setup_llm.return_value = mock_llm
        
        mock_message = MagicMock()
        mock_message.content = "Task completed successfully"
        mock_message.response_metadata = {
            "model_name": "gpt-4o-mini",
            "token_usage": {
                "completion_tokens": 10,
                "prompt_tokens": 20,
                "total_tokens": 30
            }
        }
        
        mock_agent = AsyncMock()
        mock_agent.ainvoke.return_value = {
            "messages": [mock_message]
        }
        mock_create_agent.return_value = mock_agent
        
        mock_client_instance = AsyncMock()
        mock_client_instance.get_tools = AsyncMock()
        mock_client_instance.get_tools.return_value = ["tool1", "tool2"]
        mock_mcp_client.return_value = mock_client_instance
        
        # Setup agent with mocked docker check
        agent = BaseAgent()
        agent.mcp_config.is_docker_running = AsyncMock()
        agent.mcp_config.is_docker_running.return_value = True
        agent.mcp_config.get_config = MagicMock(return_value={"server_config": "test"})
        
        # Run the agent
        result, metadata = await agent.run("Perform this task")
        
        # Assertions
        assert result == "Task completed successfully"
        assert metadata["model_name"] == "gpt-4o-mini"
        assert metadata["completion_tokens"] == 10
        assert metadata["prompt_tokens"] == 20
        assert metadata["total_tokens"] == 30
        mock_llm_creator.assert_called_once()
        mock_mcp_client.assert_called_once()
        mock_create_agent.assert_called_once()
        mock_agent.ainvoke.assert_called_once_with({"messages": [("user", "Perform this task")]})

    @pytest.mark.asyncio
    @patch('models.BaseAgent.LLMCreator')
    @patch('models.BaseAgent.MultiServerMCPClient')
    @patch('models.BaseAgent.create_react_agent')
    async def test_run_with_recursion_limit(self, mock_create_agent, mock_mcp_client, mock_llm_creator):
        """Test run method with recursion limit parameter."""
        # Setup mocks
        mock_llm = MagicMock()
        mock_llm_creator.return_value.setup_llm.return_value = mock_llm
        
        mock_message = MagicMock()
        mock_message.content = "Task completed with recursion limit"
        mock_message.response_metadata = {
            "model_name": "gpt-4o-mini",
            "token_usage": {
                "completion_tokens": 5,
                "prompt_tokens": 15,
                "total_tokens": 20
            }
        }
        
        mock_agent = AsyncMock()
        mock_agent.ainvoke.return_value = {
            "messages": [mock_message]
        }
        mock_create_agent.return_value = mock_agent
        
        mock_client_instance = AsyncMock()
        mock_client_instance.get_tools = AsyncMock()
        mock_client_instance.get_tools.return_value = ["tool1", "tool2"]
        mock_mcp_client.return_value = mock_client_instance
        
        # Setup agent with mocked docker check
        agent = BaseAgent()
        agent.mcp_config.is_docker_running = AsyncMock()
        agent.mcp_config.is_docker_running.return_value = True
        agent.mcp_config.get_config = MagicMock(return_value={"server_config": "test"})
        
        # Run the agent with recursion limit
        result, metadata = await agent.run("Perform this task", recursion_limit=5)
        
        # Assertions
        assert result == "Task completed with recursion limit"
        mock_agent.ainvoke.assert_called_once_with(
            {"messages": [("user", "Perform this task")]}, 
            {"recursion_limit": 5}
        )

    @pytest.mark.asyncio
    async def test_docker_not_running(self):
        """Test behavior when Docker is not running."""
        agent = BaseAgent()
        agent.mcp_config.is_docker_running = AsyncMock(return_value=False)
        
        with pytest.raises(Exception) as excinfo:
            await agent.run("Perform this task")
        
        assert "Docker is not running" in str(excinfo.value)

    @pytest.mark.asyncio
    @patch('models.BaseAgent.LLMCreator')
    @patch('models.BaseAgent.MultiServerMCPClient')
    @patch('models.BaseAgent.create_react_agent')
    async def test_agent_error_propagation(self, mock_create_agent, mock_mcp_client, mock_llm_creator):
        """Test that errors from the agent are properly propagated."""
        # Setup mocks
        mock_llm = MagicMock()
        mock_llm_creator.return_value.setup_llm.return_value = mock_llm
        
        mock_agent = AsyncMock()
        mock_agent.ainvoke.side_effect = ValueError("Agent error")
        mock_create_agent.return_value = mock_agent
        
        mock_client_instance = AsyncMock()
        mock_client_instance.get_tools.return_value = []
        mock_mcp_client.return_value = mock_client_instance
        
        # Setup agent with mocked docker check
        agent = BaseAgent()
        agent.mcp_config.is_docker_running = AsyncMock(return_value=True)
        agent.mcp_config.get_config = MagicMock(return_value={"server_config": "test"})
        
        # Run the agent and expect error
        with pytest.raises(ValueError) as excinfo:
            await agent.run("Perform this task")
        
        assert "Agent error" in str(excinfo.value)

    @pytest.mark.asyncio
    @patch('models.BaseAgent.LLMCreator')
    @patch('models.BaseAgent.MultiServerMCPClient')
    @patch('models.BaseAgent.create_react_agent')
    async def test_different_mcp_tools(self, mock_create_agent, mock_mcp_client, mock_llm_creator):
        """Test using different MCP tools."""
        # Setup mocks
        mock_llm = MagicMock()
        mock_llm_creator.return_value.setup_llm.return_value = mock_llm
        
        mock_message = MagicMock()
        mock_message.content = "Task completed with custom tools"
        mock_message.response_metadata = {
            "model_name": "gpt-4o-mini",
            "token_usage": {
                "completion_tokens": 8,
                "prompt_tokens": 12,
                "total_tokens": 20
            }
        }
        
        mock_agent = AsyncMock()
        mock_agent.ainvoke.return_value = {
            "messages": [mock_message]
        }
        mock_create_agent.return_value = mock_agent
        
        mock_client_instance = AsyncMock()
        mock_client_instance.get_tools = AsyncMock()
        mock_client_instance.get_tools.return_value = ["custom_tool1", "custom_tool2"]
        mock_mcp_client.return_value = mock_client_instance
        
        # Setup agent with custom tools and mocked docker check
        agent = BaseAgent(mcp_tools=["terminal", "custom_tool"])
        agent.mcp_config.is_docker_running = AsyncMock()
        agent.mcp_config.is_docker_running.return_value = True
        
        # Mock the get_config method
        agent.mcp_config.get_config = MagicMock()
        
        # Run the agent
        result, metadata = await agent.run("Use custom tools")
        
        # Assertions
        assert result == "Task completed with custom tools"
        agent.mcp_config.get_config.assert_called_once_with(["terminal", "custom_tool"])

    @pytest.mark.asyncio
    @patch('models.BaseAgent.LLMCreator')
    @patch('models.BaseAgent.MultiServerMCPClient')
    @patch('models.BaseAgent.create_react_agent')
    async def test_metadata_extraction(self, mock_create_agent, mock_mcp_client, mock_llm_creator):
        """Test that metadata is properly extracted from the response."""
        # Setup mocks
        mock_llm = MagicMock()
        mock_llm_creator.return_value.setup_llm.return_value = mock_llm
        
        mock_message = MagicMock()
        mock_message.content = "Test response"
        mock_message.response_metadata = {
            "model_name": "claude-3-opus",
            "token_usage": {
                "completion_tokens": 25,
                "prompt_tokens": 50,
                "total_tokens": 75
            }
        }
        
        mock_agent = AsyncMock()
        mock_agent.ainvoke.return_value = {
            "messages": [mock_message]
        }
        mock_create_agent.return_value = mock_agent
        
        mock_client_instance = AsyncMock()
        mock_client_instance.get_tools = AsyncMock()
        mock_client_instance.get_tools.return_value = ["tool1"]
        mock_mcp_client.return_value = mock_client_instance
        
        # Setup agent
        agent = BaseAgent()
        agent.mcp_config.is_docker_running = AsyncMock(return_value=True)
        agent.mcp_config.get_config = MagicMock(return_value={"server_config": "test"})
        
        # Run the agent
        result, metadata = await agent.run("Test task")
        
        # Assertions
        assert result == "Test response"
        assert metadata["model_name"] == "claude-3-opus"
        assert metadata["completion_tokens"] == 25
        assert metadata["prompt_tokens"] == 50
        assert metadata["total_tokens"] == 75
        assert metadata["response_metadata"] == mock_message.response_metadata
        assert metadata["token_usage"] == mock_message.response_metadata["token_usage"]

    @pytest.mark.asyncio
    @patch('models.BaseAgent.LLMCreator')
    @patch('models.BaseAgent.MultiServerMCPClient')
    @patch('models.BaseAgent.create_react_agent')
    async def test_metadata_with_missing_token_usage(self, mock_create_agent, mock_mcp_client, mock_llm_creator):
        """Test metadata extraction when token_usage is missing."""
        # Setup mocks
        mock_llm = MagicMock()
        mock_llm_creator.return_value.setup_llm.return_value = mock_llm
        
        mock_message = MagicMock()
        mock_message.content = "Test response"
        mock_message.response_metadata = {
            "model_name": "gpt-4o-mini"
            # No token_usage
        }
        
        mock_agent = AsyncMock()
        mock_agent.ainvoke.return_value = {
            "messages": [mock_message]
        }
        mock_create_agent.return_value = mock_agent
        
        mock_client_instance = AsyncMock()
        mock_client_instance.get_tools = AsyncMock()
        mock_client_instance.get_tools.return_value = ["tool1"]
        mock_mcp_client.return_value = mock_client_instance
        
        # Setup agent
        agent = BaseAgent()
        agent.mcp_config.is_docker_running = AsyncMock(return_value=True)
        agent.mcp_config.get_config = MagicMock(return_value={"server_config": "test"})
        
        # Run the agent
        result, metadata = await agent.run("Test task")
        
        # Assertions
        assert result == "Test response"
        assert metadata["model_name"] == "gpt-4o-mini"
        assert metadata["completion_tokens"] == 0
        assert metadata["prompt_tokens"] == 0
        assert metadata["total_tokens"] == 0
        assert metadata["token_usage"] == {}
