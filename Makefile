# Developer commands for OpenRx. Run `make help` to list them.

# Dummy credentials: Floci accepts any values, but the AWS CLI refuses to run
# without some. Exported so every recipe (and the tools it calls) sees them.
export AWS_ACCESS_KEY_ID ?= test
export AWS_SECRET_ACCESS_KEY ?= test
export AWS_DEFAULT_REGION ?= us-east-1
export AWS_ENDPOINT_URL ?= http://localhost:4566

.PHONY: help up up-ui down logs ps smoke-bedrock

help: ## List available commands
	@grep -E "^[a-z-]+:.*## " $(MAKEFILE_LIST) | awk -F ":.*## " "{printf \"  %-15s %s\n\", \$$1, \$$2}"

up: ## Start Floci (local AWS emulator)
	docker compose up -d

up-ui: ## Start Floci plus the web console at http://localhost:4500
	docker compose --profile ui up -d

down: ## Stop all containers, including the console
	docker compose --profile ui down

logs: ## Follow Floci logs
	docker compose logs -f floci

ps: ## Show container status
	docker compose --profile ui ps

smoke-bedrock: ## Send one Bedrock Converse call through Floci to Ollama
	aws bedrock-runtime converse --model-id openrx-local --messages file://scripts/smoke-messages.json --query "{answer: output.message.content[0].text, usage: usage}"
