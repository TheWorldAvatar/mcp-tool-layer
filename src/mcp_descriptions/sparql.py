SPARQL_QUERY_DESCRIPTION =   """
    Execute a SPARQL query against a knowledge graph endpoint.
    
    This tool provides access to various SPARQL endpoints including:

    - OntoMOP: Metal-Organic Polyhedra data (default endpoint)
    - OntoSynthesis: Chemical synthesis data
    - OntoSpecies: Chemical species information

    The according endpoint urls are:

    - ontomops: http://68.183.227.15:3838/blazegraph/namespace/ontomops_ogm/sparql
    - ontosynthesis: http://68.183.227.15:3838/blazegraph/namespace/OntoSynthesisTestEncoding2/sparql
    - ontospecies: http://178.128.105.213:3838/blazegraph/namespace/ontospecies/sparql
    
    Args:
        endpoint_url: The SPARQL endpoint URL. Defaults to OntoMOP endpoint.
        query: The SPARQL 1.1 query string to execute.
        raw_json: If True, return the full SPARQL JSON document; if False, return simplified results.
        mode: Query mode - "probe" (safety checks, truncates large results) or "full" (saves to file). Default: "probe".
        
    Returns:
        - probe mode: Simplified results with safety checks (warns if no LIMIT, truncates if >1000 chars)
        - full mode: File path where results are saved (for large datasets)
        
    Safety Features:
        - probe mode: Automatically checks for LIMIT clause and warns if missing
        - probe mode: Truncates results if they exceed 1000 characters to prevent context explosion
        - full mode: Saves large results to files in data/temp/ directory
        
 

    """