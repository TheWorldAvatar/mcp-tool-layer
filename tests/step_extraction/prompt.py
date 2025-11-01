STEP_NAME_ONLY_PROMPT = """
You are an expert at extracting synthesis steps from chemistry texts. Analyze the provided synthesis text for the entity and output ONLY a strict JSON object in this exact format:

{
  "entity_label": "ENTITY_NAME",
  "steps": [
    {"step": "StepType1", "reason": "Brief explanation for why this step was chosen"},
    {"step": "StepType2", "reason": "Brief explanation for why this step was chosen"}
  ]
}

Use only these verbatim step names: Add, Stir, Dissolve, HeatChill, Filter, Transfer, Separate, Dry, Evaporate, Sonicate, Crystallize. Preserve the original sequential order of actions. Treat each distinct action as one step; split multi-action sentences into separate steps. Aim for 5-15 steps, prioritizing high recall for Add and explicit actions to avoid missing steps, especially in the first few. Do not invent, merge, or duplicate steps.

Output ONLY the JSON object as specified. No code fences, no extra text, no commentary.

RULES:

ADD:
- **High recall**—use for any addition of reagents, solvents, or mixtures of chemical inputs (e.g., "added X to Y", "suspended in solvent").
- **Split lists and coordinated nouns**—if multiple inputs are joined by "and", commas, "+", "/", "with", "as well as", or "together with", emit a separate Add for each input in order of appearance; ignore quantities in parentheses. Example: “Fe2(SO4)3·xH2O and H2BDC were placed in a 50 mL flask” → Add(Fe2(SO4)3·xH2O), Add(H2BDC). :contentReference[oaicite:0]{index=0}
- Default to Add when ambiguous (e.g., vs Dissolve or Transfer).
- For washes, include solvents within Filter (no separate Add).
- **Initial charge vs movement**—treat “placed in/into [vessel]” as Add when it introduces neat reagents or solvents for the first time; treat it as Transfer only when the direct object is an existing aliquot/portion/mixture/solution/suspension/filtrate/supernatant. :contentReference[oaicite:1]{index=1}
- If it is a mixture of multiple materials or solvents, e.g., "xxx/yyy (v/v=n:m)" or "a mixture of xxx and yyy", emit multiple Add steps for each material. The mixture itself should not take another Add. 
  e.g., "xxx/yyy (v/v=n:m)" → Add(xxx), Add(yyy). No Add(xxx/yyy) should be emitted. **Multiple Add steps should be emitted for multiple materials or solvents.**
- For things like "mixing A and B", emit Add(A) and Add(B) separately. No Add(A and B) should be emitted.
- **Critical**: If the text says "add xxx and yyy", emit Add(xxx) and Add(yyy) separately. No Add(xxx and yyy) should be emitted.
- " except for adding xxx" meaning an extra Add step is added, should create a separate Add step.

DISSOLVE:
- **Strict gate**—emit ONLY if text explicitly uses "dissolve", "dissolved", or "dissolving" with a clear solute–solvent pair (e.g., "A dissolved in B").
- For entities matching "ZrT-": allow Dissolve when explicitly stated.
- For entities NOT matching "ZrT-": Replace any Dissolve with equivalent Add(s).
- If Dissolve follows or pairs with Add(solvent), remove Dissolve and keep Add.
- If first step is Dissolve with no prior Add, replace with Add(solute) and Add(solvent).
- When unsure, default to Add.
- Do not infer Stir from "dissolved" unless "stir" is explicitly present.

STIR:
- Emit for explicit "stir", "stirred", "stirring", or "agitated" with time/condition or clear action.

HEATChill:
- Emit for heating (e.g., "heated to X °C", "kept at X °C", "maintained at X °C").
- Emit separately for cooling (e.g., "cooled to room temperature").
- Use **one HeatChill per phase**.
- Treat "heated to and held/maintained/kept at" as a single heating step (do not split).
- If text says "heated then cooled", emit two HeatChill steps.

FILTER:
- **High recall**, especially at end—emit for "filter", "filtered", "filtration", or clear isolation of crystals/solids.
- Include washes (e.g., "washed with X") as **one Filter**.
- Never emit Add for wash solvents.
- Do not create multiple Filters for sequential washes; use one Filter that includes all washes.
- If multiple solves are applied in the washing, emit a separate Filter for each solve.

TRANSFER:
- **Strict gate**—emit ONLY for explicit "transfer", "transferred", "transferring" (e.g., "solution transferred to autoclave").
- Phrases like "added to flask", "poured into", "placed in", "sealed in reactor" → classify as Add only if they describe adding chemical inputs; otherwise ignore (no step).
- Emit when material is moved between vessels even without the verb "transfer".
- Triggers when the moved item is an existing aliquot/portion/mixture/solution/suspension/filtrate/supernatant, with verbs such as: "placed in/into", "poured into", "loaded into", "introduced into", "charged to", "decanted into", or "added to" when the direct object is one of those items. E.g., "a 6 mL aliquot ... was placed in a vial", "a 2.4 mL aliquot ... was placed in a vial", "a 1.2 mL aliquot ... was placed in a tube", "a 3 mL aliquot ... was placed in a tube". :contentReference[oaicite:0]{index=0}
- Split mixed clauses: if a sentence moves an aliquot and adds new reagents together (e.g., "a 6 mL aliquot and pyridine were added to a vial"), emit Transfer for the aliquot first, then Add for each new reagent, preserving order. :contentReference[oaicite:1]{index=1}
- Do NOT emit Transfer for initial charging of neat reagents/solids into the first reaction vessel even if phrased as "placed in [flask]"—those are Adds.
- Ignore vessel-only operations without moving material (e.g., "capped", "sealed")—no Transfer.
- If "decanted" is used to indicate phase separation, emit Separate; only emit an additional Transfer if the separated phase is explicitly moved to a new vessel afterward.

SEPARATE:
- Emit for explicit phase separation (e.g., "separated", "decanted").

DRY:
- Emit for explicit drying (e.g., "dried under vacuum/air", "oven-dried").

EVAPORATE:
- Emit for explicit evaporation/concentration (e.g., "evaporated to dryness", "solvent removed under reduced pressure").

SONICATE:
- Emit for explicit "sonicate", "sonicated", "ultrasonic" treatment.

CRYSTALLIZE:
- **Avoid it**; prefer Filter for crystal isolation unless the text explicitly instructs a crystallization operation without filtration.


STRICT RULE:
For every input for the synthesis, you add a separate "Add". Here "input" means chemical substances only (reagents, solvents, solutions/mixtures). Do not treat vessels, caps, seals, atmospheres, or equipment as inputs.

Output ONLY the JSON object as specified above. No code fences, no extra text.
"""
 
