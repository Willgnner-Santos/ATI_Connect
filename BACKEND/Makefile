# Local convenience commands. Deploys remain explicit in docs/ops/scripts.md.
PYTHON ?= python
SAM ?= sam
STAGE ?= sandbox
REGION ?= sa-east-1

.PHONY: help test validate build local-template build-local layer local-api
help:
	@echo "make test            Unit tests only; no AWS calls"
	@echo "make validate        Validate the SAM template"
	@echo "make build           Build application handlers"
	@echo "make local-template  Generate the local-only SAM template"
	@echo "make build-local     Build handlers and layer in Docker"
	@echo "make layer           Build only the local layer in Docker"
	@echo "make local-api       Start API after a local build (uses real AWS resources)"
	@echo "Deploy instructions: docs/ops/scripts.md"

test:
	$(PYTHON) -m pytest tests_unit -q

validate:
	$(SAM) validate --template-file template.yaml --region $(REGION)

build:
	$(SAM) build --template-file template.yaml --config-env $(STAGE)

local-template:
	$(PYTHON) scripts/generate-local-template.py

build-local: local-template
	$(SAM) build --template-file template.local.yaml --config-env $(STAGE) --use-container

layer: local-template
	$(SAM) build PlataformaAtiLayerLocal --template-file template.local.yaml --config-env $(STAGE) --use-container --build-dir .aws-sam/layer-build

local-api:
	$(SAM) local start-api --template-file .aws-sam/build/template.yaml --config-env $(STAGE)
