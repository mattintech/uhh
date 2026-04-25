"""uhh — ask a local LLM for the command you forgot."""
from importlib.metadata import PackageNotFoundError, version

from .cli import main

try:
    __version__ = version("uhh")
except PackageNotFoundError:
    __version__ = "0+unknown"

__all__ = ["main", "__version__"]
