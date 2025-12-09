# Pipeline Utilities

This folder contains modular utility functions used by the generic pipeline system.

## Modules

### `hash.py`
DOI hashing utilities.

**Functions:**
- `generate_hash(doi: str) -> str` - Generate 8-character hash from DOI

### `config.py`
Configuration loading utilities.

**Functions:**
- `load_config(config_path: str) -> dict` - Load pipeline configuration from JSON

### `discovery.py`
DOI discovery and mapping utilities.

**Functions:**
- `load_doi_mapping(data_dir: str = "data") -> dict` - Load DOI to hash mapping
- `discover_dois(input_dir: str, data_dir: str = "data") -> dict` - Discover DOIs from PDFs

### `file_ops.py`
File operation utilities.

**Functions:**
- `copy_pdfs_to_data_dir(doi: str, doi_hash: str, input_dir: str, data_dir: str = "data") -> bool` - Copy PDFs to data directory

### `loader.py`
Module loading utilities.

**Functions:**
- `load_step_module(step_name: str)` - Dynamically load a pipeline step module

## Usage

All utilities are exported from the package root:

```python
from src.pipelines.utils import (
    generate_hash,
    load_config,
    load_doi_mapping,
    discover_dois,
    copy_pdfs_to_data_dir,
    load_step_module,
)

# Generate hash
doi_hash = generate_hash("10.1021.acs.chemmater.0c01965")

# Load config
config = load_config("configs/pipeline.json")

# Discover DOIs
mapping = discover_dois("raw_data", "data")

# Load step module
pdf_module = load_step_module("pdf_conversion")
```

## Design Principles

1. **Single Responsibility**: Each module handles one concern
2. **No Side Effects**: Functions are pure where possible
3. **Clear Interfaces**: Simple, well-documented APIs
4. **Error Handling**: Graceful failures with clear messages
5. **Testability**: Easy to unit test in isolation

## Adding New Utilities

1. Create a new `.py` file in this directory
2. Implement your utility functions
3. Export them in `__init__.py`
4. Document in this README

Example:

```python
# src/pipelines/utils/my_util.py
def my_function(arg: str) -> str:
    """Do something useful."""
    return arg.upper()

# src/pipelines/utils/__init__.py
from .my_util import my_function
__all__ = [..., 'my_function']
```

