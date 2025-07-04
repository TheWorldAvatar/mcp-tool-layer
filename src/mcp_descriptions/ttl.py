TTL_VALIDATION_DESCRIPTION = """
    Validate a Turtle file for syntax errors and return a status string. TTL file must be created before using this function (You should always assume that the ttl file is not created yet.). 
    Note: This function is only used for validation of ttl files, not for creating ttl files. 
    The ttl file is the ontology file, which is used to define the ontology, which is necessary for the entire semantic data pipeline.
    After a ttl file is created, it needs to be validated to ensure the syntax is correct.
    Args:
        ttl_path (str): The path to the ttl file to be validated

    You must create the ttl file first, and then validate it. (ttl files are usually not provided in the resource, you need to create them first.)
    """