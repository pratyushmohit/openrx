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
export AWS_ENDPOINT_URL ?= http://localhost:4566

TF := terraform -chdir=infra/envs/floci

.PHONY: help up up-ui down logs ps smoke-bedrock tf-init tf-plan tf-apply tf-destroy

help: ## List available commands
	@grep -E "^[a-z-]+:.*## " $(MAKEFILE_LIST) | awk -F ":.*## " "{printf \"  %-15s %s\n\", \$$1, \$$2}"

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

smoke-bedrock: ## Send one Bedrock Converse call through Floci to Ollama
	aws bedrock-runtime converse --model-id openrx-local --messages file://scripts/smoke-messages.json --query "{answer: output.message.content[0].text, usage: usage}"

tf-init: ## Download the Terraform providers
	$(TF) init

tf-plan: ## Show what Terraform would change in Floci
	$(TF) plan

tf-apply: ## Create/update resources in Floci
	$(TF) apply -auto-approve

tf-destroy: ## Delete all Terraform-managed resources from Floci
	$(TF) destroy -auto-approve
