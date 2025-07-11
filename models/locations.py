import os

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT_DIR, "data")
DATA_TEST_DIR = os.path.join(DATA_DIR, "test")
DATA_LOG_DIR = os.path.join(DATA_DIR, "log")
DATA_GENERIC_DIR = os.path.join(DATA_DIR, "generic_data")
SANDBOX_DIR = os.path.join(ROOT_DIR, "sandbox")
SANDBOX_CONFIGS_DIR = os.path.join(SANDBOX_DIR, "configs")
SANDBOX_CODE_DIR = os.path.join(SANDBOX_DIR, "code")
SANDBOX_DATA_DIR = os.path.join(SANDBOX_DIR, "data")
SANDBOX_TASK_DIR = os.path.join(SANDBOX_DIR, "tasks")
SANDBOX_TASK_ARCHIVE_DIR = os.path.join(SANDBOX_TASK_DIR, "archive")

PLAYGROUND_DIR = os.path.join(ROOT_DIR, "playground")
PLAYGROUND_DATA_DIR = os.path.join(PLAYGROUND_DIR, "data")

CONFIGS_DIR = os.path.join(ROOT_DIR, "configs")

TRACE_FILE_PATH = os.path.join(SANDBOX_TASK_DIR, "task_tracing.json")


def check_dir_exists(dir_path):
    if not os.path.exists(dir_path):
        # raise an error
        raise FileNotFoundError(f"Directory {dir_path} does not exist, please create it first")

check_dir_exists(ROOT_DIR)
check_dir_exists(DATA_DIR)
check_dir_exists(DATA_TEST_DIR)
check_dir_exists(DATA_LOG_DIR)
check_dir_exists(SANDBOX_DIR)
check_dir_exists(SANDBOX_CODE_DIR)
check_dir_exists(SANDBOX_DATA_DIR)
check_dir_exists(SANDBOX_TASK_DIR)
check_dir_exists(SANDBOX_TASK_ARCHIVE_DIR)
check_dir_exists(PLAYGROUND_DIR)
check_dir_exists(PLAYGROUND_DATA_DIR)
check_dir_exists(CONFIGS_DIR)
