"""
Simple Serper-based Google search operations.
"""

import http.client
import json
import os
from pathlib import Path
from typing import Optional

from src.mcp_servers.enhanced_websearch.operations.timeout import (
    call_with_retry,
    env_float,
    env_int,
)

_DEFAULT_TIMEOUT_SEC = 15.0
_DEFAULT_ATTEMPTS = 3

_DOTENV_LOADED = False


def _ensure_dotenv_loaded() -> None:
    """Load repository ``.env`` once so MCP subprocesses see ``SERPER_API_KEY``."""
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    _DOTENV_LOADED = True
    try:
        from dotenv import load_dotenv
    except Exception:
        return
    try:
        root = Path(__file__).resolve().parents[4]
        env_path = root / ".env"
        if env_path.is_file():
            load_dotenv(env_path, override=False)
        else:
            load_dotenv(override=False)
    except Exception:
        pass


def _serper_api_key() -> Optional[str]:
    _ensure_dotenv_loaded()
    key = (os.environ.get("SERPER_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip()
    return key or None


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
        api_key = _serper_api_key()
        if not api_key:
            return json.dumps(
                {
                    "error": (
                        "Serper API key missing. Set SERPER_API_KEY in the project .env "
                        "or in the process environment."
                    )
                }
            )

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


def _timeout_seconds() -> float:
    return env_float("SERPER_TIMEOUT_SEC", _DEFAULT_TIMEOUT_SEC)


def _attempts() -> int:
    return env_int("SERPER_ATTEMPTS", _DEFAULT_ATTEMPTS)


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
    timeout_seconds = _timeout_seconds()
    attempts = _attempts()

    def _do() -> str:
        conn = http.client.HTTPSConnection("google.serper.dev", timeout=timeout_seconds)
        try:
            payload = json.dumps({
                "q": query,
                "num": 10,
                "page": page,
            })
            headers = {
                "X-API-KEY": api_key,
                "Content-Type": "application/json",
            }
            conn.request("POST", "/search", payload, headers)
            res = conn.getresponse()
            data = res.read()
            if res.status >= 500:
                raise RuntimeError(f"Serper HTTP {res.status}")
            return data.decode("utf-8")
        finally:
            conn.close()

    try:
        return call_with_retry(
            f"serper({query!r}, page={page})",
            _do,
            timeout_seconds=timeout_seconds,
            attempts=attempts,
        )
    except Exception as e:
        return json.dumps({
            "error": f"Error performing single page search: {str(e)}"
        })