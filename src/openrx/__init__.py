"""OpenRx: an MCP server with sandboxed code execution for public FDA/NIH drug data."""

from importlib.metadata import version

# Single source of truth for the version is pyproject.toml.
__version__ = version("openrx")
