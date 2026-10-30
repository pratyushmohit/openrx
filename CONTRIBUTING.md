# Contributing to OpenRx

Thanks for your interest. Issues, ideas and pull requests are all welcome. For questions
or a design idea you'd like to talk through first, start a
[discussion](https://github.com/pratyushmohit/openrx/discussions).

## Set up

You need Python 3.13, [uv](https://docs.astral.sh/uv/), Docker and Terraform. Ollama is
only needed to run the agent and the evals.

```bash
uv sync          # install dependencies
make check       # lint, format check, type check, unit tests: what CI runs
```

Integration tests run against the Lambdas deployed on Floci, a local AWS emulator:

```bash
make up && make deploy
make test-integration
```

`make help` lists every command.

## How changes are made here

- **Small pull requests**, one idea each, with a message that says why.
- **Tests in the same change.** Every behaviour change comes with unit tests; code that
  touches the Lambdas also gets an integration test.
- **`make check` passes** before you open the PR (ruff, pyright and pytest; CI runs the
  same thing).
- **Design decisions are written down.** If a change picks between real alternatives,
  add an entry to [`docs/decisions.md`](docs/decisions.md) with the options and the
  reason.

## Good places to start

- **Add an API source or endpoint.** The catalog lives in
  [`src/openrx/catalog/sources/`](src/openrx/catalog/sources/), one YAML file per source.
  Add the endpoint with a query guide and an example response, then run
  `make lint-catalog`.
- **Add eval tasks.** Tasks are in [`evals/tasks.yaml`](evals/tasks.yaml). Run
  `make eval-record` to record the data they need and compute the expected answers.
- **Try another model.** Run the evals with `uv run openrx eval run --model <model>` and
  compare with the results in [`results/`](results/).

Issues labelled
[`good first issue`](https://github.com/pratyushmohit/openrx/labels/good%20first%20issue)
are scoped to be done in one sitting.

## Security

Please don't report vulnerabilities in public issues; see [SECURITY.md](SECURITY.md).

## Code of conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md).
