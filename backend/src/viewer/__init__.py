"""Measurement viewer backend."""
from importlib.metadata import PackageNotFoundError, version as _version

try:
    __version__ = _version("viewer")
except PackageNotFoundError:  # running from a source tree without installation
    __version__ = "0.0.0"
