"""Repository and package roots for the migrated OntoLogX package."""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
# ontologx → kg_building → src → repository root
REPO_ROOT = HERE.parents[2]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))
