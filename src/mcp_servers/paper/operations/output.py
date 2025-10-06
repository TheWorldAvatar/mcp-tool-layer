import os
from typing import List
from pydantic import BaseModel
from models.locations import SANDBOX_DATA_DIR
import json

class Paper(BaseModel): 
    title: str
    url: str
    abstract: str

def paper_to_dict(paper: Paper) -> dict:
    return {
        "title": paper.title,
        "url": paper.url,
        "abstract": paper.abstract
    }


def output_paper(iteration: int, paper: List[Paper]) -> str:
    output_dir = os.path.join(SANDBOX_DATA_DIR, "papers")
    os.makedirs(output_dir, exist_ok=True)
    with open(os.path.join(output_dir, f"{iteration}.json"), "w") as f:
        f.write(json.dumps([paper_to_dict(p) for p in paper]))
    return f"Paper outputted to {output_dir}"


if __name__ == "__main__":
    papers = [
        Paper(title="Paper 1", url="https://www.google.com", abstract="Abstract 1"),
        Paper(title="Paper 2", url="https://www.google.com", abstract="Abstract 2"),
    ]
    print(output_paper(0, papers))