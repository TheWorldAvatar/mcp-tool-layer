from docling.document_converter import DocumentConverter
import os
from models.locations import DATA_DIR 

def convert_pdf_to_markdown(pdf_path, output_folder):
    """Convert a PDF to markdown and save to the same folder."""
    try:
        print(f"Converting {pdf_path}...")
        # Initialize the converter
        converter = DocumentConverter()
        
        # Convert the PDF
        result = converter.convert(pdf_path)
        
        # Generate markdown content
        markdown_content = result.document.export_to_markdown()
        
        # Create output filename
        base_name = os.path.splitext(os.path.basename(pdf_path))[0]
        output_file = os.path.join(output_folder, f"{base_name}.md")
        
        # Save markdown to file
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(markdown_content)
        
        print(f"Successfully converted {pdf_path} to {output_file}")
        return output_file
        
    except Exception as e:
        print(f"Error converting {pdf_path}: {str(e)}")
        return None

def convert_doi_pdfs(doi: str):
    """Convert PDFs for a specific DOI using the new file path convention."""
    # Define the data folder path
    data_folder = os.path.join(DATA_DIR, doi)
    
    # Check if data folder exists
    if not os.path.exists(data_folder):
        print(f"X Data folder not found: {data_folder}")
        return False
    
    # List of PDF files to convert for this DOI
    pdf_files = [f"{doi}.pdf", f"{doi}_si.pdf"]
    
    print(f"Starting PDF to Markdown conversion for DOI: {doi}")
    print(f"Data folder: {data_folder}")
    print("-" * 50)
    
    success_count = 0
    skipped_count = 0
    
    # Convert each PDF
    for pdf_file in pdf_files:
        pdf_path = os.path.join(data_folder, pdf_file)
        markdown_file = os.path.join(data_folder, f"{os.path.splitext(pdf_file)[0]}.md")
        
        if os.path.exists(pdf_path):
            # Check if markdown file already exists
            if os.path.exists(markdown_file):
                print(f"SKIP Markdown file already exists: {markdown_file}")
                skipped_count += 1
                success_count += 1  # Count as success since file exists
            else:
                output_file = convert_pdf_to_markdown(pdf_path, data_folder)
                if output_file:
                    print(f"OK Conversion completed: {output_file}")
                    success_count += 1
                else:
                    print(f"X Conversion failed: {pdf_file}")
        else:
            print(f"X PDF file not found: {pdf_path}")
    
    print("-" * 50)
    if skipped_count > 0:
        print(f"Conversion process completed! {success_count}/{len(pdf_files)} files ready ({skipped_count} skipped, {success_count - skipped_count} converted).")
    else:
        print(f"Conversion process completed! {success_count}/{len(pdf_files)} files converted successfully.")
    return success_count > 0

def convert_all_dois():
    """Convert PDFs for all DOIs in the data directory."""
    print("Starting PDF to Markdown conversion for all DOIs...")
    print(f"Data directory: {DATA_DIR}")
    print("-" * 50)
    
    if not os.path.exists(DATA_DIR):
        print(f"X Data directory not found: {DATA_DIR}")
        return False
    
    # Get all DOI folders in the data directory
    # Skip log directory and other non-DOI directories
    excluded_dirs = {'log', '__pycache__', '.git', '.vscode', 'node_modules'}
    
    doi_folders = [d for d in os.listdir(DATA_DIR) 
                   if os.path.isdir(os.path.join(DATA_DIR, d)) 
                   and not d.startswith('.')
                   and d not in excluded_dirs]
    
    if not doi_folders:
        print("No DOI folders found in data directory")
        return False
    
    print(f"Found {len(doi_folders)} DOI folders")
    
    success_count = 0
    total_skipped = 0
    total_converted = 0
    
    for doi in doi_folders:
        print(f"\nProcessing DOI: {doi}")
        if convert_doi_pdfs(doi):
            success_count += 1
    
    print("-" * 50)
    print(f"Overall conversion completed! {success_count}/{len(doi_folders)} DOIs processed successfully.")
    return success_count > 0

def check_markdown_files_exist(doi: str):
    """
    Check if all required markdown files exist for a DOI.
    
    Args:
        doi (str): The DOI identifier
        
    Returns:
        bool: True if all required markdown files exist, False otherwise
    """
    data_folder = os.path.join(DATA_DIR, doi)
    
    if not os.path.exists(data_folder):
        return False
    
    # Check for main markdown file
    main_md = os.path.join(data_folder, f"{doi}.md")
    if not os.path.exists(main_md):
        return False
    
    # Check for SI markdown file (optional)
    si_md = os.path.join(data_folder, f"{doi}_si.md")
    if not os.path.exists(si_md):
        print(f"Warning: SI markdown file not found: {si_md}")
        # Don't fail if SI doesn't exist, as it's optional
    
    return True

def main(specific_doi: str = None):
    """Main function for command line usage."""
    if specific_doi:
        convert_doi_pdfs(specific_doi)
    else:
        convert_all_dois()

if __name__ == "__main__":
    main()
