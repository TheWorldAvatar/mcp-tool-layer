import os
import hashlib
from typing import Tuple


def generate_hash(doi: str) -> str:
    return hashlib.sha256(doi.encode()).hexdigest()[:8]


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def get_paths_for_hash(hash_value: str) -> Tuple[str, str]:
    """Return (hash_dir, output_dir) under DATA_DIR for CBU derivation outputs."""
    from models.locations import DATA_DIR
    hash_dir = os.path.join(DATA_DIR, hash_value)
    output_dir = os.path.join(hash_dir, "cbu_derivation")
    ensure_dir(output_dir)
    return hash_dir, output_dir


def resolve_identifier_to_hash(identifier: str) -> str:
    """If identifier looks like DOI, convert to hash; else assume it's hash."""
    if '.' in identifier or '/' in identifier:
        return generate_hash(identifier)
    return identifier

