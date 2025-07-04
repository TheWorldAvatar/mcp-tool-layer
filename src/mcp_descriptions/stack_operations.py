STACK_INITIALIZATION_DESCRIPTION = """    
Initialize the semantic database stack by running the stack.sh start <stack_name> command in stack-manager dir

The stack is an already made docker stack that offers a SPARQL endpoint, a postgres database and a ontop endpoint.

This is mandatory for the entire semantic data pipeline, i.e., any data introduced will be stored in the stack, and all the queries will be executed against the stack.

You must always run this function before using any other stack related functions. 

**Important**: The stack is the only way to store the data, and the only way to query the data.

"""


STACK_DATABASE_UPDATION_DESCRIPTION = """
This function is used to update the data in the stack. The data uploaded include the data the csv file, the obda file and the ttl file.

This is mandatory for integrating any data into the existing semantic database, with no exception.

Only after the data is uploaded to the stack, the data can be queried.

Run a stack command in WSL, the default command is ./stack.sh start <stack_name> in stack-data-uploader dir, which updates the data in the ontop endpoint"""

STACK_DATA_REMOVAL_DESCRIPTION =     """Remove all existing stack data, this must be done before initializing a new stack"""
