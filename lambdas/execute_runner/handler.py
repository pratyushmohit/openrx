"""Lambda entry point for the execute-runner. The logic lives in openrx.runner.handler."""

from openrx.runner.handler import execute_handler as handler

__all__ = ["handler"]
