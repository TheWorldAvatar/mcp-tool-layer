import os
import shutil
import subprocess
from pathlib import Path
from mcp.server.fastmcp import FastMCP
import logging

mcp = FastMCP("OntopOBDAValidator")

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Create log directory and file
log_file = Path("data/log/obda_validataion.log")
log_file.parent.mkdir(parents=True, exist_ok=True)

# Add file handler to logger
file_handler = logging.FileHandler(log_file, mode='a', encoding='utf-8')
file_handler.setLevel(logging.INFO)

# Create formatter and add it to the handler
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
file_handler.setFormatter(formatter)

# Add the handler to the logger
logger.addHandler(file_handler)

def find_ontop_executable() -> str | None:
    """Return the path of 'ontop' (Linux/macOS) or 'ontop.bat' (Windows) in PATH."""
    exe_name = "ontop" if os.name == "nt" else "ontop"
    logger.info(f"Finding Ontop executable: {exe_name}")
    logger.info(f"Ontop executable found: {shutil.which(exe_name)}")
    return shutil.which(exe_name)

def to_uri(p: Path) -> str:
    """Return a file:// URI for any Path (works cross-platform)."""
    return p.resolve().as_uri()

@mcp.tool()
def validate_ontop_obda(
    mapping_file: str,
    ontology_file: str,
    properties_file: str
) -> dict:
    """
    Validate an OBDA mapping file using Ontop.

    The creation of OBDA file is often error prone, and this tool is used to validate the consistency between the mapping file, the ontology file and the properties file.

    To run the validation, you need to have the ontology file, the mapping file and the properties file, also the postgres database (validation database) needs to be created and populated. 

    Args:
        mapping_file (str): Path to the OBDA mapping file
        ontology_file (str): Path to the ontology file (.ttl/.owl)
        properties_file (str): Path to the DB properties file

    Returns:
        dict: Validation result with status and message
    """


    try:
        # convert file path to local path from mcp paths 
        mapping_file = mapping_file.replace("/projects/data", "data")
        ontology_file = ontology_file.replace("/projects/data", "data")
        properties_file = properties_file.replace("/projects/data", "data")


        logger.info(f"Validating OBDA file: {mapping_file}")
        logger.info(f"Ontology file: {ontology_file}")
        logger.info(f"Properties file: {properties_file}")
        logger.info(f"Mapping path: {mapping_file}")
    

        # Convert paths to Path objects
        mapping_path = Path(mapping_file)
        ontology_path = Path(ontology_file)
        properties_path = Path(properties_file)

        logger.info("="*100)    
        logger.info(f"Mapping path: {mapping_path}")
        logger.info(f"Ontology path: {ontology_path}")
        logger.info(f"Properties path: {properties_path}")

        # Check file existence
        missing = [p.name for p in (mapping_path, ontology_path, properties_path) if not p.is_file()]
        if missing:

            logger.error(f"Missing file(s): {', '.join(missing)}")
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

        logger.info(f"Running Ontop validation command: {' '.join(cmd)}")

        # Run the validation
        result = subprocess.run(cmd, capture_output=True, text=True)
        logger.info("-"*50)
        logger.info(f"Ontop validation result: {result.stdout}")
        logger.info(f"Ontop validation error: {result.stderr}")

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