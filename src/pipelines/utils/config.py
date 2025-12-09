"""Configuration loading utilities"""

import json
import sys


def load_config(config_path: str) -> dict:
    """
    Load pipeline configuration from JSON file.
    
    Args:
        config_path: Path to pipeline configuration JSON
        
    Returns:
        Configuration dictionary
        
    Raises:
        SystemExit: If config cannot be loaded
    """
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"❌ Failed to load config from {config_path}: {e}")
        sys.exit(1)

