OBDA_VALIDATION_DESCRIPTION = """
    Validate an OBDA mapping file. The validation compares the OBDA file, the ttl ontology file, the data in the postgres database and check whether they are consistent with each other. 

    For any obda file created, you should always run this tool to validate the consistency. This is a mandatory step. 

    Args:
        mapping_file (str): Path to the OBDA mapping file
        ontology_file (str): Path to the ontology file (.ttl/.owl)
        properties_file (str): Path to the DB properties file

    Mandatory Prerequisites: 

    1. The OBDA mapping file is created in a previous task. 
    2. The ontology file (ttl file) is created in a previous task. 
    3. The data in the validation postgres database is uploaded in a previous task. 


    """
    
OBDA_CREATION_DESCRIPTION_EXECUTION = """
Create (or append to) an **Ontop .obda mapping file** that integrates **all CSV
tables describing a single real-world entity** (e.g. all vibrational-mode
tables, or all atomic-property tables) into one coherent virtual graph.

Why one call per *entity*?
--------------------------
Tables such as *vibfreqs* and *vibrmasses* refer to the **same** vibrational
mode (they share the key columns `file` + `index`). Calling this tool once with
*both tables* ensures that every property is attached to the **same URI** for
each entity instance, so SPARQL joins happen automatically and the graph stays
normalized.

This function supports safe appending to an existing OBDA file. It will ensure:
- **Triple deduplication** across repeated calls or tables.
- **Safe mappingId generation**, avoiding naming collisions.
- **Automatic IRI pattern generation**, if omitted.
- **One file, one triple map collection** – final ']]]' is repaired if lost.

Agent workflow
--------------
1. Read `data_schema.json` and the domain ontology (.ttl).  
2. Group CSV tables that share the same **primary-key column(s)** and describe
   the *same* conceptual entity.  
3. Assign a shared `ontology_class` and `iri_template`.  
4. Create one `OBDAInput` describing this entity group and invoke this tool.  
5. Repeat until *all* tables are covered.

Key design rules
~~~~~~~~~~~~~~~~
* **id_columns** – list of one or more key columns shared by all tables.
  These identify entity instances (e.g. `["file", "index"]`) and define URI scope.  
* **iri_template** – all tables must use the *same* template so that triples
  join on a common subject. Example: `atom_{file}_{index}`.  
* **ontology_class** – optional RDF type to assign to each subject.  
* **property_mappings** – for each table, map non-key columns to ontology
  predicates. If missing, predicate names are auto-derived from column names
  (e.g. `value` → `:dataValue`, `val0` → `:dataVal0`, etc.).  
* **use_xsd_typing** – when True, all literals are typed as `^^xsd:string`.

Parameters
----------
obda_input : OBDAInput
    Describes the entity class, shared keys, IRI template, and all table inputs.  
meta_task_name : str
    Used to choose the sandbox output directory `sandbox/data/{meta_task}/`.  
iteration_index : int
    Sub-directory inside the meta-task folder.

--------------------------------------------------------------------------
OBDAInput schema
--------------------------------------------------------------------------
prefixes        : Dict[str,str]        – must include the default namespace with key ""  
entities        : List[EntityMapping]  – each group maps to one subject pattern

EntityMapping
~~~~~~~~~~~~~
id_columns      : List[str]            – 1+ key columns common to all tables  
ontology_class  : str | None           – optional RDF class to assert  
iri_template    : str | None           – pattern with {col} placeholders; auto-generated if missing  
use_xsd_typing  : bool = False         – append ^^xsd:string to literals  
tables          : List[TableMapping]   – 1+ CSV/SQL tables mapping to this entity

TableMapping
~~~~~~~~~~~~
table_name        : str                – table name (CSV or SQL view)  
columns           : List[str]          – full list of columns in this table  
property_mappings : Dict[str,str] | None  
    Maps column → predicate (if omitted, predicate is auto-named from column)

--------------------------------------------------------------------------
Minimal example – vibrational modes (two tables, one entity)
--------------------------------------------------------------------------
```python
OBDAInput(
    prefixes = {
        "":   "http://example.org/resource/",
        "ont":"http://example.org/gaussian#",
        "xsd":"http://www.w3.org/2001/XMLSchema#",
    },
    entities = [
        EntityMapping(
            id_columns = ["file", "index"],
            ontology_class = "VibrationalMode",
            iri_template = "vibMode_{file}_{index}",
            use_xsd_typing = True,
            tables = [
                TableMapping(
                    table_name="vibfreqs",
                    columns=["file", "index", "value"],
                    property_mappings={"value": "hasVibrationalFrequency"},
                ),
                TableMapping(
                    table_name="vibrmasses",
                    columns=["file", "index", "value"],
                    property_mappings={"value": "hasReducedMass"},
                ),
            ],
        ),
    ],
)
"""

OBDA_CREATION_DESCRIPTION = """
    Create an OBDA mapping file. This is the core and mandatory step for integrating data into the semantic stack. 

    The OBDA file maps tabular data in a postgres database to the ontology, allowing using SPARQL queries to query the data from relational databases. `

    task_meta_name and iteration_index are not part of the obda file, just for composing the output file path. 

    Mandatory Prerequisites: 
     - One or more schema files for the tabular data, which provides the column names and their types, must be created in a previous task. 
     - One ontology (ttl file) file, which provides the ontology class names and their relationships, must be created in a previous task. 
     - The data in the validation postgres database is uploaded in a previous task. 
 
 
    """