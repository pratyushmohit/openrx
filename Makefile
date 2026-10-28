# Developer commands for OpenRx. Run `make help` to list them.

# On Windows, make runs recipes with cmd.exe, which lacks grep, rm, etc.
# Use Git Bash instead so recipes behave the same on every OS. Simple commands
# (no ; | && etc.) skip the shell and are looked up on PATH directly, so Git's
# tools folder (rm, grep, ...) must be on PATH as well.
ifeq ($(OS),Windows_NT)
SHELL := C:/Program Files/Git/bin/bash.exe
export PATH := C:\Program Files\Git\usr\bin;$(PATH)
endif

# Dummy credentials: Floci accepts any values, but the AWS CLI refuses to run
# without some. Exported so every recipe (and the tools it calls) sees them.
export AWS_ACCESS_KEY_ID ?= test
export AWS_SECRET_ACCESS_KEY ?= test
export AWS_DEFAULT_REGION ?= us-east-1
# 127.0.0.1, not localhost: on Windows + Docker Desktop, "localhost" adds a fixed
# ~43 ms to every request (docs/m0-findings.md, E4).
export AWS_ENDPOINT_URL ?= http://127.0.0.1:4566

TF := terraform -chdir=infra/envs/floci

.PHONY: help check lint lint-catalog typecheck test test-integration bench-bridge bench-runners deploy up up-ui down logs ps build tf-init tf-plan tf-apply tf-destroy

help: ## List available commands
	@grep -E "^[a-z-]+:.*## " $(MAKEFILE_LIST) | awk -F ":.*## " "{printf \"  %-15s %s\n\", \$$1, \$$2}"

check: lint lint-catalog typecheck test ## Run all code checks (what CI will run)

lint: ## Lint and check formatting with ruff
	uv run ruff check .
	uv run ruff format --check .

lint-catalog: ## Check the API catalog (YAML + examples)
	uv run openrx catalog lint

typecheck: ## Static type check with pyright
	uv run pyright

test: ## Run unit tests with coverage
	uv run pytest --cov --cov-report=term-missing

test-integration: ## Run tests against the deployed Lambdas on Floci (needs make up + deploy)
	uv run pytest -m integration -p no:cacheprovider

bench-bridge: ## Measure the deployed bridge's overhead in replay mode (docs/slos.md)
	uv run python scripts/bench_bridge.py

bench-runners: ## Measure the deployed runners' sandbox overhead (docs/slos.md)
	uv run python scripts/bench_runners.py

# Floci keeps state in memory, so recreating the container wipes every AWS
# resource. Terraform's state must be wiped too, or it would believe the old
# resources still exist. Run `make tf-apply` afterwards to recreate them.
#
# --renew-anon-volumes: the Floci image declares /app/data as a volume, and on
# recreate compose would otherwise carry the old container's volume over.
up: ## Start a clean Floci (wipes all emulated resources)
	docker compose up -d --force-recreate --renew-anon-volumes floci
	rm -f infra/envs/floci/terraform.tfstate infra/envs/floci/terraform.tfstate.backup

up-ui: up ## Same as `up`, plus the web console at http://localhost:4500
	docker compose --profile ui up -d floci-ui

down: ## Stop all containers, including the console
	docker compose --profile ui down

logs: ## Follow Floci logs
	docker compose logs -f floci

ps: ## Show container status
	docker compose --profile ui ps

build: ## Build a Lambda zip: make build LAMBDA=<name> (from lambdas/<name>/)
	@test -n "$(LAMBDA)" || { echo "usage: make build LAMBDA=<name>"; exit 1; }
	bash scripts/build_lambda.sh $(LAMBDA)

# Terraform needs the zips to exist (it hashes them), so build before applying.
deploy: ## Build the Lambda zips and apply Terraform to Floci
	$(MAKE) build LAMBDA=bridge
	$(MAKE) build LAMBDA=search_runner
	$(MAKE) build LAMBDA=execute_runner
	$(TF) apply -auto-approve

tf-init: ## Download the Terraform providers
	$(TF) init

tf-plan: ## Show what Terraform would change in Floci
	$(TF) plan

tf-apply: ## Create/update resources in Floci
	$(TF) apply -auto-approve

tf-destroy: ## Delete all Terraform-managed resources from Floci
	$(TF) destroy -auto-approve
