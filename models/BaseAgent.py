"""
BaseAgent is the basic class for ReAct-based atomic agents. 

It is designed to be used as standard template for creating ReAct-based atomic agents. 

The class requires the following setup parameters:
- model_name: The name of the model to use
- remote_model: Whether to use a remote model
- model_config: Configuration for the model 
- mcp_tools: List of MCP tools to use (e.g. ["github", "filesystem"])
"""


from typing import Any
import asyncio
import openai
from models.LLMCreator import LLMCreator
from models.MCPConfig import MCPConfig
from models.ModelConfig import ModelConfig
from langgraph.prebuilt import create_react_agent
from langchain_mcp_adapters.client import MultiServerMCPClient


class BaseAgent:
    def __init__(
        self,
        model_name: str = "gpt-4o-mini",
        remote_model: bool = True,
        model_config: ModelConfig = None,
        mcp_tools: list = ["github", "filesystem"],
        structured_output: bool = False,
        structured_output_schema: Any = None
    ):
        """
        Initialize the BaseAgent with configurable parameters.
        
        Args:
            model_name (str): The name of the model to use
            remote_model (bool): Whether to use a remote model
            model_config (ModelConfig): Configuration for the model
            mcp_tools (list): List of MCP tools to use
        """
        self.model_name = model_name
        self.remote_model = remote_model
        self.model_config = model_config or ModelConfig()
        self.mcp_config = MCPConfig()
        self.mcp_tools = mcp_tools
        self.llm = LLMCreator(
            model=self.model_name, 
            remote_model=self.remote_model, 
            model_config=self.model_config,
            structured_output=structured_output,
            structured_output_schema=structured_output_schema
        ).setup_llm()
        
    async def run(self, task_instruction, recursion_limit=None):
        """
        Run the agent with the given task instruction.
        
        Args:
            task_instruction (str): The instruction for the agent to execute
            
        Returns:
            str: The agent's response
        """

        
        server_configs = self.mcp_config.get_config(self.mcp_tools)
        # Check whether docker is running, if not raise an exception as MCP tools will not work and the agent
        # will enter a stale state. 
        docker_running = await self.mcp_config.is_docker_running()
        if not docker_running:
            raise Exception("Docker is not running. Please start Docker and try again.")

        # Create a MultiServerMCPClient instance, which supports the use of multiple MCP servers
        client = MultiServerMCPClient(server_configs)
            # Get the tools from the MCP client
        tools = await client.get_tools()
        # Create a ReAct agent with the LLM and tools
        agent = create_react_agent(self.llm, tools)
        
        try:
            # Invoke the agent with the task instruction
            if recursion_limit: 
                agent_response = await agent.ainvoke({"messages": [("user", task_instruction)]}, {"recursion_limit": recursion_limit})   
            else:
                agent_response = await agent.ainvoke({"messages": [("user", task_instruction)]}) 
            # Return the last message from the agent response

            # get all the keys in the agent_response
            metadata = {}
            for message in agent_response["messages"]:
                metadata["response_metadata"] = message.response_metadata
                token_usage = message.response_metadata.get("token_usage", {})
                metadata["model_name"] = message.response_metadata.get("model_name", "")
                metadata["token_usage"] = token_usage
                metadata["completion_tokens"] = token_usage.get("completion_tokens", 0)
                metadata["prompt_tokens"] = token_usage.get("prompt_tokens", 0)
                metadata["total_tokens"] = token_usage.get("total_tokens", 0)

            return agent_response["messages"][-1].content, metadata
        except Exception as e:
            raise e
        
        except openai.BadRequestError as e:
            # this is a known issue with the openai api, where the model returns a bad request error
            # this is usually due to too long a prompt. 
            print(f"BadRequestError: {e}")



if __name__ == "__main__":
    agent = BaseAgent()
    task_instruction = """

    Say hello to me. 
    """
    response, metadata = asyncio.run(agent.run(task_instruction))
    print(response)
    print(metadata)