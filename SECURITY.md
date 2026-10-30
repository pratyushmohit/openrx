# Security policy

OpenRx runs model-written code, so its isolation matters: the Monty sandbox, the
`request()` bridge as the only way out, and API keys that never enter the sandbox
(see [docs/architecture.md](docs/architecture.md)).

## Reporting a vulnerability

Please report it privately through
[GitHub's private vulnerability reporting](https://github.com/pratyushmohit/openrx/security/advisories/new),
not in a public issue. Include what you found, how to reproduce it, and what an attacker
could do with it. You'll get a reply within a week.

Especially interesting: code that escapes the sandbox, reaches the network or files
without `request()`, calls an endpoint outside the catalog, gets past the call budget or
rate limits, or reveals an API key.

## Supported versions

Only the latest release on `main` gets fixes.
