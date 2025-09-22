"""
Simple Docling-based URL to markdown conversion.
"""


def url_to_markdown(url: str) -> str:
    """
    Fetches content from URL and converts it to markdown format using Docling.
    
    Args:
        url: The URL to fetch and convert
        
    Returns:
        str: The converted content in markdown format
    """
    
    try:
        from docling.document_converter import DocumentConverter
        
        converter = DocumentConverter()
        doc = converter.convert(url).document
        return doc.export_to_markdown()
    except ImportError:
        return "Error: Docling library not installed. Please install it with: pip install docling"
    except Exception as e:
        return f"Error fetching the URL: {e}"


if __name__ == "__main__":
    print(url_to_markdown("https://www.cd-bioparticles.net/p/9912/3355-azobenzenetetracarboxylic-acid"))