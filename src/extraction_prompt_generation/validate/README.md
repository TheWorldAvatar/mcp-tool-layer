# validate

Mechanical checks on generated prompts and, when present, generated MCP
script packages. This package does not author files.

Prompt-time gates used during exact-edits live in
`generate/extraction_prompts/contracts/`. This package assembles the
durable `generation_report.json`.

## Files

| File | Role |
| --- | --- |
| `creator_atomicity.py` | Contract-derived probes for generated entity creators |
| [`report/`](report/README.md) | Report assembly and per-surface checkers |
