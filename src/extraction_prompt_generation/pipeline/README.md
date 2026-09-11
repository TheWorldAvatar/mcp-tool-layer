# pipeline

Public orchestration that the CLI calls. Submodules are imported lazily from
`__init__.py` so compile-only `--stage context` does not pull the authoring
stack.

## Files

| File | Role |
| --- | --- |
| `experiment.py` | End-to-end run: compile context → scaffold slots → model author → report |
| `prompts.py` | Deterministic EXTRACTION / PRE_EXTRACTION scaffolds (emptied before authoring) |
| `plan.py` | Project compiled iterations into the runtime `iterations.json` shape |
| `formatters.py` | Live `_format_*` contract text used by scaffolds and `.inc` companions |
| `runtime_support.py` | Write `iterations.json`, `_namespace.json`, and flatten RDF into `_fixed_rdf_runtime.py` |
| `legacy.py` | Unused formatters kept after the package split; do not add new callers |

`generate_deterministic_prompt_slice` writes the slot files and compiled
companions. When `llm_agent_generation` is on, `experiment.py` then blanks
the `.md` files so every final line is model-authored.