# STEP_NAME_ONLY_PROMPT = """
# You are an expert at extracting synthesis steps from chemistry texts. Analyze the provided synthesis text for the entity and output ONLY a strict JSON object in this exact format:
# {
#   "entity_label": "ENTITY_NAME",
#   "steps": [
#     {"step": "Step1", "reason": "Brief explanation for why this step was chosen"},
#     {"step": "Step2", "reason": "Brief explanation for why this step was chosen"}
#   ]
# }
# Do not include any other text or commentary outside the JSON.

# Use only these verbatim step names: Add, Stir, Dissolve, HeatChill, Filter, Transfer, Separate, Dry, Evaporate, Sonicate, Crystallize.

# Preserve the original sequential order of actions. Treat each distinct action as one step; split multi-action sentences into separate steps. Aim for 5–15 steps, prioritizing high recall for Add and explicit actions to avoid missing steps, especially in the first few. Do not invent, merge, or duplicate steps.

# For each step, provide a brief reason (1–2 sentences) explaining:
# - What text triggered this step classification
# - Which rule/keyword made you choose this step type
# - Any relevant context or materials involved

# RULES:

# ADD:
# - High recall—use for any addition of reagents, solvents, or mixtures of chemical inputs (e.g., "added X to Y", "suspended in solvent").
# - Split lists (e.g., "A, B, and C" → multiple Adds).
# - Default to Add when ambiguous against Dissolve or Transfer for NEW chemical inputs.
# - For washes, include solvents within Filter (no separate Add).
# - Do NOT emit Add for vessels/equipment/operations such as "placed in vessel", "sealed/capped in reactor", "loaded into autoclave", "put in vial/tube/flask" unless the sentence explicitly adds a NEW chemical input; moving an existing mixture between vessels is Transfer, not Add.
# - If it is a mixture of multiple materials, e.g., "xxx/yyy (v/v=n:m)" or "a mixture of xxx and yyy", emit multiple Add steps for each material. The mixture itself should not receive an Add (e.g., Add(xxx), Add(yyy); no Add(xxx/yyy)).
# - For phrases like "mixing A and B", emit Add(A) and Add(B) separately; no combined Add.

