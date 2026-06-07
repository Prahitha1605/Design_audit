"""Compatibility shim package for design-audit-agent.

This package inserts the parent project directory onto its own __path__ so
that the existing modules stored in the project root (agent/, models/, etc.)
are discoverable as subpackages under `design_audit_agent` during tests.
"""
from pathlib import Path

# Prepend the project root (parent of this directory's parent) to package path
__path__.insert(0, str(Path(__file__).resolve().parent.parent))

__all__ = ["agent", "models"]
