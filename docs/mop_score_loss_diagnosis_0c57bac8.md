# MOP score-loss diagnosis: `0c57bac8`

This note separates extraction, generated-MCP/KG-building, and conversion
failures for DOI `10.1021/acsami.7b18836`.

## Score snapshot

- CBU: F1 `1.000`
- Characterisation: F1 `0.824` (`FN=6`)
- Steps: F1 `0.722` (`FP=24`, `FN=23`)
- Chemicals: F1 `0.526` (`FN=9`)

The primary bottlenecks are KG linking/materialisation, extraction completeness,
then merge/conversion.

## 1. PubChem alias enrichment did not run

Iteration 2 requests `pubchem`, `enhanced_websearch`, and `ccdc` through
`configs/meta_task/ontosynthesis_iterations_blueprint.json` and
`configs/chemistry.json`. The PubChem MCP server exists at
`data/third_party_repos/PubChem-MCP-Server/pubchem_server.py` and exposes name,
SMILES, CID, and advanced lookup tools with synonyms.

For this run, however,
`src/pipelines/main_ontology_extractions/extract.py` deliberately changed
`use_agent` to false whenever `ccdc` or `enhanced_websearch` was present. The
runtime records under
`data/0c57bac8/responses/iter2_extraction/UMC-{1,2}.md` therefore say
`Mode: Simple LLM`. No PubChem tool call occurred.

The result is visible in
`data/0c57bac8/mcp_run/iter2_hints_UMC-{1,2}.txt`: inputs use short labels such
as `Cp2ZrCl2`, `DMF`, `H2O`, and `MeOH`, but omit names expected by the full
ground truth, including:

- `bis(cyclopentadienyl)zirconium dichloride`
- `N,N-dimethylformamide`
- `water`
- `methanol`
- `4,4'-sulfonyldibenzoic acid`
- `4,4'-methylenedibenzoic acid`

There is a second schema bottleneck: the generated ChemicalInput materialisation
contract does not currently accept `hasAlternativeNames` or
`hasChemicalFormula`. Merely enabling the PubChem process would not preserve
the enrichment through KG building.

Recommended follow-up:

1. Replace the unconditional portability fallback with a real MCP health check.
2. Require recorded PubChem tool activity when alias enrichment is configured.
3. Include T-Box-supported alias/formula fields in extraction and constructor
   contracts, with deterministic deduplication and provenance.

Implementation status (2026-07-23):

- PubChem and Serper were tested both as direct backends and through MCP stdio.
- A real BaseAgent ReAct run executed both `search_pubchem_by_name` and
  `google_search`, returning DMF CID 6228, `N,N-dimethylformamide`, `C3H7NO`,
  the `DMF` synonym, and nine web results.
- The unconditional external-tool fallback was removed. Runtime startup
  failures may still use the existing exception-based fallback.
- Iteration 2 now requires recorded execution of a PubChem lookup tool and
  writes executed tool names/counts into the extraction response.
- Anonymous `owl:unionOf` datatype-property domains are expanded during T-Box
  parsing, so `hasAlternativeNames`, `hasChemicalFormula`, and
  `hasChemicalDescription` reach generated ChemicalInput/ChemicalOutput
  constructors and materializable hint contracts.

## 2. Missing OM-2 quantities are a generated-script defect

The OntoSynthesis T-Box declares object ranges such as:

- `hasStepDuration -> om-2:Duration`
- `hasTargetTemperature -> om-2:Temperature`

`build_generation_contract_bundle` records these as
`om2_quantity_properties`. Generated agentic scripts nevertheless used
OntoSyn-local `NS.Duration`/`NS.Temperature` nodes and a weak
`_add_quantity_label_metadata` parser. They did not enforce proper OM-2
quantity classes or canonical unit IRIs.

The case also demonstrates prompt-content degradation:

- `iter3_base_hints_UMC-1.txt` contains `hasStepDuration_label: "3 min"`,
  `hasStepDuration_label: "8 h"`, and
  `hasTargetTemperature_label: "60 °C"`.
- `iter3_hints_UMC-1.txt` collapses them to unstructured
  `hasParameter` strings.
- `ontosynthesis_output/UMC-1.ttl` therefore contains no OM-2 duration or
  temperature objects.
- `evaluation/data/full_result/steps/error_details/{duration,targetTemperature}.md`
  reports the corresponding values as `n/a`.

This is both a content-prompt regression and a script-generation validation gap.
The script layer must be capable of converting valid quantity labels even when
the agent supplies them correctly. Level-1 validation and the semantic A-Box
gate now need to require OM-2 typing, `om-2:hasNumericalValue`, and
`om-2:hasUnit`.

## 3. Add-to-chemical corruption is an enforcement failure

This was not primarily an LLM failure to call the link tool.

- `mcp_run/iter3_base_hints_UMC-1.txt` correctly asks for
  `Add DMF -> hasAddedChemicalInput DMF`.
- The intermediate/memory graph contains that relationship.
- During publication, same-order member reconciliation selected winners using
  a score/IRI tie-break. Correct DMF Add nodes were removed, while duplicate
  MeOH Add nodes survived.
- In `ontosynthesis_output/UMC-1.ttl`, DMF remains a synthesis-level
  ChemicalInput but is orphaned from every Add step.

The T-Box comment and generated contract already classify
`hasAddedChemicalInput` as required for Add. Existing
`post_publish_warnings.typed_nodes_missing_predicate` only warns when an Add has
no predicate at all. It cannot detect a surviving Add linked to the wrong input,
and it does not protect a semantically better node from order-conflict pruning.

Recommended follow-up:

1. Make ordered-member conflict scoring preserve required object links and
   source-label agreement before considering IRI ordering.
2. After reconciliation, reject graphs where hinted Add/input pairs are absent,
   inputs become unexpectedly orphaned, or two Adds collapse onto the wrong
   input.
3. Promote required-link warnings to a hard semantic gate when the contract
   marks the property required.

## Prompt enhancement versus semantic repair

Semantic repair remains responsible for code and graph structure: valid
properties, ranges, OM-2 structures, required links, and reasoner consistency.

Prompt enhancement is a separate content loop:

1. Generate a mock document together with source-grounded gold hints.
2. Run the actual extraction and KG prompts through the ReAct pipeline.
3. Compare predicted hints with gold leaf facts and compare the resulting graph
   with an oracle graph materialised from gold hints.
4. Feed content mismatches only to the prompt agent.
5. Repeat while retaining HermiT and generated-script validation as independent
   gates.

This separation prevents a content omission such as `sealedVessel` or a PubChem
alias from being misclassified as a Python repair, while still catching
structural defects such as non-OM-2 quantity nodes.
