 # prompts.py — updated

PROMPT_CORE = r'''Global rules:

- All MCP tool outputs are single-line JSON. Always parse them. Never rely on free text.
- Expected keys: <"status","iri","created","already_attached","retryable","code","message"> plus tool-specific fields (e.g., "supplier_iri").
- Stop conditions for functions:
  * already_attached == true → mark this target DONE; do not call the same tool again with identical arguments.
  * status == "ok" and created == true → DONE for that target.
  * status == "error" and retryable == false → SKIP; never retry with the same payload.

- Stop conditions for iterations:
  * When you think you have put all information from the paper into the knowledge graph according to your task scope, and 
  the entities you created are correctly connected, you should use the export_memory tool to export the memory and terminate the job.


- Never invoke the same tool twice with identical arguments in one run.
- Prefer check_existing_* tools to avoid duplicates; only create when missing.
- Supplier placeholder policy:
  * supplier_name "N/A" denotes unknown. Reuse the canonical existing "N/A" Supplier once; do not reattach in loops.
- ChemicalInput.name must not be "N/A". If a tool returns code == "INVALID_PLACEHOLDER", do not retry; adjust inputs or skip.
- Terminate the iteration by emitting exactly: <"run_status":"done"> when all current items are DONE or SKIPPED (non-retryable error).

**Critical**: VesselEnvironment must be atmosphere, never a mixture of chemicals etc. 

** Critical**: When a function provides an input that is and iri, e.g., supplier_iri, you must create the instance before you pass and iri to the function. This is very important. IRIs are hash-based or timestamp-based, so you cannot come up with any iri on your own. Each creation instance function will return 
the created iri after you call it successfully, that is where you can get the iri. Never pass "http://xxxxxxx/N/A" or "N/A" as an iri to any function.


**Critical**: You can never assume that an IRI exist unless you used the according check_existing_* tool to check it and in the result, 
the IRI is explicitly returned. 


** Critical**: Before you call any function with an iri as an input, you must use the according check_existing_* tool to check the IRIs you can use. 
This is compulsory. If no valid IRI can be reused, or the details of the existing IRI doesn't match the required (e.g., a temperature exist, but with a different value),
you must create a new IRI with the necessary details.

**Critical**: You should not repeatedly use any check_existing_* tool to check the same IRI, the results will not change unless you take 
any action between the check_existing_* calls. If you think you have finished the given task, you should use the export_memory tool to export the memory and terminate the job.

When you think you are done with the given task, terminate the job, don't do useless things even if you have not yet hit the recursion limit. 
'''

MCP_PROMPT_ITER_1 = '''Follow these generic rules for any iteration.

{PROMPT_CORE}

Identification:
- If a DOI is provided by upstream context, treat it as the sole task identifier and reuse it consistently. Do not invent new identifiers.
Don't change any details of the doi.

The following is the doi: {doi}

Entity focus:

- When entity_label and entity_uri are provided, scope all creation and connections to this entity. You may create and connect new entities only insofar as they attach to this scoped entity.

==============

Task:
Using only the extracted information, create instances of ontosyn:ChemicalSynthesis for each chemical synthesis procedure described. Link each ChemicalSynthesis to its source document using ontosyn:retrievedFrom. Write all triples to output.ttl.

Constraints:
- In this iteration, do NOT create ontosyn:ChemicalOutput or any ChemicalInput. Only create ontosyn:ChemicalSynthesis instances (one per synthesis procedure).
- Avoid duplicate ontosyn:ChemicalSynthesis; deduplicate at the procedure level.

Termination:
- When all syntheses are created or skipped per rules above, output <"run_status":"done">.

==============

The following is the paper content for your task: 

{paper_content}

'''.replace('{PROMPT_CORE}', PROMPT_CORE)



MCP_PROMPT_ITER_2 = '''Follow these generic rules for any iteration.

{PROMPT_CORE}

Identification:
- If a DOI is provided by upstream context, treat it as the sole task identifier and reuse it consistently. Do not invent new identifiers.
Don't change any details of the doi.

The following is the doi: {doi}

Entity focus:

- When entity_label and entity_uri are provided, scope all creation and connections to this entity. You may create and connect new entities only insofar as they attach to this scoped entity.

Restrict yourself to create only the KG part that given to you above and nothing else.

==============

Task:
Using only the extracted information for the specific ontosyn:ChemicalSynthesis, create BOTH:
1) All ontosyn:ChemicalInput instances and link them via ontosyn:hasChemicalInput. For each input, add alternative names (ontosyn:hasAlternativeNames), chemical formula (ontosyn:hasChemicalFormula), detailed description (ontosyn:hasChemicalDescription), purity (ontosyn:hasPurity), and supplier (ontosyn:isSuppliedBy) if explicitly mentioned. If a reference to an external material is provided, link using ontosyn:referencesMaterial.
2) A single ontosyn:ChemicalOutput instance for this synthesis and set it via ontosyn:hasChemicalOutput. If a representation is explicitly stated, link it using ontosyn:isRepresentedBy.
Write all triples to output.ttl.
3) Supplier handling: If a supplier is explicitly mentioned, reuse the existing supplier IRI (after check_existing_suppliers). Otherwise attach the canonical supplier named "N/A" (reuse if it already exists). Do not loop on "N/A".

Critical constraints:
- Ensure exactly ONE ChemicalOutput per synthesis.
- Do NOT create duplicate ChemicalInput instances for the same synthesis.
- ChemicalInput.name must not be "N/A". If a tool returns INVALID_PLACEHOLDER → do not retry the same payload.

Post-call handling:
- After add_supplier_to_chemical_input, if already_attached==true or retryable==false, treat as DONE for that pair and do not call again with identical arguments.

Termination:
- When inputs, output, and suppliers are DONE or SKIPPED for this synthesis, emit <"run_status":"done">.

==============

This is the top level entity for you to focus on during this iteration. 
{entity_label}, {entity_uri}

The following is the paper content for your task: 

{paper_content}'''.replace('{PROMPT_CORE}', PROMPT_CORE)

