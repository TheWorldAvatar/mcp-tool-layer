"""
Simple websearch operations package.
"""

from .serper_search import google_search
from .docling_fetch import url_to_markdown

__all__ = [
    "google_search",
    "url_to_markdown"
]
