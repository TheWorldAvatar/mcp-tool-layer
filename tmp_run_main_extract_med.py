from src.pipelines.main_ontology_extractions.extract import run_step
cfg = {"data_dir": "data", "meta_task_config": "configs/meta_task/meta_task_config_medical_non_flat_v3.json"}
print(run_step("ec5d5219", cfg))
