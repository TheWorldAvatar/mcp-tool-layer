import os
import shutil
import subprocess
from pathlib import Path
from models.locations import ROOT_DIR, SANDBOX_DATA_DIR

def find_ontop_executable() -> str | None:
    """Return the path of 'ontop' (Linux/macOS) or 'ontop.bat' (Windows) in PATH."""
    exe_name = "ontop" if os.name == "nt" else "ontop"
    return shutil.which(exe_name)

def to_uri(p: str) -> str:  
    """Return a file:// URI for any Path (works cross-platform)."""
    # given a path, convert it to a file:// uri
    return f"file://{os.path.join(ROOT_DIR, p)}"


def create_db_properties_file(meta_task_name: str, iteration: int) -> str:
    db_properties_content = f"""
    jdbc.driver = org.postgresql.Driver
    jdbc.url    = jdbc:postgresql://host.docker.internal:4321/postgres
    jdbc.user   = postgres
    jdbc.password = validation_pwd

    """
    properties_file_relative_path = f"{SANDBOX_DATA_DIR}/{meta_task_name}/{iteration}/db.properties"
    with open(properties_file_relative_path, "w") as f:
        f.write(db_properties_content)

    return f"db.properties file created at {properties_file_relative_path}"
 
def validate_ontop_obda(
    mapping_file_relative_path: str,
    ontology_file_relative_path: str,
    meta_task_name: str,
    iteration_index: int
) -> dict:
    try:

        create_db_properties_file(meta_task_name, iteration_index)    

        # Convert paths to Path objects
        mapping_path = Path(mapping_file_relative_path)
        ontology_path = Path(ontology_file_relative_path)
        properties_path = Path(properties_file_relative_path)

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
    result = validate_ontop_obda(
        mapping_file_relative_path="data/test/ontocompchem.obda",
        ontology_file_relative_path="data/test/ontocompchem.ttl",
        properties_file_relative_path="data/test/db.properties"
    )


