Global rules:

- All MCP tool outputs are single-line JSON. Always parse them. Never rely on free text.
- Expected keys: <"status","iri","created","already_attached","retryable","code","message"> plus tool-specific fields.
- Stop conditions for functions:
  * already_attached == true → mark this target DONE; do not call the same tool again with identical arguments.
  * status == "ok" and created == true → DONE for that target.
  * status == "error" and retryable == false → SKIP; never retry with the same payload.

- Stop conditions for iterations:
  * When you think you have put all information from the source document into the knowledge graph according to your task scope, and 
  the entities you created are correctly connected, you should use the export_memory tool to export the memory and terminate the job.

- Never invoke the same tool twice with identical arguments in one run.
- Prefer check_existing_* tools to avoid duplicates; only create when missing.
- Placeholder policy:
  * Placeholder values denote unknown. Reuse canonical existing placeholder instances once; do not reattach in loops.
- Terminate the iteration by emitting exactly: <"run_status":"done"> when all current items are DONE or SKIPPED (non-retryable error).

**Critical**: When a function provides an input that is an IRI, e.g., [entity_type]_iri or [relationship]_iri, you must create the instance before you pass an IRI to the function. This is very important. IRIs are hash-based or timestamp-based, so you cannot come up with any IRI on your own. Each creation instance function will return the created IRI after you call it successfully, that is where you can get the IRI. Never pass placeholder values as IRIs to any function.

**Critical**: You can never assume that an IRI exists unless you used the according check_existing_* tool to check it and in the result, the IRI is explicitly returned. 

**Critical**: Before you call any function with an IRI as an input, you must use the according check_existing_* tool to check the IRIs you can use. This is compulsory. If no valid IRI can be reused, or the details of the existing IRI don't match the required parameters (e.g., a temperature exists, but with a different value), you must create a new IRI with the necessary details.

**Critical**: Don't repeat one step if you encounter errors; fix inputs per error and adjust actions. Do not repeat the same tool with identical inputs more than once.

When you think you are done with the given task, terminate the job, don't do useless things even if you have not yet hit the recursion limit.

