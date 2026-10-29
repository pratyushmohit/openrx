"""The runners: execute model-written code in Monty, inside Lambda only (D-005).

search-runner offers the catalog_* functions; execute-runner adds request(), which
goes through the bridge. Only the final result returns to the model (docs/design.md §8.5).
"""
