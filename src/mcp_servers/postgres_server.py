import pandas as pd
from sqlalchemy import create_engine, text
from mcp.server.fastmcp import FastMCP
import os

mcp = FastMCP("PostgresUpload")

@mcp.tool()
def upload_csv_to_postgres(csv_path: str, table_name: str) -> dict:
    """
    Upload a CSV file to a validationPostgreSQL database.

    Note: This postgres server is for validating the consistency between the data, the obda file and the ttl file.
    In order to validate the consistency, we need to upload the data to the validation postgres database.

    You must note that this is not the actual database, it is only used for validation purposes.

    Args:
        csv_path (str): Path to the CSV file to be uploaded
        table_name (str): Name of the table to be created
    Returns:
        dict: Dictionary containing:
            - table_name: Name of the created table
            - status: Success or error status
            - message: Descriptive message


    
    """


    # Database connection parameters
    USER = "postgres"
    PASSWORD = "validation_pwd"
    HOST = "host.docker.internal"  # only works if port 4321 is exposed to host
    DBNAME = "postgres"
    PORT = "4321"

    # Connection string
    connection_string = f"postgresql+psycopg2://{USER}:{PASSWORD}@{HOST}:{PORT}/{DBNAME}"

    # Create SQLAlchemy engine
    engine = create_engine(connection_string)



    csv_path = csv_path.replace("/projects/data", "data")
    try:
        if not os.path.isfile(csv_path):
            raise FileNotFoundError(f"File not found: {csv_path}")

        # Read the CSV file
        df = pd.read_csv(csv_path)
        
        # Upload to PostgreSQL
        df.to_sql(table_name, engine, if_exists="replace", index=False)
        
        return {
            'table_name': table_name,
            'status': 'success',
            'message': f'Data successfully uploaded to table {table_name}'
        }
        
    except Exception as e:
        return {
            'status': 'error',
            'message': str(e)
        }

if __name__ == "__main__":
    mcp.run(transport="stdio")