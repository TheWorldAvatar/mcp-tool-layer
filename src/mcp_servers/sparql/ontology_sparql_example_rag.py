import json
import os
import fcntl
import time
from typing import Literal, Dict, List, Optional
from pathlib import Path
from models.locations import SPARQL_EXAMPLE_REPO_DIR
 
# Constants
SPARQL_EXAMPLES_FILE = "sparql_examples.json"
VALID_ONTOLOGIES = ["ontosynthesis", "ontomops", "ontospecies"]

def _get_file_path() -> Path:
    """Get the path to the SPARQL examples JSON file."""
    return Path(SPARQL_EXAMPLE_REPO_DIR) / SPARQL_EXAMPLES_FILE

def _ensure_file_exists():
    """Create the JSON file if it doesn't exist or is corrupted."""
    file_path = _get_file_path()
    # Ensure directory exists
    file_path.parent.mkdir(parents=True, exist_ok=True)
    
    if not file_path.exists():
        _write_examples([])
    else:
        # Check if file is valid JSON
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
                if content.strip():
                    json.loads(content)
        except (json.JSONDecodeError, FileNotFoundError):
            # File is corrupted, recreate it
            print(f"Warning: Corrupted JSON file detected, recreating: {file_path}")
            _write_examples([])

def _repair_file():
    """Repair corrupted JSON file by creating a new one."""
    file_path = _get_file_path()
    try:
        # Backup corrupted file
        backup_file = file_path.with_suffix('.backup')
        if file_path.exists():
            file_path.rename(backup_file)
        # Create new empty file
        _write_examples([])
        print(f"File repaired: {file_path}")
        return True
    except Exception as e:
        print(f"Failed to repair file: {e}")
        return False

