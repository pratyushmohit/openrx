"""Lambda entry point for the search-runner. The logic lives in openrx.runner.handler."""

from openrx.runner.handler import search_handler as handler

__all__ = ["handler"]
