"""
Simple Serper-based Google search operations.
"""

import http.client
import json
from typing import Optional


def google_search(query: str, page: int = 1) -> str:
    """
    Perform a Google search using the Serper API.
    
    Args:
        query: Search query string
        page: Cumulative page number (1 returns page 1, 2 returns pages 1+2, 3 returns pages 1+2+3, etc.)
    
    Returns:
        JSON string containing the search results (combined if multiple pages)
    """
    try:
        # Use hardcoded API key
        api_key = "a6b91e8f487e465e6b84ad1711e00be5b0b75da2"
        
        # Collect results from page 1 through specified page
        all_results = {
            "organic": [],
            "knowledgeGraph": None,
            "searchInformation": None,
            "searchParameters": None
        }
        
        for current_page in range(1, page + 1):
            page_result = _single_search(query, api_key, current_page)
            page_data = json.loads(page_result)
            
            # Check for errors
            if "error" in page_data:
                return page_result  # Return error immediately
            
            # Merge results
            if "organic" in page_data:
                all_results["organic"].extend(page_data["organic"])
            
            # Keep knowledge graph from first page that has it
            if all_results["knowledgeGraph"] is None and "knowledgeGraph" in page_data:
                all_results["knowledgeGraph"] = page_data["knowledgeGraph"]
            
            # Keep search information from first page
            if all_results["searchInformation"] is None and "searchInformation" in page_data:
                all_results["searchInformation"] = page_data["searchInformation"]
            
            # Keep search parameters from first page
            if all_results["searchParameters"] is None and "searchParameters" in page_data:
                all_results["searchParameters"] = page_data["searchParameters"]
        
        # Update search information to reflect combined results
        if all_results["searchInformation"]:
            all_results["searchInformation"]["totalResults"] = len(all_results["organic"])
        
        return json.dumps(all_results)
        
    except Exception as e:
        return json.dumps({
            "error": f"Error performing Google search: {str(e)}"
        })


def _single_search(query: str, api_key: str, page: int) -> str:
    """
    Perform a single page search using Serper API.
    
    Args:
        query: Search query string
        api_key: Serper API key
        page: Page number to search
    
    Returns:
        JSON string containing the search results for this page
    """
    try:
        # Establish connection to Serper API
        conn = http.client.HTTPSConnection("google.serper.dev")
        
        # Prepare payload
        payload = json.dumps({
            "q": query,
            "num": 10,  # Fixed at 10 results per page
            "page": page
        })
        
        # Prepare headers
        headers = {
            'X-API-KEY': api_key,
            'Content-Type': 'application/json'
        }
        
        # Make the request
        conn.request("POST", "/search", payload, headers)
        res = conn.getresponse()
        data = res.read()
        
        # Close connection
        conn.close()
        
        # Return the response data
        return data.decode("utf-8")
        
    except Exception as e:
        return json.dumps({
            "error": f"Error performing single page search: {str(e)}"
        })