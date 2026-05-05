from src.agents.scripts_and_prompts_generation.task_extraction_prompt_creation_agent import generate_prompts_from_iterations
ok = generate_prompts_from_iterations(['medical'], tbox_path_map={'medical': 'medical_case/medical_case_schema_de_non_flat_v3.ttl'})
print('EXTRACTION_OK', ok)
