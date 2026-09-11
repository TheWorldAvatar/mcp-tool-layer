# Extraction prompt generation

One method: the configured model (default `gpt-5`) writes a full extraction
prompt for each planned iteration.

The frozen 0903 prompt-contract English lives in
[`contracts/`](contracts/README.md). Do not rewrite those English strings.

## What is generated

- `EXTRACTION_ITER_1.md` for a main ontology (top-entity pass)
- `EXTRACTION_ITER_N.md` for each planned semantic iteration
- `PRE_EXTRACTION_ITER_N.md` only when that slot requires a closed ledger
- `*.materializable.inc` compiled next to those prompts (not authored)

## What is not generated

- `KG_BUILDING_ITER_*.md`
- `KG_BUILDING_ITER_*_ONEPASS.md`
- Any second extraction style or one-pass extraction variant

## Runtime slots

Main-ontology extraction prompts bind `{paper_content}` (and iteration-specific
slots from the contract).

Extension extraction prompts bind only `{entity_label}` and `{entity_uri}`.
They must not invent parent-graph placeholders.

## Files

| File | Role |
| --- | --- |
| `author.py` | Track entry: call shared exact-edits with `generate_prompts=True` |
| `slots.py` | Create empty `.md` slots via `pipeline.prompts` |
| `review.py` | Re-export the prompt-only semantic reviewer |

## How to run

```powershell
python -m src.extraction_prompt_generation ontosynthesis --tag gpt5_r1 --stage prompts
```

`--stage all` and `--stage prompts` both run this track.
