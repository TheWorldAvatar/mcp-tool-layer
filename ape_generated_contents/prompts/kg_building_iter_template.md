Follow these generic rules for any iteration.

{PROMPT_CORE}

{IDENTIFICATION_HEADER}

**Critical**: 

- Be absolutely faithful to the provided content, strictly assign orders according to the provided content. 
Strictly put the steps one by one according to the provided content. 
- Make sure every step listed in the paper content is created, no exception.


** Highest priority**: 
- For any task, creation, addition and connection of entities are the highest priority, must be done before you terminate the task. 
- There is no exception to this rule.
- Repeated using check_existing_* tools will tell you that you should terminate the task and export, this is only vaiable 
if you have already done all the creation, addition and connection of entities.
- However, you should restrict to the information that is provided in the paper content.
- For sequential adding, make sure you follow the order and add one by one.
- If check_existing_* something gives no existing entity, you **MUST** to create/add the entity immediately instead of checking again.

{FOOTER_WITH_ENTITY}

