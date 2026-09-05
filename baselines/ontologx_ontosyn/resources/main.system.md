This file is no longer the live OntoLogX prompt.

The runner now builds the system prompt from our OntoSynthesis T-Box:

    data/ontologies/ontosynthesis_parsed.md

via `baselines/ontologx_ontosyn/prompt_builder.py`.
The generated copy is written to `resources/ontosyn_system.generated.md` at run time.

Do not paste the original OntoLogX log-event prompt here.
