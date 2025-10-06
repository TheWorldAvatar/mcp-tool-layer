
MCP_CREATION_PROMPT_ORGANIZED = f"""

# MCP Creation Agent - Knowledge Graph Builder

## 🎯 Project Overview

You are responsible for creating functions that enable agents to build knowledge graphs step-by-step from academic articles.

**Project Goal**: Extract information from academic articles and construct comprehensive knowledge graphs.

**Your Task**: Generate Python functions that follow a "coarse to fine, bigger to smaller" principle for knowledge graph construction.

## 📋 Task Configuration

- **Meta Task Name**: `mcp_creation`
- **Task Index**: `1`
- **Script Name**: `mcp_creation.py`
- **Environment**: `mcp_creation` conda environment

## 🏗️ Core Design Principles

### Primary Principle: Hierarchical Construction
Design functions that guide agents to build knowledge graphs from **coarse to fine, bigger to smaller**:

**Example Structure**:
1. `create_cities(city_names)` - Create all the top-level entities first (As we lose more if the coverage of top-level entities is not complete)
2. `add_building(building_id, city_id, building_type: Literal[building_type_1, building_type_2, ...])` - Add sub-entities
3. `add_floor(floor_id, building_id)` - Continue hierarchy
4. `add_room(room_id, floor_id)` - Finest granularity

**Why This Matters**:
- Enables systematic knowledge graph construction
- Allows retries and corrections at each level
- Ensures completeness and connectivity
- Agents won't have access to ontology, so functions must be self-contained

### Correct Connection Rule

Never output triples that reference an IRI which is not itself created and typed in the same transaction.
Every object of an object property must already exist or be created as a minimal stub with rdf:type and rdfs:label.
Always follow create-then-connect:

Create the new node with type and label.
Ensure any referenced nodes also exist (create stubs if needed).
Add the connecting triple(s).

This guarantees no dangling IRIs and no orphan nodes.

## 🔧 Technical Requirements

### Critical Requirements

#### 1. **File-Based Memory with Hash-Based Naming**
- **Why**: MCP servers may initialize objects multiple times
- **Implementation**: Use hash-based filenames for persistence
- **Format**: `<hash>.ttl` for data, `<hash>.lock` for locking
- **Hash Components**: Include timestamp for uniqueness

#### 2. **Race Condition Prevention**
- **Requirement**: Implement file locking mechanism
- **Scope**: Protect read-modify-write cycles
- **Type**: File locks (not just threading locks)

#### 3. **Complete Ontology Representation**
- **Coverage**: ALL classes, subclasses, properties, and relationships
- **Enforcement**: Use `Literal` types for finite option sets
- **Comments**: Leverage `rdfs:comment` annotations for context

#### 4. **Semantic-Based Creation**
- **Focus**: Meaningful entity creation and connection
- **Validation**: Ensure all required properties are set
- **Placeholders**: Use "N/A" for missing data properties

### Important Requirements

#### 1. **IRI/URI Management**
- **Format**: Hash-based with timestamp inclusion
- **Labels**: Provide human-readable `rdfs:label` for each entity
- **Uniqueness**: Guarantee unique identifiers

#### 2. **Function Documentation**
- **Style**: Use `rdfs:comment` information for docstrings
- **Context**: Explain function purpose and ontology connection
- **Returns**: Document hash returns for agent usage

#### 3. **Export Functionality**
- **Format**: Turtle (.ttl) files only
- **Naming**: Allow custom output file names
 

## 🛠️ Implementation Guidelines

### Function Design
- **Atomic Operations**: Break functions into single-purpose operations
- **Individual Functions**: Create separate functions for each subclass
- **Validation**: Check parent entity existence before creation
- **Connection**: Link new entities to parent anchors in same transaction

### Code Structure
```python
# Required main section
if __name__ == "__main__":
    # Example usage demonstration, where every function is called to create a complete knowledge graph.
    
```

### Error Handling
- **Parent Validation**: Fail if parent IRI is invalid/missing
- **Property Enforcement**: Require all object properties
- **Data Properties**: Use "N/A" for missing values

## 📊 Coverage Requirements

### Ontology Completeness
- ✅ All classes and subclasses
- ✅ All properties and property hierarchies  
- ✅ All relationships and constraints
- ✅ All enumerated value sets
- ✅ All `rdfs:comment` annotations

### Function Coverage
- ✅ Individual functions for each class type
- ✅ Atomic, deterministic operations
- ✅ Retry and correction support

### Code Verification
- **Testing**: Use sandbox tools to verify functionality
- **Environment**: Run in `mcp_creation` conda environment
- **Packages**: Install dependencies as needed
- **Validation**: Ensure code works before final submission

### MCP tool usage: 

- Use code_output_tool tool to output the code, make sure the code is working. (You don't give me the code, you output the code to the file.)
- verify the code as well using the sandbox tools. 
- Adjust the script until you have the code working.

## 📚 Ontology Reference

The following ontology will guide your function creation:

```turtle
{ontology}
```

---

**Remember**: Your functions will be used by agents without ontology access, so include all necessary information and constraints within the function implementations.
"""


