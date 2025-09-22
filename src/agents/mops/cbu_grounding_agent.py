from models.BaseAgent import BaseAgent
from models.ModelConfig import ModelConfig
from models.locations import SANDBOX_TASK_DIR, PLAYGROUND_DATA_DIR
from src.utils.global_logger import get_logger
import os
import asyncio  
import argparse




async def cbu_grounding_agent(species_name: str):
    """
    This agent reads the md file produced and outputs the CBU information involved
    """
    logger = get_logger("agent", "CBUGroundingAgent")
    logger.info(f"Starting CBU grounding agent for task: {species_name}")
    
    model_config = ModelConfig(temperature=0.2, top_p=0.02)
    mcp_tools = ["pubchem", "enhanced_websearch", "chemistry"]
    agent = BaseAgent(model_name="gpt-4o", model_config=model_config, remote_model=True, mcp_tools=mcp_tools, mcp_set_name="chemistry.json")   


    INSTRUCTION_CBU_PROMPT = """

    You are provided a chemical product name. Your task is to use the mcp tools to come up with a specific chemical formula (CBU formula convention). 

    You are able to 

    - Enhanced websearch tool allows you to search and fetch content from the internet. It is wise to only input the chemical product name given in the query, and may be wrapped in quotes for exact match.
    - Search for pubchem for accurate and specific chemical representation, especially SMILES. You must use CAS number to search for the chemical in pubchem tool and get the SMILES.
    - Use a specific tool to convert SMILES to the specific chemical formula we need. 
    - To use the enhanced websearch, usually you search first, then find the link you think is most relevant, then use the docling tool to fetch the content from the link.
    - Don't use docling until you are sure that is the link you want. Only use docling once or twice. It is very slow.

    I recommand you to first search the internet to get things like CAS number, IUPAC name, etc. When you are sure you have enough information, 
    use the pubchem tool to get the SMILES and use the chemistry tool to convert the SMILES to the specific chemical formula we need.

    I also recommend you not to use pubchem until you got the CAS number for the chemical. 

    Don't stop until you have the core-label out. Retry from beginning if you don't have the core-label out.

    Provide the chemical formula in the CBU formula convention. There might be multiple core-labels out, show all of them. 

    The chemcial product name is "{species_name}"
    """
    instruction = INSTRUCTION_CBU_PROMPT.format(
        species_name=species_name
    )

    response, metadata = await agent.run(instruction, recursion_limit=400)
    return response

if __name__ == "__main__":

    # The expected outputs are
    # [(C12H6)(CO2)4]
    # [(C6H4C)2(CO2)2]
    # [(C12H6N2)2(CO2)4]
    # [(C14H6)2(CO2)4] 

    # The output we got are
    #  -  
    # [(C6H6C6)(CO2)4]
    # [(C6H4C)_2(CO2)2]
    # [(C6H6C6)(N2)(CO2)4]
    # [(C6H6C8)(CO2)4]: 
    # 
    # https://www.cd-bioparticles.net/p/9912/3355-azobenzenetetracarboxylic-acid?srsltid=AfmBOoql_u90U1fkK4yUlOGZWmRU1excUXJEBdqeVqsdVHC8Tm-3FIPw -> C16H10N2O8 indicate the same issue as the following one.  
    # However, https://www.sigmaaldrich.com/GB/en/product/aldrich/sik211710?srsltid=AfmBOopLIN7S3k3ZPJGk3OsEqM5DJXBaB0FoK-77eBtKe2WLJaIqBwGv shows Empirical Formula (Hill Notation):
    # C18H10O8, which is not consistent with the ground truth and indicates that the output is correct. 



 

    candidate_list = ["H4BPTC", "H2edb", "H4abtc", "H4EBTC"]

    for candidate in candidate_list:
        response = asyncio.run(cbu_grounding_agent(candidate))
        print(response)
        print("--------------------------------")
        x = input("Press Enter to continue...")



