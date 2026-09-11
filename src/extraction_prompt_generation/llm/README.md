# llm

Remote model I/O used by planning, reuse judgment, exact-edits, and prompt
semantic review. Default model name is `gpt-5`; other names are allowed.

The only supported edit backend is `exact_edits`. There is no unified-diff
or whole-file rewrite path.

## Files

| File | Role |
| --- | --- |
| `invoke.py` | JSON chat completions via `models.LLMCreator` |
| `artifact_editor.py` | Dispatch one artifact edit to the exact-edits backend |
| `exact_edits.py` | Propose / apply / validate structured exact edits |
| `editor_retry.py` | Field-schema vs semantic retry fingerprints |
| `invocation_journal.py` | Hard timeout + durable JSONL event log under `generated/` |
