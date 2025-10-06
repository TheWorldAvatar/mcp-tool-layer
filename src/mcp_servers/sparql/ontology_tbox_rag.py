"""
This utility provides, in the first stage, a simple name -> T-Box mapping 
for the agents to retrieve **FUll T-Box** for a given ontology. 

In the following stages, we will allow multi-level, coarse to fine-grained T-Box retrieval
on demand, which aims to shorten the token usage on T-Box. 


Currently, three T-Boxes in data/ontologies/ontology_repo are supported:
- ontosynthesis
- ontomop
- ontospecies
"""

from models.locations import ONTOLOGY_REPO_DIR
from typing import Literal
import os


def get_ontology_tbox(ontology_name: Literal["ontosynthesis", "ontomops", "ontospecies"]) -> str:
    """
    Get the T-Box for a given ontology name.
    """
    ontology_path = os.path.join(ONTOLOGY_REPO_DIR, f"{ontology_name}.ttl")
    with open(ontology_path, "r") as f:
        return f.read()



if __name__ == "__main__":
    print(get_ontology_tbox("ontosynthesis"))
    print(get_ontology_tbox("ontomops"))
    print(get_ontology_tbox("ontospecies"))