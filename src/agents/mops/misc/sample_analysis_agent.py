from models.LLMCreator import LLMCreator
from models.ModelConfig import ModelConfig
from models.locations import SANDBOX_TASK_DIR, PLAYGROUND_DATA_DIR
from src.utils.global_logger import get_logger
import asyncio
import os
import glob  

def load_sampling_report():
    """Load the latest ontology sampling report from playground directory."""
    sampling_files = glob.glob("playground/ontomops_sampling_ontomops_ogm_2hop_10classes_20250919_165443.md")
    if not sampling_files:
        return "No sampling report found."
    
    # Get the most recent file
    latest_file = max(sampling_files, key=os.path.getmtime)
    
    try:
        with open(latest_file, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        return f"Error reading sampling report: {e}"

async def sample_analysis_agent():
    model_config = ModelConfig(temperature=0.2, top_p=0.02)
    llm_creator = LLMCreator(model="gpt-4o", model_config=model_config)
    llm = llm_creator.setup_llm()

    logger = get_logger("agent", "SampleAnalysisAgent")
    logger.info(f"Starting Sample Analysis Agent")
    
    # Load the sampling report
    sampling_report = load_sampling_report()
    logger.info(f"Loaded sampling report: {len(sampling_report)} characters")
 
    chemical_species = "C16H10O4, 4,4'-(Ethyne-1,2-diyl)dibenzoic acid (This is the same species.)"


    INSTRUCTION_SAMPLE_ANALYSIS_PROMPT = f"""

 
    Your task is to study the sampling report and derive the chemical representations for the given chemical species that 
    aligns with the representations in the sampling report.

    The sampling report gives you hints about how the chemical species are represented in an existing knowledge graph.

    Your task is to derive the chemical representation for the given chemical species that aligns with the representations in the sampling report.

    Provide the derived chemical representation of the given chemical species. 

    Chemical species:
    {chemical_species}

    Sampling report:
    {sampling_report}
    """

    response = llm.invoke(INSTRUCTION_SAMPLE_ANALYSIS_PROMPT).content

    logger.info(f"Sample analysis completed")
    print(response)


if __name__ == "__main__":
    asyncio.run(sample_analysis_agent())