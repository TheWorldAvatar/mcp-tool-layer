"""Hash generation utilities for DOI identifiers"""

import hashlib


def generate_hash(doi: str) -> str:
    """
    Generate an 8-character hash from DOI.
    
    Args:
        doi: DOI string (e.g., "10.1021.acs.chemmater.0c01965")
        
    Returns:
        8-character hexadecimal hash
    """
    return hashlib.sha256(doi.encode()).hexdigest()[:8]

