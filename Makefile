REGISTRY ?= your-registry.gatesfoundation.org
IMAGE    ?= email-agent
VERSION  ?= $(shell git rev-parse --short HEAD 2>/dev/null || echo "dev")

.PHONY: help run test build push deploy-staging deploy-prod logs status rollback

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

run: ## Run locally (development)
	uvicorn app.main:app --reload --port 8000

test: ## Run all tests
	pytest tests/ -v

build: ## Build Docker image
	docker build -t $(IMAGE):$(VERSION) -t $(IMAGE):latest .

push: build ## Build and push to registry
	docker tag $(IMAGE):$(VERSION) $(REGISTRY)/$(IMAGE):$(VERSION)
	docker tag $(IMAGE):latest $(REGISTRY)/$(IMAGE):latest
	docker push $(REGISTRY)/$(IMAGE):$(VERSION)
	docker push $(REGISTRY)/$(IMAGE):latest

deploy-staging: push ## Deploy to staging
	nomad job run -var="version=$(VERSION)" -var="env=staging" nomad/email-agent.nomad.hcl

deploy-prod: push ## Deploy to production (5s confirmation)
	@echo "⚠️  Deploying $(VERSION) to PRODUCTION. Ctrl+C to cancel."
	@sleep 5
	nomad job run -var="version=$(VERSION)" -var="env=production" nomad/email-agent.nomad.hcl

logs: ## Tail production logs
	@nomad alloc logs -f $$(nomad job status email-agent 2>/dev/null | grep running | head -1 | awk '{print $$1}')

status: ## Check deployment status
	nomad job status email-agent

rollback: ## Rollback to previous version
	nomad job revert email-agent 0
	@echo "Rolled back. Check status with: make status"

docker-run: build ## Run in Docker locally (test the container)
	docker run --rm -p 8000:8000 --env-file .env $(IMAGE):latest