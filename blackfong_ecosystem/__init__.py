"""Blackfong Ecosystem Package
==============================

This package contains components for the O.B.E.O.S (Official Blackfong Ecosystem
Operating System) desktop shell prototype and other helper modules.  The
directory is structured as a Python package so that modules inside can be
imported via the ``blackfong_ecosystem`` namespace.  It does not currently
re‑export any symbols but serves to mark the directory as a package.

Note:
    Most of the core system modules (e.g. ``memory_lifecycle_management.py``,
    ``decision_quality_control.py``) reside in the project root rather than
    within this package.  The desktop shell includes logic to locate those
    modules regardless of how it is invoked.  Additional modules may be moved
    into this package in the future as the project matures.
"""

# The package exposes no public API at this time.
__all__: list[str] = []