MCP_PROMPT_ITER_3 = '''Follow these generic rules for any iteration.

{PROMPT_CORE}

Identification:
- If a DOI is provided by upstream context, treat it as the sole task identifier and reuse it consistently. Do not invent new identifiers.
Don't change any details of the doi.

The following is the doi: {doi}

Entity focus:

- When entity_label and entity_uri are provided, scope all creation and connections to this entity. You may create and connect new entities only insofar as they attach to this scoped entity.

Task-specific rules for this iteration come after this header.

**Critical**: Don't repeat one step if you encounter errors; fix inputs per error and adjust actions. Do not repeat the same tool with identical inputs more than once.

==============

**Critical**: 

- Be absolutely faithful to the provided content, strictly assign orders according to the provided content. 
Strictly put the steps one by one according to the provided content. 
- Make sure every step listed in the paper content is created, no exception.


Task:
Using only the extracted information, for each synthesis step in each ontosyn:ChemicalSynthesis, create an instance of the appropriate step class (Add, Stir, HeatChill, Evaporate, Sonicate, Dissolve, Crystallize, Transfer, Separate, Filter, Dry, SeparationType). Link each step to its parent ChemicalSynthesis via ontosyn:hasSynthesisStep. For each step, set ontosyn:hasOrder and add all step-specific properties (vessels, vessel environments, duration, equipment, and type-specific parameters). Write all triples to output.ttl.

ChemicalInputs (including materials and solvents):
- All materials and solvents are modeled as ontosyn:ChemicalInput instances.
- For Add steps, attach ChemicalInput individuals via ontosyn:hasAddedChemicalInput.
- For Dissolve/Separate/Filter steps, use step-specific properties with ChemicalInput IRIs (e.g., ontosyn:hasDissolveChemical, ontosyn:hasSeparationChemical, ontosyn:hasWashingChemical).
- Strictly create the Add steps for all the materials listed in the paper content, one material one Add step, one Add step per material.


Add steps:
- One Add step per material. Do not combine multiple materials into one Add step.
- For each ontosyn:ChemicalInput linked to the synthesis, create a separate Add step and call add_chemical_to_add_step with chemical_input_iri.
- For each material (including solvents) added, create a separate Add step with the existing chemical_input_iri from the ChemicalInputs created in iteration 2.
- You must include all the Add steps listed in the paper content.


Vessels, Vessel Environments, Durations:
- They are important components of the steps, you should create them if they are mentioned in the paper.
- Make sure you include all of them. 
- Make sure you add them to the step.
 
Ordering:
- Maintain contiguous ontosyn:hasOrder values across all steps. 
- Strictly assign orders according to the provided content.

Error handling:
- Respect JSON stop conditions. If a post-call response indicates already_attached==true or retryable==false, do not call the same tool again with identical args.

Termination:
- When all required steps and properties are DONE or SKIPPED, emit <"run_status":"done">.

==============

This is the top level entity for you to focus on during this iteration. 
{entity_label}, {entity_uri}


======================= Paper Content =======================
The following is the paper content for your task: 

{paper_content}'''.replace('{PROMPT_CORE}', PROMPT_CORE) 



MCP_PROMPT_ITER_4 = '''Follow these generic rules for any iteration.

{PROMPT_CORE}

Identification:
- If a DOI is provided by upstream context, treat it as the sole task identifier and reuse it consistently. Do not invent new identifiers.
Don't change any details of the doi.

The following is the doi: {doi}

Entity focus:

- When entity_label and entity_uri are provided, scope all creation and connections to this entity. You may create and connect new entities only insofar as they attach to this scoped entity.

==============

Task:
Using ONLY the extracted yield information, add the yield to the ontosyn:ChemicalSynthesis using ontosyn:hasYield. This iteration focuses EXCLUSIVELY on yield data.

Scope:
- Extract and add ONLY yield percentage data
- Do NOT add equipment, steps, or other properties in this iteration
- If no yield is stated in the extraction, skip this synthesis

Yield handling:
- Use the create_yield or equivalent tool to create a yield instance
- Link it to the ChemicalSynthesis via ontosyn:hasYield
- If multiple yields are provided (e.g., different qualifiers), create separate yield instances for each
- Preserve qualifiers (e.g., "based on H3TATB", "isolated yield") in the yield properties if supported

Constraints:
- Apply check_existing_* tools before creating to avoid duplicates
- Obey JSON stop conditions and do not repeat identical calls if already_attached==true or retryable==false

Termination:
- When yield is added or skipped (if not stated), emit <"run_status":"done">.

==============

This is the top level entity for you to focus on during this iteration. 
{entity_label}, {entity_uri}

The following is the paper content for your task: 

{paper_content}'''.replace('{PROMPT_CORE}', PROMPT_CORE)

 