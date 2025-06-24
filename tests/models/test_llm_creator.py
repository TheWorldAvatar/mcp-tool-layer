import pytest
from unittest.mock import patch, MagicMock
from models.LLMCreator import LLMCreator
from models.ModelConfig import ModelConfig


class TestLLMCreator:
    """Test suite for the LLMCreator class."""

    def test_init_default_params(self):
        """Test initialization with default parameters."""
        with patch('models.LLMCreator.load_dotenv'):
            creator = LLMCreator()
            
            assert creator.model == "gpt-4o-mini"
            assert creator.remote_model is True
            assert creator.structured_output is False
            assert creator.structured_output_schema is None
            assert creator.config is None

    def test_init_custom_params(self):
        """Test initialization with custom parameters."""
        custom_config = ModelConfig(temperature=0.7)
        schema = {"type": "object", "properties": {"result": {"type": "string"}}}
        
        with patch('models.LLMCreator.load_dotenv'):
            creator = LLMCreator(
                model="claude-3-opus",
                remote_model=False,
                model_config=custom_config,
                structured_output=True,
                structured_output_schema=schema
            )
            
            assert creator.model == "claude-3-opus"
            assert creator.remote_model is False
            assert creator.structured_output is True
            assert creator.structured_output_schema == schema
            assert creator.config == custom_config

    @patch('models.LLMCreator.os.environ.get')
    def test_load_api_key_from_env_remote(self, mock_env_get):
        """Test loading API key for remote model."""
        mock_env_get.side_effect = lambda key, default=None: {
            "REMOTE_BASE_URL": "https://api.mock.openai.com/v1",
            "REMOTE_API_KEY": "test-remote-key"
        }.get(key, default)
        
        with patch('models.LLMCreator.load_dotenv'):
            creator = LLMCreator(remote_model=True)
            
            assert creator.base_url == "https://api.mock.openai.com/v1"
            assert creator.api_key == "test-remote-key"

    @patch('models.LLMCreator.os.environ.get')
    def test_load_api_key_from_env_local(self, mock_env_get):
        """Test loading API key for local model."""
        mock_env_get.side_effect = lambda key, default=None: {
            "LOCAL_BASE_URL": "http://localhost:8000",
            "LOCAL_API_KEY": "test-local-key"
        }.get(key, default)
        
        with patch('models.LLMCreator.load_dotenv'):
            creator = LLMCreator(remote_model=False)
            
            assert creator.base_url == "http://localhost:8000"
            assert creator.api_key == "test-local-key"

    @patch('models.LLMCreator.ChatOpenAI')
    def test_setup_llm_basic(self, mock_chat_openai):
        """Test setting up LLM without structured output."""
        mock_llm_instance = MagicMock()
        mock_chat_openai.return_value = mock_llm_instance
        
        custom_config = ModelConfig(temperature=0.5, max_tokens=100)
        
        with patch('models.LLMCreator.load_dotenv'):
            with patch('models.LLMCreator.os.environ.get', return_value="test-value"):
                creator = LLMCreator(model_config=custom_config)
                result = creator.setup_llm()
                
                call_args = mock_chat_openai.call_args.kwargs
                assert call_args["model"] == "gpt-4o-mini"
                assert call_args["base_url"] == "test-value"
                assert call_args["api_key"] == "test-value"
                assert call_args["temperature"] == 0.5
                assert result == mock_llm_instance

 

    def test_get_model_info_with_config(self):
        """Test getting model info with configuration."""
        custom_config = ModelConfig(temperature=0.8, max_tokens=200)
        
        with patch('models.LLMCreator.load_dotenv'):
            with patch('models.LLMCreator.os.environ.get', return_value="test-url"):
                creator = LLMCreator(
                    model="gpt-4",
                    remote_model=False,
                    model_config=custom_config
                )
                info = creator.get_model_info()
                
                assert info["model_name"] == "gpt-4"
                assert info["remote"] is False
                assert info["base_url"] == "test-url"
                assert info["config"]["temperature"] == 0.8
                assert info["config"]["max_tokens"] == 200

    def test_get_model_info_without_config(self):
        """Test getting model info without configuration."""
        with patch('models.LLMCreator.load_dotenv'):
            with patch('models.LLMCreator.os.environ.get', return_value="test-url"):
                creator = LLMCreator()
                info = creator.get_model_info()
                
                assert info["model_name"] == "gpt-4o-mini"
                assert info["remote"] is True
                assert info["base_url"] == "test-url"
                assert info["config"] == {}

    @patch('models.LLMCreator.os.environ.get')
    def test_load_api_key_from_env_missing_key(self, mock_env_get):
        """Test loading API key when key is missing from environment."""
        mock_env_get.return_value = None
        
        with patch('models.LLMCreator.load_dotenv'):
            creator = LLMCreator()
            result = creator.load_api_key_from_env("MISSING_KEY")
            
            assert result is None
            # Check that the specific call to MISSING_KEY was made, not just the total count
            mock_env_get.assert_any_call("MISSING_KEY", None)
