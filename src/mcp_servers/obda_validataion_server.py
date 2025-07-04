import os
import shutil
import subprocess
from pathlib import Path
from mcp.server.fastmcp import FastMCP
import logging
from src.mcp_descriptions.obda import OBDA_VALIDATION_DESCRIPTION


mcp = FastMCP("OntopOBDAValidator")
 
def find_ontop_executable() -> str | None:
    """Return the path of 'ontop' (Linux/macOS) or 'ontop.bat' (Windows) in PATH."""
    exe_name = "ontop" if os.name == "nt" else "ontop"
    return shutil.which(exe_name)

def to_uri(p: Path) -> str:
    """Return a file:// URI for any Path (works cross-platform)."""
    return p.resolve().as_uri()

@mcp.tool(name="validate_ontop_obda", description=OBDA_VALIDATION_DESCRIPTION, tags=["obda"])
def validate_ontop_obda(
    mapping_file: str,
    ontology_file: str,
    properties_file: str
) -> dict:
    try:
        # convert file path to local path from mcp paths 
        mapping_file = mapping_file.replace("/projects/data", "data")
        ontology_file = ontology_file.replace("/projects/data", "data")
        properties_file = properties_file.replace("/projects/data", "data")


        # Convert paths to Path objects
        mapping_path = Path(mapping_file)
        ontology_path = Path(ontology_file)
        properties_path = Path(properties_file)

        # Check file existence
        missing = [p.name for p in (mapping_path, ontology_path, properties_path) if not p.is_file()]
        if missing:

            return {
                "status": "error",
                "message": f"Missing file(s): {', '.join(missing)}"
            }

        # Find Ontop CLI
        ontop = find_ontop_executable()
        if not ontop:
            return {
                "status": "error",
                "message": "Ontop CLI not found on PATH"
            }

        # Build command with URIs
        cmd = [
            ontop, "validate",
            "-m", to_uri(mapping_path),
            "-t", str(ontology_path),
            "-p", to_uri(properties_path),
        ]
        
        # Run the validation
        result = subprocess.run(cmd, capture_output=True, text=True)


        if result.returncode == 0:
            return {
                "status": "success",
                "message": "Ontop reports ontology, mapping and DB settings are consistent."
            }
        else:
            return {
                "status": "error",
                "message": f"Ontop validation failed: {result.stdout.strip()}\n{result.stderr.strip()}"
            }

    except Exception as e:
        return {
            "status": "error",
            "message": str(e)
        }

if __name__ == "__main__":
    mcp.run(transport="stdio")