#!/usr/bin/env python3
"""
OntoMOP SPARQL Operation - Simple Query Interface

A simple SPARQL query function for the OntoMOP knowledge graph.
Takes a SPARQL query and returns JSON results.

Based on test results:
- 2,383 MOPs available
- 150 Chemical Building Units (CBUs)
- 166 MOPs with CCDC numbers
- Endpoint: http://68.183.227.15:3838/blazegraph/namespace/ontomops_ogm/sparql
"""

import sys
import os
import json
import requests
import time
from typing import Any, Optional

# Add the KG_trial directory to the path for imports
try:
    current_dir = os.path.dirname(os.path.abspath(__file__))
    kg_trial_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(current_dir))), 
                                'playground', 'KG_trial')
    sys.path.append(kg_trial_path)
except NameError:
    # Handle case when __file__ is not defined
    kg_trial_path = os.path.join(os.getcwd(), 'playground', 'KG_trial')
    sys.path.append(kg_trial_path)


class OntoMOPConnection:
    def __init__(self, endpoint_url: Optional[str] = None):
        self.endpoint = endpoint_url or "http://68.183.227.15:3838/blazegraph/namespace/ontomops_ogm/sparql"


def query_sparql(query: str, endpoint_url: Optional[str] = None, raw_json: bool = False, mode: str = "probe") -> Any:
    """
    Execute a SPARQL query against the OntoMOP knowledge graph.
    
    Args:
        query (str): The SPARQL query to execute.
        endpoint_url (str, optional): SPARQL endpoint URL. If None, uses default OntoMOP endpoint.
        raw_json (bool): If True, return raw JSON response; if False, return simplified results.
        mode (str): Query mode - only "probe" mode is supported. Default: "probe".

        note: Full mode is disabled. Only probe mode is available.
        
    Returns:
        Any: Query results - simplified list of dicts with max 10 entries, or raw JSON response.
         
    """
    
    # Initialize connection
    connection = OntoMOPConnection(endpoint_url)
    endpoint = connection.endpoint
    
    try:
        # Execute the query
        response = requests.post(
            endpoint,
            data=query.encode('utf-8'),
            headers={
                'Content-Type': 'application/sparql-query',
                'Accept': 'application/sparql-results+json'
            },
            timeout=600
        )
        response.raise_for_status()
        result = response.json()
        
        # Handle different result types
        if "boolean" in result:
            return result["boolean"]
        
        # If raw_json is requested, return the raw result
        if raw_json:
            return result
        
        # Parse the bindings from the JSON result
        bindings = result.get("results", {}).get("bindings", [])
        
        # Simplify bindings for easier use
        simplified_results = []
        for row in bindings:
            simplified_row = {var: cell["value"] for var, cell in row.items()}
            simplified_results.append(simplified_row)
        
        # Probe mode: Always limit to 10 results
        if len(simplified_results) > 10:
            truncated_results = simplified_results[:10]
            return {
                "status": "probe_mode",
                "message": f"⚠️  Probe mode: Showing first 10 results out of {len(simplified_results)} total results.",
                "data": truncated_results,
                "total_results": len(simplified_results),
                "suggestion": "Full mode is disabled. Use raw_json=true to get complete raw results."
            }
        else:
            return {
                "status": "probe_mode",
                "message": f"✅ Probe mode: Showing all {len(simplified_results)} results.",
                "data": simplified_results,
                "total_results": len(simplified_results)
            }
        
    except requests.exceptions.HTTPError as e:
        # Handle specific HTTP errors
        if e.response.status_code == 400:
            return {
                "error": str(e),
                "status": "failed",
                "note": "SPARQL query is not syntactically correct. Please check your query syntax.",
                "suggestion": "Common issues: missing closing braces, incomplete class names, or malformed prefixes"
            }
        elif e.response.status_code == 500:
            return {
                "error": str(e),
                "status": "failed",
                "note": "SPARQL query failed due to server error."
            }
        else:
            return {
                "error": str(e),
                "status": "failed",
                "note": f"HTTP error {e.response.status_code}: {e.response.reason}"
            }
    except requests.exceptions.RequestException as e:
        return {
            "error": str(e),
            "status": "failed",
            "note": "SPARQL query failed due to connection or runtime error."
        }
    except Exception as e:
        return {
            "status": "failed",
            "note": "Unexpected error occurred during query execution.",
            "error": str(e)
        }



def main():
    """Simple example usage - just run some SPARQL queries and print results."""
    print("🧪 OntoMOP SPARQL Query Examples")
    print("=" * 40)
    
    # Query 1: Count MOPs
    print("\n1️⃣ Count total MOPs:")
    query1 = """
    PREFIX mop: <https://www.theworldavatar.com/kg/ontomops/>
    SELECT ?bindingSite ?bindingPoint 
    WHERE { 
        ?bindingSite a mop:BindingSite . 
        ?bindingSite mop:hasBindingPoint ?bindingPoint 
    }
    LIMIT 5
    """
    result1 = query_sparql(query1)
    print(f"   Result: {result1}")
    
    # # Query 2: Query ontospecies
    # print("\n2️⃣ Query ontospecies:")
    # query2 = """
    #     PREFIX species: <https://www.theworldavatar.com/kg/ontospecies/>
    #     SELECT ?species
    #     WHERE {
    #         ?species ?p ?o .
    #     }
    #     LIMIT 10
    # """
    # ontospecies_endpoint_url = "http://178.128.105.213:3838/blazegraph/namespace/ontospecies/sparql"
    # result2 = query_sparql(query2, endpoint_url=ontospecies_endpoint_url)
    # print(f"   Result: {result2}")

if __name__ == "__main__":
    main()