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

**Critical**: VesselEnvironment must be atmosphere, never a mixture of solvents etc. 

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
1) All ontosyn:ChemicalInput instances (excluding common solvents) and link them via ontosyn:hasChemicalInput. For each input, add alternative names (ontosyn:hasAlternativeNames), chemical formula (ontosyn:hasChemicalFormula), detailed description (ontosyn:hasChemicalDescription), purity (ontosyn:hasPurity), and supplier (ontosyn:isSuppliedBy) if explicitly mentioned. If a reference to an external material is provided, link using ontosyn:referencesMaterial.
2) A single ontosyn:ChemicalOutput instance for this synthesis and set it via ontosyn:hasChemicalOutput. If a representation is explicitly stated, link it using ontosyn:isRepresentedBy.
Write all triples to output.ttl.
3) Supplier handling: If a supplier is explicitly mentioned, reuse the existing supplier IRI (after check_existing_suppliers). Otherwise attach the canonical supplier named "N/A" (reuse if it already exists). Do not loop on "N/A".

Critical constraints:
- Do NOT include common solvents as ChemicalSynthesis inputs; they are handled at the step level in Iteration 3.
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

Task:
Using only the extracted information, for each synthesis step in each ontosyn:ChemicalSynthesis, create an instance of the appropriate step class (Add, Stir, HeatChill, Evaporate, Sonicate, Dissolve, Crystallize, Transfer, Separate, Filter, Dry, SeparationType). Link each step to its parent ChemicalSynthesis via ontosyn:hasSynthesisStep. For each step, set ontosyn:hasOrder and add all step-specific properties (vessels, vessel environments, duration, equipment, and type-specific parameters). Write all triples to output.ttl.

Solvents:
- For Add steps, attach Solvent individuals (ONTOSPECIES:Solvent) via ontosyn:hasAddedChemicalInput.
- For Dissolve/Separate/Filter steps, use step-specific solvent properties (e.g., ontosyn:hasSolventDissolve, ontosyn:hasSeparationSolvent, ontosyn:hasWashingSolvent).
- Never create solvents as ChemicalInput. Use create_solvent + add_chemical_to_add_step instead.

Add steps:
- One Add step per material. Do not combine multiple materials into one Add step.
- For each ontosyn:ChemicalInput linked to the synthesis, create a separate Add step and call add_chemical_to_add_step with chemical_input_iri.
- For each solvent added as a material, create a separate Add step and call add_chemical_to_add_step with an existing solvent_iri when possible, or mint a new solvent by name (not "N/A") if missing.

Vessels, Vessel Environments, Durations:
- They are important components of the steps, you should create them if they are mentioned in the paper.
- Make sure you include all of them. 
- Make sure you add them to the step.

Vessel assignment logic:
- Vessel 1 = the first vessel where reagents are initially charged and mixed at ambient conditions. Assign Vessel 1 to all steps before any transfer/aliquot leaves this vessel.
- Vessel 2 = the destination vessel that first receives a transferred portion/aliquot from Vessel 1 for crystallization or subsequent treatment.
- Every physical transfer to a new container increments the vessel id (Vessel 3, 4, …). The source vessel keeps its original id.
- If no transfer occurs, do not create a new vessel id.
- Container typing:
  - Initial mixing vessel is typically a round-bottom flask or equivalent; label as the mixing vessel.
  - Crystallization vessel is typically a small vial when heated/open or capped in air; label as vial.
  - Use sealed tube when the procedure indicates evacuation, sealing, or running under vacuum/inert with a flame-seal or similar.
- Atmosphere flags:
  - Open/capped in air → sealed=False, underVacuum=False, inert=False.
  - Sealed/evacuated/flame-sealed → sealed=True, underVacuum=True.
  - Inert gas purge/fill → inert=True; also sealed=True if closed during heating.
- On aliquot splits, each distinct destination gets the next vessel id. Track downstream steps by the vessel id they occur in.
- Do not renumber vessels later. Maintain ids consistently across all subsequent steps.

Atmosphere and agitation (patch):
- Vials:
  - Capped vial ≠ sealed. Set vessel_environment="air", sealed_status=False, underVacuum=False.
  - Do not infer inert or vacuum without explicit text.
- Sealed tubes (flame-sealed):
  - After evacuation and flame-seal: sealed_status=True, underVacuum=True.
  - If inert gas fill is explicit: inert=True. Keep underVacuum=False unless evacuation is maintained.
- Agitation:
  - Use Stir only when agitation is explicitly stated.
  - Model long room-temperature holds without stirring as Wait/Hold at the stated temperature (or Stir with isWait=true when Wait/Hold is unavailable).

Ordering:
- Maintain contiguous ontosyn:hasOrder values across all steps.

Error handling:
- Respect JSON stop conditions. If a post-call response indicates already_attached==true or retryable==false, do not call the same tool again with identical args.

Termination:
- When all required steps and properties are DONE or SKIPPED, emit <"run_status":"done">.

==============

This is the top level entity for you to focus on during this iteration. 
{entity_label}, {entity_uri}

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
Using only the extracted information, for each ontosyn:ChemicalSynthesis, add the yield using ontosyn:hasYield, and link all equipment used (ontosyn:hasEquipment). Ensure explicit links to the source document are included via ontosyn:retrievedFrom. Write all triples to output.ttl.

Constraints:
- Apply check_existing_* tools before creating or linking to avoid duplicates.
- Obey JSON stop conditions and do not repeat identical calls if already_attached==true or retryable==false.

Use prior iteration context (if provided):
- If iter3 step hints for this entity are present in the input context, treat them as authoritative for vessels, atmospheres, and step ordering. Reuse vessel ids and do not renumber.

Termination:
- When all yields, equipment links, and provenance links are DONE or SKIPPED, emit <"run_status":"done">.

==============

This is the top level entity for you to focus on during this iteration. 
{entity_label}, {entity_uri}

The following is the paper content for your task: 

{paper_content}'''.replace('{PROMPT_CORE}', PROMPT_CORE)

 