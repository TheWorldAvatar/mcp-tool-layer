import os

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT_DIR, "data")
RAW_DATA_DIR = os.path.join(ROOT_DIR, "raw_data")
CONFIGS_DIR = os.path.join(ROOT_DIR, "configs")
DATA_LOG_DIR = os.path.join(DATA_DIR, "log")

def check_dir_exists(dir_path):
    if not os.path.exists(dir_path):
        # raise an error
        raise FileNotFoundError(f"Directory {dir_path} does not exist, please create it first")

check_dir_exists(ROOT_DIR)
check_dir_exists(DATA_DIR)
check_dir_exists(RAW_DATA_DIR)
check_dir_exists(CONFIGS_DIR)
check_dir_exists(DATA_LOG_DIR)