"""The bridge: the only component that holds API keys and calls the public APIs.

Model-written code never runs here. It reaches the bridge only through `request()`,
and every request is checked against the catalog before anything is sent (README §8.6).
"""
