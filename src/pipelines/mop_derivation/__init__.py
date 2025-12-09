"""
MOP Derivation Module

Derives Chemical Building Units (CBUs) - both metal and organic - from CCDC files and paper content,
then integrates them to derive complete MOP formulas.
"""

from .derive import run_step

__all__ = ["run_step"]

