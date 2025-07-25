TTL_VALIDATION_DESCRIPTION = """
    Validate a created ontology ttl file for syntax errors. 
    
    For any ttl file created, you should always run this tool to validate the syntax. This is a mandatory step. 

    Mandatory Prerequisites:

    1. The ttl file must be created in a previous task.  
    """

TTL_VALIDATION_DESCRIPTION_EXECUTION = """
    Validate a Turtle ontology file (ttl file) for syntax errors and return a status string. TTL file must be created before using this function (You should always assume that the ttl file is not created yet.). 
    Note: This function is only used for validation of ttl files, not for creating ttl files. 
    The ttl file is the ontology file, which is used to define the ontology, which is necessary for the entire semantic data pipeline.
    After a ttl file is created, it needs to be validated to ensure the syntax is correct.
    Args:
        ttl_path (str): The path to the ttl file to be validated

    You must create the ttl file first, and then validate it. (ttl files are usually not provided in the resource, you need to create them first.)
    """

TTL_ONTOLOGY_CREATION_DESCRIPTION = """
    Create a turtle ontology file (ttl file) from rich input.

    A proper ontology should define domain-specific concepts and relationships in accordance with the domain knowledge and the data. 

    It is important that the ontology covers all the concepts and relationships in the data. 
    """
