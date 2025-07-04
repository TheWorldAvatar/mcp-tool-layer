POSTGRES_UPLOAD_DESCRIPTION = """
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