"""
DAISE core package.

Exposes the five-engine cognitive model and the
top-level DAISE orchestration class.
"""

from .core import DAISE  # noqa: F401
from .reasoning_pipeline import install_daise_v1_cutover

install_daise_v1_cutover(DAISE)
