from docling.document_converter import DocumentConverter

def fetch_and_convert_from_url(url: str) -> str:
    """
    Fetches the content from the given URL and converts it to a readable format using the docling library.
    
    Args:
        url (str): The URL to fetch the content from.
        
    Returns:
        str: The converted content from the URL in markdown format.
    """
    try:
        converter = DocumentConverter()
        doc = converter.convert(url).document
        return doc.export_to_markdown()
    except Exception as e:
        return f"Error fetching the URL: {e}"

# Example usage for testing
url = "https://www.cd-bioparticles.net/p/9912/3355-azobenzenetetracarboxylic-acid?srsltid=AfmBOoqGUt3jGTQ3II2SU1K92bpHFLpAO2E1b2R_Xw9cKYCZy3Chh946"
converted_content = fetch_and_convert_from_url(url)
print(converted_content)
