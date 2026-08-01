# Validated ontology-to-tools compilation

This document is a figure-design source for the ontology-to-tools compilation and qualification workflow. It is intentionally separate from the LaTeX manuscript so that the procedure can be redrawn as a PowerPoint figure.

## Inputs and output

**Inputs**

- `T`: ontology T-Box.
- `M`: domain-agnostic compilation meta-prompts.
- `B`: maximum number of revision attempts.

**Output**

- A qualified package containing the task plan, runtime prompts, ontology-aware functions, adapters, MCP server, and typed tool schemas; or
- A failure report if no candidate passes within the revision budget.

## Pseudocode

```text
PROCEDURE COMPILE_ONTOLOGY_TO_TOOLS(T, M, B)

    CANDIDATE GENERATION

    1. Parse T into an ontology contract C containing:
       - classes and properties;
       - domains, ranges, and datatypes;
       - required links and cardinalities;
       - ordering and quantity rules;
       - imported ontology concepts; and
       - operational annotations.

    2. Generate candidate package A from C and M:
       - executable task plan;
       - extraction prompts;
       - knowledge-graph construction prompts;
       - ontology-aware creation and relation functions;
       - persistent graph-store adapter;
       - MCP server; and
       - typed MCP tool schemas.

    3. Set BEST_CHECKPOINT to NONE.


    REVISION AND QUALIFICATION LOOP

    4. For REVISION from 0 to B:

        LEVEL 1 — PACKAGE VALIDATION

        4.1. Validate A for:
             - formatting and syntax;
             - importability;
             - MCP registration and tool exposure;
             - function signatures and required tool coverage;
             - conformance to C;
             - isolation from unrelated ontology symbols; and
             - deterministic runtime probes.

        4.2. If package validation fails:
             a. Convert the failures into focused observations.
             b. Select the smallest authorised dependent file set.
             c. Propose an exact patch tied to the current file hashes.
             d. Apply the complete patch transaction.
             e. If the transaction is stale, ambiguous, unauthorised,
                or invalid, roll it back.
             f. Start the next revision.


        LEVEL 2 — MOCK A-BOX EXECUTION

        4.3. Build:
             - a mock source document D*; and
             - source-grounded expected hints H* covering critical
               classes, links, and slots.

        4.4. Execute the actual runtime prompts and tools on D* to obtain:
             - predicted hints H_hat; and
             - candidate A-Box G_hat.

        4.5. Independently materialise oracle A-Box G* from H* using
             the generated constructors.


        GRAPH AND REASONING GATE

        4.6. Validate G_hat against T for:
             - known ontology vocabulary;
             - domain and range conformance;
             - required shell links;
             - reachability from the top entity;
             - complete quantity structures where relevant;
             - OWL-RL constraints; and
             - HermiT consistency.

        4.7. If graph or reasoning validation fails:
             a. Route the focused observations to script/adapter repair.
             b. Patch only authorised scripts or adapters.
             c. Apply the patch transaction or roll back.
             d. Start the next revision.


        CONTENT GATE

        4.8. Compare:
             - H_hat with H*; and
             - G_hat with G*.

        4.9. Reject the candidate if it contains:
             - critical omissions;
             - forbidden or spurious facts;
             - misclassified content;
             - insufficient content scores; or
             - regressions relative to BEST_CHECKPOINT.

        4.10. If content validation fails:
              a. Route the focused observations to prompt repair.
              b. Patch only extraction or KG-construction prompts.
              c. Apply the patch transaction or roll back.
              d. Start the next revision.


        ACCEPTANCE

        4.11. Freeze A as BEST_CHECKPOINT.
        4.12. Return A as QUALIFIED.


    FAILURE

    5. If no candidate passes within B revisions:
       - return the complete validation report; and
       - do not promote or deploy the unqualified candidate.

END PROCEDURE
```

## Suggested figure layout

Use a left-to-right or top-to-bottom flow with five principal stages:

1. **Inputs and contract parsing**
   - T-Box and domain-agnostic meta-prompts.
   - Parsed ontology contract.

2. **Candidate generation**
   - Task plan, prompts, functions, adapters, MCP server, and schemas.

3. **Level 1: package validation**
   - Code, interface, contract, exposure, and runtime-probe checks.
   - Failure arrow to transactional script/adapter patching.

4. **Level 2: mock A-Box evaluation**
   - Mock document and expected hints.
   - Runtime execution produces predicted hints and candidate A-Box.
   - Expected hints produce the oracle A-Box.
   - Two parallel gates:
     - graph/reasoning gate;
     - content-comparison gate.

5. **Qualification decision**
   - Pass: freeze checkpoint and qualify package.
   - Fail with budget remaining: route to the appropriate repair channel.
   - Budget exhausted: return failure report without deployment.

Use two visually distinct feedback loops:

- **Structural/semantic failure → scripts and adapters**
- **Content failure → extraction and KG prompts**

Transactional patching should appear between each failure route and the next validation attempt, with explicit **apply atomically / rollback** and **revision-hash check** annotations.
