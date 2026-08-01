Problems: 

1. main.py exposes generic functions that allows agents to freely create triples, bypassing the script-based enforcement for semantic constraints.

Solution: 

0. meta prompt should expose the correct context ... 

1. Move the validation (hard and soft) to earlier, whatever we generated, should be high quality in the first time. 

    - Soft validation: 

2. the first try generation quality is low, taking too much effort to repair. 


creation_base.py → creation_entities.py → creation_relationships.py → creation_checks.py → main.py


- 