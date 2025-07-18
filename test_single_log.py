#!/usr/bin/env python3
"""
Test script to demonstrate the single log file system.
All logs go to data/log/agent.log
"""

import asyncio
from src.utils.global_logger import get_logger, initialize_logging, mcp_tool_logger

async def test_single_log():
    """Test the single log file system."""
    
    # Initialize logging system (creates agent.log)
    initialize_logging()
    
    # Get multiple loggers - all will log to the same agent.log file
    logger1 = get_logger("test", "component1")
    logger2 = get_logger("test", "component2")
    logger3 = get_logger("test", "component3")
    
    # All these logs go to the same agent.log file
    logger1.info("This is from component1")
    logger2.info("This is from component2")
    logger3.info("This is from component3")
    
    # Test MCP tool logging
    @mcp_tool_logger
    def test_tool(param: str) -> str:
        return f"Processed: {param}"
    
    # This will be logged to agent.log
    result = test_tool("test parameter")
    
    logger1.info("Single log file test completed")
    logger2.info("All logs are in data/log/agent.log")

if __name__ == "__main__":
    asyncio.run(test_single_log()) 