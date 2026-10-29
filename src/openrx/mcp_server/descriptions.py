"""What the model is told about OpenRx: the server instructions and the two tools.

These are the model's only documentation (docs/design.md §8.4), and they count against the
1,500-token budget for tool definitions (NFR-1), so every line has to earn its place.
Everything else the model needs is in the catalog, which it reads through search.

The examples are separate constants because they are code: an integration test runs
each one in the deployed runner, so the model is never shown an example that fails.
"""

INSTRUCTIONS = """\
OpenRx: public FDA and NIH drug data from openFDA (adverse events, labels, recalls, \
NDC, approvals, shortages), ClinicalTrials.gov, PubMed, RxNorm (drug names and \
classes) and DailyMed. Work in two steps: search the catalog for the endpoint and its \
query syntax, then execute Python that fetches and processes the data. Code runs in a \
sandbox and only its final value comes back, so filter and summarise in code. \
Research use only, not medical advice."""

SEARCH_EXAMPLE = """\
hits = catalog_find("adverse events")
ep = catalog_endpoint(hits[0]["source"], hits[0]["id"])
{"path": ep["path"], "params": [p["name"] for p in ep["params"]]}"""

SEARCH_DESCRIPTION = f"""\
Explore the API catalog with Python. Returns the value of the last expression; \
print() output is captured.

catalog_find(text, limit=10): endpoints matching the words, best first
catalog_endpoint(source, id, full_example=False): params, notes, example request and \
response
catalog_guide(source): the source's query syntax; read it before writing queries
catalog_endpoints(source=None), catalog_sources()

Example:
{SEARCH_EXAMPLE}"""

EXECUTE_EXAMPLE = """\
r = await request("openfda", "/drug/event.json", {
    "search": 'patient.drug.openfda.generic_name:"metformin"',
    "count": "patient.reaction.reactionmeddrapt.exact", "limit": 5})
[(t["term"], t["count"]) for t in r["data"]["results"]]"""

EXECUTE_DESCRIPTION = f"""\
Fetch and process data with Python. Returns the value of the last expression, so \
return something small (filter, count, pick fields); print() output is captured. The \
catalog_* functions work here too.

await request(source, path, params=None) -> {{"status", "data", "truncated", "notes"}}
  path: the endpoint path from the catalog. params: a dict of plain values (spaces, \
not "+").
  Raises RuntimeError("<code>: <message>") when rejected. At most 50 calls per run; \
use asyncio.gather for several.

Example:
{EXECUTE_EXAMPLE}

Not available: class inheritance, @property, generators, statistics, heapq, \
operator, csv. Available: json, datetime, re, collections, itertools, math, asyncio."""
