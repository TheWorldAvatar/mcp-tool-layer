"""
Ontology Sampling Operations for generating improved markdown reports.
"""

import os
import json
import requests
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple, List
import re

from src.mcp_servers.sparql.ontomop_operation import query_sparql
from src.utils.global_logger import get_logger

logger = get_logger("mcp_server", "ontology_sampling")

def get_classes_with_instances(endpoint_url: str) -> Dict[str, int]:
    """Get all classes that have at least one instance."""
    logger.info("Finding classes with instances...")
    
    query = """
    PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    PREFIX owl: <http://www.w3.org/2002/07/owl#>
    
    SELECT DISTINCT ?class (COUNT(?instance) as ?count)
    WHERE {
        ?instance rdf:type ?class .
        # Filter out common RDF/OWL system classes
        FILTER(!STRSTARTS(STR(?class), "http://www.w3.org/1999/02/22-rdf-syntax-ns#"))
        FILTER(!STRSTARTS(STR(?class), "http://www.w3.org/2002/07/owl#"))
        FILTER(!STRSTARTS(STR(?class), "http://www.w3.org/2000/01/rdf-schema#"))
    }
    GROUP BY ?class
    ORDER BY DESC(?count) ?class
    """
    
    try:
        result = query_sparql(query, endpoint_url=endpoint_url, raw_json=True)
        if result and 'results' in result and 'bindings' in result['results']:
            classes_info = {}
            for binding in result['results']['bindings']:
                class_uri = binding['class']['value']
                count = int(binding['count']['value'])
                classes_info[class_uri] = count
            return classes_info
        else:
            logger.warning("No results returned from classes query")
            return {}
    except Exception as e:
        logger.error(f"Error querying classes: {e}")
        return {}

def sample_instance_for_class(class_uri: str, endpoint_url: str) -> Optional[str]:
    """Sample one random instance for a given class."""
    query = f"""
    PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
    
    SELECT ?instance
    WHERE {{
        ?instance rdf:type <{class_uri}> .
    }}
    LIMIT 1
    """
    
    try:
        result = query_sparql(query, endpoint_url=endpoint_url, raw_json=True)
        if result and 'results' in result and 'bindings' in result['results']:
            bindings = result['results']['bindings']
            if bindings:
                return bindings[0]['instance']['value']
    except Exception as e:
        logger.error(f"Error sampling instance for {class_uri}: {e}")
    
    return None

def get_2hop_subgraph(instance_uri: str, endpoint_url: str) -> str:
    """Get 2-hop subgraph around an instance."""
    query = f"""
    CONSTRUCT {{
        <{instance_uri}> ?p1 ?o1 .
        ?o1 ?p2 ?o2 .
        ?s1 ?p3 <{instance_uri}> .
        ?s1 ?p4 ?o3 .
    }}
    WHERE {{
        {{
            <{instance_uri}> ?p1 ?o1 .
            OPTIONAL {{ ?o1 ?p2 ?o2 . }}
        }}
        UNION
        {{
            ?s1 ?p3 <{instance_uri}> .
            OPTIONAL {{ ?s1 ?p4 ?o3 . }}
        }}
    }}
    """
    
    try:
        # Use raw SPARQL endpoint for CONSTRUCT queries
        headers = {
            'Accept': 'text/turtle',
            'Content-Type': 'application/sparql-query'
        }
        
        response = requests.post(endpoint_url, data=query, headers=headers)
        if response.status_code == 200:
            return response.text
        else:
            logger.warning(f"Error in subgraph query: {response.status_code}")
            return ""
    except Exception as e:
        logger.error(f"Error getting subgraph for {instance_uri}: {e}")
        return ""

def get_common_namespaces() -> List[str]:
    """Get common namespace declarations used in the ontology."""
    return [
        "@prefix brick: <https://brickschema.org/schema/Brick#> .",
        "@prefix csvw: <http://www.w3.org/ns/csvw#> .",
        "@prefix dc: <http://purl.org/dc/elements/1.1/> .",
        "@prefix dcam: <http://purl.org/dc/dcam/> .",
        "@prefix dcat: <http://www.w3.org/ns/dcat#> .",
        "@prefix dcmitype: <http://purl.org/dc/dcmitype/> .",
        "@prefix dcterms: <http://purl.org/dc/terms/> .",
        "@prefix doap: <http://usefulinc.com/ns/doap#> .",
        "@prefix foaf: <http://xmlns.com/foaf/0.1/> .",
        "@prefix geo: <http://www.opengis.net/ont/geosparql#> .",
        "@prefix odrl: <http://www.w3.org/ns/odrl/2/> .",
        "@prefix org: <http://www.w3.org/ns/org#> .",
        "@prefix owl: <http://www.w3.org/2002/07/owl#> .",
        "@prefix prof: <http://www.w3.org/ns/dx/prof/> .",
        "@prefix prov: <http://www.w3.org/ns/prov#> .",
        "@prefix qb: <http://purl.org/linked-data/cube#> .",
        "@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .",
        "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .",
        "@prefix schema: <https://schema.org/> .",
        "@prefix sh: <http://www.w3.org/ns/shacl#> .",
        "@prefix skos: <http://www.w3.org/2004/02/skos/core#> .",
        "@prefix sosa: <http://www.w3.org/ns/sosa/> .",
        "@prefix ssn: <http://www.w3.org/ns/ssn/> .",
        "@prefix time: <http://www.w3.org/2006/time#> .",
        "@prefix vann: <http://purl.org/vocab/vann/> .",
        "@prefix void: <http://rdfs.org/ns/void#> .",
        "@prefix wgs: <https://www.w3.org/2003/01/geo/wgs84_pos#> .",
        "@prefix xml: <http://www.w3.org/XML/1998/namespace> .",
        "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> ."
    ]

