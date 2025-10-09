"""File operations module for dynamic MCP agent."""
import os
import shutil


def read_text(path: str) -> str:
    """Read text content from a file."""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def write_text(path: str, content: str):
    """Write text content to a file, creating directories if needed."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def clear_previous_data():
    """Clear previous execution data including memory and TTL files."""
    for p in ["memory", ".kg_memory", ".kg_state"]:
        if os.path.exists(p):
            shutil.rmtree(p)
    
    if os.path.exists("data"):
        for doi_dir in os.listdir("data"):
            doi_path = os.path.join("data", doi_dir)
            if os.path.isdir(doi_path) and doi_dir not in ['log', 'ontologies']:
                mem = os.path.join(doi_path, "memory")
                if os.path.exists(mem):
                    shutil.rmtree(mem)
                ttl1 = os.path.join(doi_path, "iteration_1.ttl")
                if os.path.exists(ttl1):
                    os.remove(ttl1)
                out = os.path.join(doi_path, "output.ttl")
                if os.path.exists(out):
                    os.remove(out)
    
    for f in ["iteration_1.ttl", "output.ttl"]:
        if os.path.exists(f):
            os.remove(f)
    
    os.makedirs(".kg_memory", exist_ok=True)
    os.makedirs(".kg_state", exist_ok=True)


def find_tasks(root="data"):
    """Find all valid task directories (DOI folders) in the data directory."""
    return [d for d in os.listdir(root)
            if os.path.isdir(os.path.join(root, d))
            and not d.startswith('.')
            and d not in ['log', 'ontologies']]

