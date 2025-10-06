from src.utils.stitch_md import stitch_sections_to_markdown
from models.locations import DATA_DIR
import os
import json 


# iterate through folders in SANDBOX_TASK_DIR, find those with sections.json, and stitch the markdown
for folder in os.listdir(DATA_DIR):
    if os.path.isdir(os.path.join(DATA_DIR, folder)):
        if os.path.exists(os.path.join(DATA_DIR, folder, "sections.json")):
            sections_dict = json.load(open(os.path.join(DATA_DIR, folder, "sections.json"), "r"))
            stitch_sections_to_markdown(sections_dict, folder)










