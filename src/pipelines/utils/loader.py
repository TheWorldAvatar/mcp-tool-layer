"""Module loading utilities"""


def load_step_module(step_name: str):
    """
    Dynamically load a pipeline step module.
    
    Args:
        step_name: Name of the step (e.g., 'pdf_conversion')
        
    Returns:
        Module with run_step function, or None if loading failed
    """
    try:
        module_path = f"src.pipelines.{step_name}"
        module = __import__(module_path, fromlist=['run_step'])
        
        if not hasattr(module, 'run_step'):
            print(f"❌ Step module {step_name} missing run_step function")
            return None
        
        return module
    except ImportError as e:
        print(f"❌ Failed to load step module '{step_name}': {e}")
        return None

