SPARQL_QUERY_DESCRIPTION = """
    Send a SPARQL SELECT/ASK query to **endpoint_url** and return the answer.

    Args
    ----
    endpoint_url : full URL, e.g. "http://localhost:3838/ontop/ui/sparql"
    query        : the SPARQL 1.1 string
    raw_json     : if True, return the full SPARQL JSON document;
                   if False (default), return a simplified list of rows

    Returns
    -------
    • SELECT → list of dicts  (unless raw_json=True)
    • ASK    → bool
    • other  → raw JSON (construct queries are not altered)
    """