def clean_turtle_content(content: str) -> str:
    """Clean turtle content by removing namespace declarations and extra whitespace."""
    # Remove @prefix declarations since we'll have them at the top
    content = re.sub(r'@prefix[^.]*\.\s*', '', content, flags=re.IGNORECASE)
    
    # Remove extra blank lines
    content = re.sub(r'\n\s*\n\s*\n', '\n\n', content)
    
    # Remove leading/trailing whitespace from each line
    lines = [line.strip() for line in content.split('\n') if line.strip()]
    
    return '\n'.join(lines)

def generate_improved_sampling_report(
    endpoint_url: str,
    ontology_name: str = "ontomops-ogm",
    max_hops: int = 2,
    output_dir: str = "playground"
) -> Tuple[bool, str]:
    """
    Generate an improved sampling report that only includes classes with instances
    and consolidates namespace declarations.
    
    Returns:
        Tuple of (success: bool, message: str)
    """
    logger.info(f"Starting improved ontology sampling for {ontology_name}...")
    
    try:
        # Get classes with instances
        classes_info = get_classes_with_instances(endpoint_url)
        
        if not classes_info:
            return False, "No classes with instances found!"
        
        logger.info(f"Found {len(classes_info)} classes with instances")
        
        # Generate timestamp for filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Start building the markdown report
        markdown_lines = []
        
        # Header
        markdown_lines.extend([
            "# Ontology Instance Sampling Report",
            "",
            f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            f"**SPARQL Endpoint:** `{endpoint_url}`",
            "",
            f"**Maximum Hops:** {max_hops}",
            "",
            f"**Ontology:** {ontology_name}",
            "",
            f"**Total Classes with Instances:** {len(classes_info)}",
            "",
            "This report contains sampled instances and their 2-hop subgraphs for classes that have instances in the knowledge graph.",
            "",
            "## Namespace Declarations",
            ""
        ])
        
        # Add namespace declarations once
        markdown_lines.append("```turtle")
        markdown_lines.extend(get_common_namespaces())
        markdown_lines.extend(["```", ""])
        
        # Process each class
        successful_samples = 0
        failed_samples = 0
        
        for class_uri, instance_count in classes_info.items():
            logger.info(f"Processing {class_uri} ({instance_count} instances)...")
            
            # Sample an instance
            instance_uri = sample_instance_for_class(class_uri, endpoint_url)
            
            if not instance_uri:
                logger.warning(f"No instance found for {class_uri}")
                failed_samples += 1
                continue
            
            # Get 2-hop subgraph
            subgraph = get_2hop_subgraph(instance_uri, endpoint_url)
            
            if not subgraph or len(subgraph.strip()) == 0:
                logger.warning(f"No subgraph found for {instance_uri}")
                failed_samples += 1
                continue
            
            # Extract class name for display
            class_name = class_uri.split('/')[-1] if '/' in class_uri else class_uri.split('#')[-1]
            
            # Add class section
            markdown_lines.extend([
                f"## Class: {class_name}",
                "",
                f"**Full URI:** `{class_uri}`",
                "",
                f"**Instance Count:** {instance_count}",
                "",
                f"**Sampled Instance:** `{instance_uri}`",
                "",
                "### 2-Hop Subgraph",
                "",
                "```turtle"
            ])
            
            # Clean and add the subgraph content
            cleaned_content = clean_turtle_content(subgraph)
            if cleaned_content:
                markdown_lines.append(cleaned_content)
            
            markdown_lines.extend([
                "```",
                "",
                "---",
                ""
            ])
            
            successful_samples += 1
        
        # Add execution summary
        success_rate = (successful_samples / len(classes_info)) * 100 if classes_info else 0
        
        markdown_lines.extend([
            "## Execution Summary",
            "",
            f"- **Total Classes Processed:** {len(classes_info)}",
            f"- **Successful Samples:** {successful_samples}",
            f"- **Failed Samples:** {failed_samples}",
            f"- **Success Rate:** {success_rate:.1f}%",
            f"- **SPARQL Endpoint:** `{endpoint_url}`",
            f"- **Hop Depth:** {max_hops}",
            "",
            "## Technical Notes",
            "",
            "- **Sampling Strategy:** One random instance per class",
            "- **Subgraph Extraction:** 2-hop traversal with cycle prevention",
            "- **Optimization:** Only includes classes with instances",
            "- **Namespace Handling:** Consolidated declarations at document start",
            "- **Content Cleaning:** Removed duplicate prefixes and excessive whitespace",
            ""
        ])
        
        # Write the report
        output_path = Path(output_dir)
        output_path.mkdir(exist_ok=True)
        filename = f"{ontology_name}_sampling_{len(classes_info)}classes_{timestamp}.md"
        report_path = output_path / filename
        
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(markdown_lines))
        
        success_message = (
            f"Report generated successfully: {report_path}\n"
            f"Successfully processed {successful_samples}/{len(classes_info)} classes "
            f"({success_rate:.1f}% success rate)"
        )
        
        logger.info(success_message)
        return True, success_message
        
    except Exception as e:
        error_message = f"Error generating sampling report: {e}"
        logger.error(error_message)
        return False, error_message