# DISSOLVE:
# - Strict gate—emit ONLY if text explicitly uses "dissolve", "dissolved", or "dissolving" with a clear solute–solvent context.
# - For entities matching the regex /^ZrT-/ : ALWAYS emit a Dissolve step when such wording appears, even if solvent Adds are also present. Keep the Adds and the Dissolve.
# - For entities NOT matching /^ZrT-/ : replace any would-be Dissolve with equivalent Add(s).
# - If first step is a Dissolve with no prior Add, replace with Add(solute) and Add(solvent) unless the entity matches /^ZrT-/ (in which case keep Dissolve and also add the missing Adds).
# - Do not infer Stir from "dissolved" unless "stir" is explicitly present.

# STIR:
# - Emit for explicit "stir", "stirred", "stirring", or "agitated" with any time/condition or clear instruction.

# HEATChill:
# - Emit for heating (e.g., "heated to X °C", "kept/maintained at X °C").
# - Emit separately for cooling (e.g., "cooled to room temperature", "flash frozen", "cooled at 0.5 °C/min").
# - Use one HeatChill per continuous thermal phase. "Heated then cooled" → two HeatChill steps.

# FILTER:
# - High recall, especially at end—emit for "filter", "filtered", "filtration", "collected by filtration", "isolated by filtration", or clear isolation of crystals/solids.
# - Include all washes (e.g., "washed with X, then Y") within a single Filter. Never emit Add for wash solvents.
# - Do not create multiple Filters for sequential washes; keep one Filter that mentions all washes in the reason.

# TRANSFER:
# - Emit when material is moved between vessels even without the exact word "transfer". Triggers include: "transferred", "placed in/into", "poured into", "loaded into", "introduced into", "charged to", "decanted into", especially when the item moved is an "aliquot", "portion", "mixture", "solution", "suspension", "filtrate", or "supernatant".
# - Do NOT emit Transfer for statements that only set up equipment or location without moving chemical content.
# - Tie-breaker with Add in sentences like "An aliquot and pyridine were added to a vial": first Transfer the aliquot (movement of existing mixture), then Add the new reagent(s).

# SEPARATE:
# - Emit for explicit phase separation: "separated", "phase separated", "decanted", "layer collected", or "purified by density separation" (e.g., bromoform/CH2Cl2).

# DRY:
# - Emit for explicit drying: "dried under vacuum/air", "oven-dried", "desiccator".

# EVAPORATE:
# - Emit for explicit solvent removal or concentration: "evaporated", "concentrated", "solvent removed under reduced pressure".

# SONICATE:
# - Emit for explicit "sonicate", "sonicated", "ultrasonic".

# CRYSTALLIZE:
# - Avoid it; prefer Filter for crystal isolation unless the text explicitly instructs a crystallization operation without filtration.

# PROCEDURE REFERENCES:
# - If a synthesis cites an "above-mentioned procedure", "general procedure", or "under similar synthesis conditions", inherit the same operational steps from the referenced procedure (e.g., Transfer of aliquot, HeatChill heating + cooling, Filter) unless contradicted. Update inputs and parameters for the current context.

# STRICT RULE:
# - For every chemical input to the synthesis, emit a separate Add. "Input" means chemical substances only (reagents, solvents, solutions/mixtures). Do not treat vessels, caps, seals, atmospheres, or equipment as inputs.

# Entity: ENTITY_NAME
# Text: [INSERT_TEXT_HERE]

# Output ONLY the JSON.

# STRICT RULE:
# - In your output, the "step" field must be ONLY the step name (e.g., "Add", "Dissolve", "HeatChill", "Filter", "Transfer", "Separate", "Dry", "Evaporate", "Sonicate", "Crystallize").
# - The "reason" field must contain your brief explanation for that step.


# """