def _read_examples() -> List[Dict]:
    """Read examples from JSON file with file locking."""
    _ensure_file_exists()
    file_path = _get_file_path()
    max_retries = 10
    retry_delay = 0.1
    
    for attempt in range(max_retries):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                # Use blocking lock with timeout
                fcntl.flock(f.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
                try:
                    content = f.read()
                    if not content.strip():
                        return []
                    data = json.loads(content)
                    return data if isinstance(data, list) else []
                except json.JSONDecodeError as e:
                    # If JSON is corrupted, return empty list and let write fix it
                    print(f"Warning: Corrupted JSON file, returning empty list: {e}")
                    return []
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except (FileNotFoundError, PermissionError) as e:
            if attempt == max_retries - 1:
                raise Exception(f"Failed to read examples after {max_retries} attempts: {e}")
            time.sleep(retry_delay)
            retry_delay *= 1.5
        except BlockingIOError:
            # File is locked by another process
            if attempt == max_retries - 1:
                raise Exception(f"File is locked by another process after {max_retries} attempts")
            time.sleep(retry_delay)
            retry_delay *= 1.5
        except Exception as e:
            if attempt == max_retries - 1:
                raise Exception(f"Unexpected error reading examples: {e}")
            time.sleep(retry_delay)
            retry_delay *= 1.5

def _write_examples(examples: List[Dict]):
    """Write examples to JSON file with file locking."""
    file_path = _get_file_path()
    max_retries = 10
    retry_delay = 0.1
    
    # Ensure directory exists
    file_path.parent.mkdir(parents=True, exist_ok=True)
    
    for attempt in range(max_retries):
        try:
            # Write to temporary file first, then atomic move
            temp_file = file_path.with_suffix('.tmp')
            
            with open(temp_file, 'w', encoding='utf-8') as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                try:
                    json.dump(examples, f, indent=2, ensure_ascii=False)
                    f.flush()
                    os.fsync(f.fileno())  # Force write to disk
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            
            # Atomic move
            temp_file.replace(file_path)
            return
            
        except BlockingIOError:
            # File is locked by another process
            if attempt == max_retries - 1:
                raise Exception(f"File is locked by another process after {max_retries} attempts")
            time.sleep(retry_delay)
            retry_delay *= 1.5
        except (PermissionError, OSError) as e:
            if attempt == max_retries - 1:
                raise Exception(f"Failed to write examples after {max_retries} attempts: {e}")
            time.sleep(retry_delay)
            retry_delay *= 1.5
        except Exception as e:
            if attempt == max_retries - 1:
                raise Exception(f"Unexpected error writing examples: {e}")
            time.sleep(retry_delay)
            retry_delay *= 1.5
        finally:
            # Clean up temp file if it exists
            if 'temp_file' in locals() and temp_file.exists():
                try:
                    temp_file.unlink()
                except:
                    pass

def repair_sparql_examples_file() -> bool:
    """
    Public function to repair corrupted SPARQL examples file.
    
    Returns:
        True if repair was successful, False otherwise
    """
    return _repair_file()

def list_sparql_example_names_and_descriptions() -> str:
    """
    Returns a string containing all SPARQL example names, descriptions, and ontology names.
    Does not include the actual SPARQL queries.
    """
    try:
        examples = _read_examples()
        
        if not examples:
            return "No SPARQL examples found."
        
        result_lines = []
        result_lines.append("SPARQL Examples:")
        result_lines.append("=" * 50)
        
        for i, example in enumerate(examples, 1):
            name = example.get('name', 'Unknown')
            description = example.get('description', 'No description')
            ontology = example.get('ontology_name', 'Unknown')
            
            result_lines.append(f"{i}. Name: {name}")
            result_lines.append(f"   Description: {description}")
            result_lines.append(f"   Ontology: {ontology}")
            result_lines.append("")
        
        return "\n".join(result_lines)
    
    except Exception as e:
        # Try to repair file if there's an error
        print(f"Error retrieving SPARQL examples, attempting repair: {str(e)}")
        if _repair_file():
            return "File was corrupted and has been repaired. Please try again."
        return f"Error retrieving SPARQL examples: {str(e)}"


def retrieve_sparql_example(example_name: str) -> Optional[str]:
    """
    Retrieves the SPARQL query string by example name.
    
    Args:
        example_name: The name of the SPARQL example to retrieve
        
    Returns:
        The SPARQL query string if found, None otherwise
    """
    try:
        examples = _read_examples()
        
        for example in examples:
            if example.get('name') == example_name:
                return example.get('sparql')
        
        return None
    
    except Exception as e:
        print(f"Error retrieving SPARQL example '{example_name}': {str(e)}")
        return None


def insert_sparql_example(example_name: str, example_query: str, 
ontology_name: Literal["ontosynthesis", "ontomops", "ontospecies"], description: str = "", example_result: str = "") -> dict:
    """
    Creates a new SPARQL example and updates the JSON file with file locking.
    
    Args:
        example_name: Name of the SPARQL example
        example_query: The SPARQL query string
        ontology_name: The ontology this example belongs to
        description: Optional description of the example
        example_result: Optional result of the example
    Returns:
        Dictionary with status, message, and details
    """
    try:
        # Validate inputs
        if not example_name or not example_query:
            return {
                "status": "error",
                "message": "Validation failed",
                "details": "example_name and example_query cannot be empty",
                "success": False
            }
        
        if ontology_name not in VALID_ONTOLOGIES:
            return {
                "status": "error", 
                "message": "Validation failed",
                "details": f"ontology_name must be one of {VALID_ONTOLOGIES}, got: {ontology_name}",
                "success": False
            }
        
        # Read existing examples
        try:
            examples = _read_examples()
        except Exception as e:
            return {
                "status": "error",
                "message": "Failed to read existing examples",
                "details": f"Error reading file: {str(e)}",
                "success": False
            }
        
        # Check if example with same name already exists
        for example in examples:
            if example.get('name') == example_name:
                return {
                    "status": "error",
                    "message": "Duplicate example name",
                    "details": f"SPARQL example with name '{example_name}' already exists",
                    "success": False
                }
        
        # Create new example
        new_example = {
            "name": example_name,
            "description": description,
            "ontology_name": ontology_name,
            "sparql": example_query,
            "result": example_result
        }
        
        # Add to examples list
        examples.append(new_example)
        
        # Write back to file
        try:
            _write_examples(examples)
        except Exception as e:
            return {
                "status": "error",
                "message": "Failed to write examples to file",
                "details": f"File write error: {str(e)}",
                "success": False
            }
        
        return {
            "status": "success",
            "message": f"Successfully added SPARQL example: {example_name}",
            "details": {
                "example_name": example_name,
                "ontology_name": ontology_name,
                "description": description,
                "query_length": len(example_query),
                "total_examples": len(examples)
            },
            "success": True
        }
    
    except Exception as e:
        return {
            "status": "error",
            "message": "Unexpected error occurred",
            "details": f"Unexpected error: {str(e)}",
            "success": False
        }


def update_sparql_example(example_name: str, example_query: str = None, ontology_name: str = None, description: str = None, example_result: str = None) -> dict:
    """
    Updates an existing SPARQL example.
    
    Args:
        example_name: Name of the SPARQL example to update
        example_query: New SPARQL query (optional)
        ontology_name: New ontology name (optional)
        description: New description (optional)
        example_result: New example result (optional)
        
    Returns:
        Dictionary with status, message, and details
    """
    try:
        # Read existing examples
        try:
            examples = _read_examples()
        except Exception as e:
            return {
                "status": "error",
                "message": "Failed to read existing examples",
                "details": f"Error reading file: {str(e)}",
                "success": False
            }
        
        # Validate ontology_name if provided
        if ontology_name is not None and ontology_name not in VALID_ONTOLOGIES:
            return {
                "status": "error",
                "message": "Validation failed",
                "details": f"ontology_name must be one of {VALID_ONTOLOGIES}, got: {ontology_name}",
                "success": False
            }
        
        # Find and update the example
        for example in examples:
            if example.get('name') == example_name:
                updates_made = []
                
                if example_query is not None:
                    example['sparql'] = example_query
                    updates_made.append("query")
                if ontology_name is not None:
                    example['ontology_name'] = ontology_name
                    updates_made.append("ontology_name")
                if description is not None:
                    example['description'] = description
                    updates_made.append("description")
                if example_result is not None:
                    example['result'] = example_result
                    updates_made.append("result")
                
                # Write back to file
                try:
                    _write_examples(examples)
                except Exception as e:
                    return {
                        "status": "error",
                        "message": "Failed to write updated examples to file",
                        "details": f"File write error: {str(e)}",
                        "success": False
                    }
                
                return {
                    "status": "success",
                    "message": f"Successfully updated SPARQL example: {example_name}",
                    "details": {
                        "example_name": example_name,
                        "updates_made": updates_made,
                        "total_examples": len(examples)
                    },
                    "success": True
                }
        
        return {
            "status": "error",
            "message": "Example not found",
            "details": f"SPARQL example with name '{example_name}' not found",
            "success": False
        }
    
    except Exception as e:
        return {
            "status": "error",
            "message": "Unexpected error occurred",
            "details": f"Unexpected error: {str(e)}",
            "success": False
        }


def delete_sparql_example(example_name: str) -> dict:
    """
    Deletes a SPARQL example by name.
    
    Args:
        example_name: Name of the SPARQL example to delete
        
    Returns:
        Dictionary with status, message, and details
    """
    try:
        # Read existing examples
        try:
            examples = _read_examples()
        except Exception as e:
            return {
                "status": "error",
                "message": "Failed to read existing examples",
                "details": f"Error reading file: {str(e)}",
                "success": False
            }
        
        # Find and remove the example
        original_length = len(examples)
        examples = [ex for ex in examples if ex.get('name') != example_name]
        
        if len(examples) == original_length:
            return {
                "status": "error",
                "message": "Example not found",
                "details": f"SPARQL example with name '{example_name}' not found",
                "success": False
            }
        
        # Write back to file
        try:
            _write_examples(examples)
        except Exception as e:
            return {
                "status": "error",
                "message": "Failed to write updated examples to file",
                "details": f"File write error: {str(e)}",
                "success": False
            }
        
        return {
            "status": "success",
            "message": f"Successfully deleted SPARQL example: {example_name}",
            "details": {
                "deleted_example": example_name,
                "remaining_examples": len(examples),
                "examples_deleted": original_length - len(examples)
            },
            "success": True
        }
    
    except Exception as e:
        return {
            "status": "error",
            "message": "Unexpected error occurred",
            "details": f"Unexpected error: {str(e)}",
            "success": False
        }


# Convenience functions for backward compatibility
def list_sparql_example_names() -> List[str]:
    """Returns a list of all SPARQL example names."""
    try:
        examples = _read_examples()
        return [ex.get('name', '') for ex in examples if ex.get('name')]
    except Exception as e:
        print(f"Error retrieving SPARQL example names: {str(e)}")
        return []


# Example usage and testing
if __name__ == "__main__":
    # Test the functions
    print("Testing SPARQL Example Manager")
    print("=" * 40)
    
    # Insert some test examples
    insert_sparql_example(
        "demo_query_1",
        "PREFIX abc: <https://www.theworldavatar.com/example_abc> SELECT ?s ?p ?o WHERE { ?s ?p ?o . } LIMIT 10",
        "ontomops",
        "Just for testing"
    )
    
 
    
    # List all examples
    print(list_sparql_example_names_and_descriptions())
    
    # Retrieve a specific example
    query = retrieve_sparql_example("get_all_compounds")
    print(f"\nRetrieved query: {query}")
    
    