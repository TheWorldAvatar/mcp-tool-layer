import os
from rdflib import Graph, Namespace
from src.utils.global_logger import get_logger

def parse_top_level_entities(doi: str, output_file: str = "iteration_1.ttl"):
    logger = get_logger("agent", "MCPRunAgent")
    ttl_file_path = os.path.join("data", doi, output_file)
    if not os.path.exists(ttl_file_path):
        logger.warning(f"TTL not found: {ttl_file_path}")
        return []
    try:
        g = Graph(); g.parse(ttl_file_path, format="ttl")
        ONTOSYN = Namespace("https://www.theworldavatar.com/kg/OntoSyn/")
        RDFS = Namespace("http://www.w3.org/2000/01/rdf-schema#")
        q = """
        SELECT DISTINCT ?synthesis ?label WHERE {
            ?synthesis a ontosyn:ChemicalSynthesis .
            OPTIONAL { ?synthesis rdfs:label ?label }
        }
        """
        out = []
        for row in g.query(q, initNs={"ontosyn": ONTOSYN, "rdfs": RDFS}):
            uri = str(row.synthesis)
            label = str(row.label) if row.label else uri.split('/')[-1]
            out.append({"uri": uri, "label": label, "types": ["ontosyn:ChemicalSynthesis"]})
        logger.info(f"Found {len(out)} top-level entities for DOI {doi}")
        return out
    except Exception as e:
        logger.error(f"Error parsing TTL {ttl_file_path}: {e}")
        return []

if __name__ == "__main__":
    # Example: python -m src.agents.mops.dynamic_mcp.modules.kg
    # Set a sample DOI folder name for local debug
    import json
    sample_doi = "10.1021.acs.chemmater.0c01965"
    result = parse_top_level_entities(sample_doi)
    print(json.dumps(result, indent=4))