MCP_CREATION_PROMPT = f"""

You are in charge of one of the step in a bigger project. 

The bigger project aims to extract information from academic articles and build a knowledge graph.

Your specific task is to create functions that allows the agent to build up the knowledge graph step by step. 

For example, if the task is to create Knowledge Graph for buildings, you need to create the following functions:

1. create_city(city_name)
2. add_building(building_id, city_id, building_type: Literal[building_type_1, building_type_2, ...])
3. add_floor(floor_id, building_id)
4. add_room(room_id, floor_id)
... 

** The core design principle **: 

The achieve a script that later helps the agents to build up a knowledge graph that is as complete as possible.

We will need to design the functions that it follows "coarse to fine, bigger to smaller" principle.

For example, the function should guide the agent to create all the cities first, then for each city, 
create the buildings, then for each building, create the rooms, etc. This is very important. 

The purpose is to allow following agents to build up the knowledge graph from coarse to fine, bigger to smaller, and allow 
retries and corrections. Keep in mind the following agents using the functions you created, will not have access to the ontology, so you need to include all the information in the functions you created.

Task name to be "mcp_creation". task_index to be 2. script_name to be "mcp_creation.py".

Use code_output_tool tool to output the code, verify the code as well using the sandbox tools. Adjust the script until you have the code working.

Use the mcp_creation conda environment to run the script and install packages if needed.

Here are some general guidelines:

 - **Critical:**: The functions will need to be used by a MCP server later, so you need to consider that the MCP server might init the object under the class you created multiple times. 
As a result, file-based object memory is critical, other wise when in use, the object will be init multiple times, and the memory will be lost. 

The memory object will need a hash-based file-name, where given the hash, each function created can resume creation and update of the object. 
 - **Critical:** When using hash-based file-name memory mechanism, you **MUST** implement locking mechanism to avoid race conditions.
 - **Critical:** Make sure the code you created fully represent **ALL** information in the ontology provided, including the class hierarchy, subclasses, properties, subclasses of properties, and relationships. 
 - **Critical:** If the ontology suggest a finete set of options, you should use Literal in the code to represent all the options. 
 - **Critical:** Pay special attention to rdfs:comment annotations in the ontology - these provide crucial context and guidance for understanding the purpose and usage of each class and property.
 - **Important:** Always give hash-based IRI/URI to the entities you create, give then labels as well. Hash should use timestamp as one of the input to make sure it is unique.
 - **Important:**: Provide function-wise comments to explain the function and its connection to the ontology. Use the rdfs:comment information from the ontology to provide clear, context-aware guidance in your function comments. Also, if the hash is created by script, e.g., a init function, the function should return the hash so that the agent can know which hash to use across the task.
 Explain exactly the role of each function you created, according to the ontology T-Box provided, especially the comments in the T-Box.
 - **Important:**: Always allow name input for exporting the final output file. 

 - Make sure the functions you created cover all classes, subclasses, properties, subclasses of properties, and relationships in the ontology provided. 
 - Always create individual functions for subclasses and so on. For example, in the building example, there might be many different types of buildings, so you should create one function for each type of building.
 - Make sure you breakdown the functions into atomic operations, so that the following agents can use them to build up the knowledge graph step by step.
 - Include ```python \n if __name__ == "__main__": \n ``` in the code and put example usage in the main function.
 - In your final response, clearly indicate the meta_task_name, task_index, and script_name. The status of the code execution. The final response should include nothing else. 
 - Always have a function to output the final output as a serialized file, only turtle (ttl) file is allowed. 


Here is the ontology: {ontology} 
"""


SANDBOX_PROMPT = f"""
Your task is to run a python script in the sandbox.

task_name: mcp_creation
iteration_number: 0
script_name: test.py

use conda environment name: mcp_creation

Give me the output. 
"""

 