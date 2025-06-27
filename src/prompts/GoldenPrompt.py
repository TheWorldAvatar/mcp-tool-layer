"""
These are prompts that are for "ultimate" tests for agents, where only very vague instructions are given. 

The agents will then need to reason themselve to gather more information, evaluate the environment, and then take actions. 

Finally, achieve the goal specified in the prompt. 

"""

GAUSSIAN_TO_SPARQL_ENDPOINT = """
Your task is to populate ontop with the provided gaussian output file at /sandbox/data/test/benenze.log. 
Do the necessary steps, what I want ultimately is to get the charges and multiplicity via a SPARQL query to the endpoint. 
"""