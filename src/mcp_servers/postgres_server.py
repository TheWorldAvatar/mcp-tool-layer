import pandas as pd
from sqlalchemy import create_engine, text
from fastmcp import FastMCP
import os
from src.mcp_descriptions.postgres import POSTGRES_UPLOAD_DESCRIPTION


mcp = FastMCP("PostgresUpload")

@mcp.tool(name="upload_data_to_postgres", description=POSTGRES_UPLOAD_DESCRIPTION, tags=["postgres"])
def upload_data_to_postgres(data_path: str, table_name: str) -> dict:
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
    data_path = data_path.replace("/projects/data", "data")
    try:
        if not os.path.isfile(data_path):
            raise FileNotFoundError(f"File not found: {data_path}")

        # Read the CSV file
        df = pd.read_csv(data_path)
        
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