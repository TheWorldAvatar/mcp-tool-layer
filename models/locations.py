import os

# Optional: load .env if python-dotenv is available
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except Exception:
    pass

ROOT_DIR = os.getenv("ROOT_DIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.getenv("DATA_DIR", os.path.join(ROOT_DIR, "data"))
RAW_DATA_DIR = os.getenv("RAW_DATA_DIR", os.path.join(ROOT_DIR, "raw_data"))
CONFIGS_DIR = os.getenv("CONFIGS_DIR", os.path.join(ROOT_DIR, "configs"))
DATA_LOG_DIR = os.getenv("DATA_LOG_DIR", os.path.join(DATA_DIR, "log"))
# Allow overriding the canonical CCDC data directory via env
DATA_CCDC_DIR = os.getenv("DATA_CCDC_DIR", os.path.join(DATA_DIR, "ontologies", "ccdc"))

def check_dir_exists(dir_path):
    if not os.path.exists(dir_path):
        # raise an error
        raise FileNotFoundError(f"Directory {dir_path} does not exist, please create it first")

check_dir_exists(ROOT_DIR)
check_dir_exists(DATA_DIR)
check_dir_exists(RAW_DATA_DIR)
check_dir_exists(CONFIGS_DIR)
check_dir_exists(DATA_LOG_DIR)