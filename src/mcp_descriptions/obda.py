OBDA_VALIDATION_DESCRIPTION = """
    Validate an OBDA mapping file. The validation compares the OBDA file, the ttl ontology file, the data in the postgres database and check whether they are consistent with each other. 

    For any obda file created, you should always run this tool to validate the consistency. This is a mandatory step. 

    Args:
        mapping_file (str): Path to the OBDA mapping file
        ontology_file (str): Path to the ontology file (.ttl/.owl)
        properties_file (str): Path to the DB properties file

    Mandatory Prerequisites: 

    1. The OBDA mapping file is created in a previous task. 
    2. The ontology file (ttl file) is created in a previous task. 
    3. The data in the validation postgres database is uploaded in a previous task. 
    """

OBDA_CREATION_DESCRIPTION_EXECUTION = """
    Create an OBDA mapping file. This is the core and mandatory step for integrating data into the semantic stack. 

    To create a OBDA file, you need to first understand the schema of the tabular data and the ontology (ontology is represented by the ttl file). 

    As a result, a ttl file must be created before creating the obda file.  Also, to create a ttl file, you need to understand the schema of the tabular data. 

    As a result, for a schema file for the tabular data also must be created before creating the obda file. 

    Parameters
    ----------
    output_path : str
        Desired file location (MCP path). Created directories if necessary.
    table_name : str
        Name of the SQL table (or view) to map.
    columns : List[str]
        All column names in the table. ``id_column`` should be included but will
        not be mapped as a predicate.
    prefixes : Dict[str, str]
        Prefix → IRI mapping. The default prefix (key ``""``) is mandatory.
    id_column : str, default "uuid"
        Primary-key column used to build IRIs.
    ontology_class : Optional[str]
        If provided, emits a *rdf:type* mapping to this class.
    iri_template : str, default "entity_{uuid}"
        Template for individual IRIs in the default ``:`` namespace. *Curly*
        braces must match column names.
    use_xsd_typing : bool, default False
        When *True*, each predicate object is explicitly typed as
        ``xsd:string``.

    Dependencies: 
     - A set of schema file for the tabular data, which provides the column names and their types. 
     - One ontology (ttl file) file, which provides the ontology class names and their relationships. This file need to be created by the agent first.
    """

OBDA_CREATION_DESCRIPTION = """
    Create an OBDA mapping file. This is the core and mandatory step for integrating data into the semantic stack. 

    The OBDA file maps tabular data in a postgres database to the ontology, allowing using SPARQL queries to query the data from relational databases. `

    task_meta_name and iteration_index are not part of the obda file, just for composing the output file path. 

    Mandatory Prerequisites: 
     - One or more schema files for the tabular data, which provides the column names and their types, must be created in a previous task. 
     - One ontology (ttl file) file, which provides the ontology class names and their relationships, must be created in a previous task. 
     - The data in the validation postgres database is uploaded in a previous task. 
 
 
    """