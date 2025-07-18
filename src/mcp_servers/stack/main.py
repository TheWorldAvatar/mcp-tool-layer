from fastmcp import FastMCP
from typing import Any
from src.mcp_descriptions.ttl import TTL_VALIDATION_DESCRIPTION
from src.mcp_descriptions.postgres import POSTGRES_UPLOAD_DESCRIPTION
from src.mcp_descriptions.stack_operations import STACK_INITIALIZATION_DESCRIPTION, STACK_DATABASE_UPDATION_DESCRIPTION, STACK_DATA_REMOVAL_DESCRIPTION
from src.mcp_descriptions.sparql import SPARQL_QUERY_DESCRIPTION
from src.mcp_descriptions.obda import OBDA_CREATION_DESCRIPTION, OBDA_VALIDATION_DESCRIPTION, OBDA_CREATION_DESCRIPTION_EXECUTION
from models.Ontology import OntologyInput
# Import functions from separated files
from src.mcp_servers.stack.operations.ttl_validation_operations import (
    validate_ttl_file,
    create_ontology
)

from src.mcp_servers.stack.operations.postgres_operations import (
    upload_data_to_postgres
)

from src.mcp_servers.stack.operations.stack_operations_functions import (
    initialize_stack,
    update_stack_database,
    remove_stack_data
)

from src.mcp_servers.stack.operations.sparql_operations import (
    query_sparql
)

from src.mcp_servers.stack.operations.obda_creation_operations import (
    create_obda_file
)

from src.mcp_servers.stack.operations.obda_validation_operations import (
    validate_ontop_obda
)
from src.utils.global_logger import get_logger, mcp_tool_logger

# -------------------- CONFIG --------------------
mcp = FastMCP(name="stack_operations")
logger = get_logger("mcp_server", "stack_main")

# -------------------- TTL VALIDATION TOOLS --------------------

@mcp.prompt(name="instruction", description="This prompt provides detailed instructions for integrating any data into the semantic stack")
def instruction_prompt():
    return """
    This server provide **mandatory** steps to integrate data into the existing stack/semantic database. 
    The working process of integrating data into the existing stack/semantic database is as follows:
    1. Extract the data from the data source and convert it into csv format (may be multiple csv files to show different aspects of the data). It is always csv, the system doesn't accpet any other data format. 
    However, if the data is already in csv format, you can skip this step.
    2. While converting the data into csv format, you should also create a data schema file. This data schema file is used to create the ontology as the context for the ontology creation. 
    So if the data is already in csv format, you still need to create a data schema file.
    3. Create an ontology based on the data schema file using the `create_ontology` tool. 
    4. Integrate the data into the existing stack/semantic database using the `upload_data_to_postgres` tool
    5. According to the data schema and the ontology created, you should create a mapping file using the `create_obda_file` tool. 
    6. Validate the mapping file using the `validate_ontop_obda` tool. 
    7. Remove the data from the stack database using the `remove_stack_data` tool. 
    8. Initailize a new stack using the `initialize_stack` tool. 
    9. Update the stack database using the `update_stack_database` tool. 
"""

@mcp.tool(name="validate_ttl_file", description=TTL_VALIDATION_DESCRIPTION, tags=["ontology"])
@mcp_tool_logger
def validate_ttl_file_tool(ttl_path: str) -> str:
    return validate_ttl_file(ttl_path)

@mcp.tool(name="create_ontology", description="Generate an turtle ontology from rich input")
@mcp_tool_logger
def create_ontology_tool(ontology: OntologyInput, meta_task_name: str, iteration_index: int) -> str:
    """Build, serialise, and persist an ontology based on *ontology* input."""
    return create_ontology(ontology, meta_task_name, iteration_index)

# -------------------- POSTGRES TOOLS --------------------

@mcp.tool(name="upload_data_to_postgres", description=POSTGRES_UPLOAD_DESCRIPTION, tags=["postgres"])
@mcp_tool_logger
def upload_data_to_postgres_tool(data_path: str, table_name: str) -> dict:
    return upload_data_to_postgres(data_path, table_name)

# -------------------- STACK OPERATION TOOLS --------------------

@mcp.tool(name="initialize_stack", description=STACK_INITIALIZATION_DESCRIPTION, tags=["stack"])
@mcp_tool_logger
def initialize_stack_tool(stack_name: str):
    return initialize_stack(stack_name)    

@mcp.tool(name="update_stack_database", description=STACK_DATABASE_UPDATION_DESCRIPTION, tags=["stack"])
@mcp_tool_logger
def update_stack_database_tool(stack_name: str):
    return update_stack_database(stack_name)

@mcp.tool(name="remove_stack_data", description=STACK_DATA_REMOVAL_DESCRIPTION, tags=["stack"])
@mcp_tool_logger
def remove_stack_data_tool():
    return remove_stack_data()

# -------------------- SPARQL TOOLS --------------------

@mcp.tool(name="query_sparql", description=SPARQL_QUERY_DESCRIPTION, tags=["sparql"])
@mcp_tool_logger
def query_sparql_tool(
    *,
    endpoint_url: str = "http://localhost:3838/ontop/ui/sparql",
    query: str,
    raw_json: bool = False,
) -> Any:
    return query_sparql(endpoint_url=endpoint_url, query=query, raw_json=raw_json)

# -------------------- OBDA TOOLS --------------------

@mcp.tool(name="create_obda_file", description=OBDA_CREATION_DESCRIPTION_EXECUTION, tags=["obda"])
@mcp_tool_logger
def create_obda_file_tool(
    *,
    table_name: str,
    columns: list[str],
    prefixes: dict[str, str],
    id_column: str = "uuid",
    ontology_class: str | None = None,
    iri_template: str = "entity_{uuid}",
    use_xsd_typing: bool = False,
    meta_task_name: str,
    iteration_index: int
) -> str:
    return create_obda_file(
        table_name=table_name,
        columns=columns,
        prefixes=prefixes,
        id_column=id_column,
        ontology_class=ontology_class,
        iri_template=iri_template,
        use_xsd_typing=use_xsd_typing,
        meta_task_name=meta_task_name,
        iteration_index=iteration_index
    )

@mcp.tool(name="validate_ontop_obda", description=OBDA_VALIDATION_DESCRIPTION, tags=["obda"])
@mcp_tool_logger
def validate_ontop_obda_tool(
    mapping_file: str,
    ontology_file: str,
    properties_file: str
) -> dict:
    return validate_ontop_obda(mapping_file, ontology_file, properties_file)

# -------------------- MAIN ENTRYPOINT --------------------
if __name__ == "__main__":
    mcp.run(transport="stdio") 