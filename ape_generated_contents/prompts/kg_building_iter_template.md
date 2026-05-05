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
- Ordered-member property fidelity is mandatory: if the extracted hints contain ordered members with `hasOrder`, create every hinted member and materialize each property on the member with that exact order.
- Never copy, inherit, or reuse a property value from one ordered member onto another ordered member unless the T-Box or prompt explicitly states that inheritance rule for that property.
- If two ordered members share the same class, still treat them as separate individuals with independent property values; identical class does not imply identical temperature, duration, vessel, amount, boolean flags, or linked objects.
- If check_existing_* something gives no existing entity, you **MUST** to create/add the entity immediately instead of checking again.
- The provided scoped top-level entity is authoritative for this iteration. Reuse its exact IRI and build around it.
- Do **NOT** create, switch to, or export around a second top-level entity for the same scope, even if another entity has the same or a similar label.
- Before export, verify that every entity created or reused in this iteration is attached to the scoped top-level entity through the required ontology relations.
- Export is only valid after creation/addition/connection are all complete for the scoped entity.

{FOOTER_WITH_ENTITY}

