"""T-Box-instantiated A-Box mock for generated occurrence MCP packages.

The mock document is an instance of the compiled occurrence surface. It does
not encode application class names in source.
"""

from src.kg_building_mcp_generation_v2.abox_mock.harness import (
    run_covering_abox_mock,
    run_covering_abox_mock_on_package,
)

__all__ = ["run_covering_abox_mock", "run_covering_abox_mock_on_package"]
