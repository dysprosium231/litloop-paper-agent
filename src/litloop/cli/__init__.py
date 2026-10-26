"""CLI package — commands grouped by domain.

The actual command implementations live in ``commands.py``.
This package exists so future command modules can be added under ``cli/``
without changing the public import path.
"""

# Re-export the Typer app and all commands from the core module
from .commands import app  # noqa: F401

# Trigger command registration by importing the core module
from . import commands  # noqa: F401
