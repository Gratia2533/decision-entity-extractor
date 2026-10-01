"""Installed distribution identity."""

from importlib.metadata import PackageNotFoundError, version

try:
    PACKAGE_VERSION = version("entity-resolution")
except PackageNotFoundError:  # Source-tree import before installation.
    PACKAGE_VERSION = "2.0.0"

__all__ = ["PACKAGE_VERSION"]
