.PHONY: lint format format-check typecheck test build tag push

lint:
	@echo "Running linting for api and workers directories..."
	@for dir in api workers; do \
		echo "Linting $${dir}..."; \
		cd $${dir} && uv run --active ruff check --config ../ruff.toml . && cd ..; \
	done

format:
	@echo "Formatting api and workers directories..."
	@for dir in api workers; do \
		echo "Formatting $${dir}..."; \
		cd $${dir} && uv run --active ruff check --select I --fix --config ../ruff.toml . && \
		uv run --active ruff format --config ../ruff.toml . && cd ..; \
	done

format-check:
	@echo "Checking formatting for api and workers directories..."
	@for dir in api workers; do \
		echo "Checking format for $${dir}..."; \
		cd $${dir} && uv run --active ruff check --select I --config ../ruff.toml . && \
		uv run --active ruff format --check --config ../ruff.toml . && cd ..; \
	done

typecheck:
	@echo "Type checking api and workers directories..."
	@for dir in api workers; do \
		echo "Type checking $${dir}..."; \
		(cd "$${dir}" && uv run --active mypy --config-file=../mypy.ini . || true); \
	done

test:
	@echo "Running tests for api and workers directories..."
	@for dir in api workers; do \
		echo "Testing $${dir}..."; \
		cd $${dir} && uv run --active pytest && cd ..; \
	done

test_cov: REPORT ?= html
test_cov:
	@echo "Running tests for api and workers directories..."
	@for dir in api workers; do \
		echo "Testing $${dir}..."; \
		cd $${dir} && \
		uv run --active pytest \
			--cov=$${dir} \
			--cov-branch \
			--cov-report=$(REPORT) \
			tests && cd ..; \
	done

test_component: TAG ?= 0.1.0
test_component:
	@echo "Running component tests for api and workers directories..."
	docker run --rm \
		jellyhook-testing:$(TAG) \
		python3 -m pytest tests/component/ --cov=. --cov-report=xml

scan:
	ggshield secret scan repo .

all: format lint typecheck test

# Docker commands
build_api: DOCKERFILE ?= api/Dockerfile
build_api: TAG ?= latest
build_api:
	@echo "Building API image jellyhook-api:$(TAG)..."
	docker buildx build \
		--no-cache \
		--platform linux/amd64 \
		--tag jellyhook-api:$(TAG) \
		--load \
		--file $(DOCKERFILE) ./api

build_worker: DOCKERFILE ?= workers/Dockerfile
build_worker: TAG ?= latest
build_worker:
	@echo "Building Worker image jellyhook-worker:$(TAG)..."
	docker buildx build \
		--no-cache \
		--platform linux/amd64 \
		--tag jellyhook-worker:$(TAG) \
		--load \
		--file $(DOCKERFILE) ./workers

tag_api: TAG ?= latest
tag_api: REGISTRY ?= ghcr.io
tag_api:
	@[ "${ACCOUNT}" ] || ( echo "Error: ACCOUNT is required. Usage: make tag_api ACCOUNT=your-account [REGISTRY=registry-url] [TAG=version]" && exit 1 )
	@echo "Tagging API image for $(REGISTRY)/$(ACCOUNT)..."
	docker tag jellyhook-api:$(TAG) $(REGISTRY)/$(ACCOUNT)/jellyhook-api:$(TAG)

tag_worker: TAG ?= latest
tag_worker: REGISTRY ?= ghcr.io
tag_worker:
	@[ "${ACCOUNT}" ] || ( echo "Error: ACCOUNT is required. Usage: make tag_worker ACCOUNT=your-account [REGISTRY=registry-url] [TAG=version]" && exit 1 )
	@echo "Tagging Worker image for $(REGISTRY)/$(ACCOUNT)..."
	docker tag jellyhook-worker:$(TAG) $(REGISTRY)/$(ACCOUNT)/jellyhook-worker:$(TAG)

push_api: TAG ?= latest
push_api: REGISTRY ?= ghcr.io
push_api: build_api tag_api
	@[ "${ACCOUNT}" ] || ( echo "Error: ACCOUNT is required. Usage: make push_api ACCOUNT=your-account [REGISTRY=registry-url] [TAG=version]" && exit 1 )
	@echo "Pushing API image to $(REGISTRY)/$(ACCOUNT)/jellyhook-api:$(TAG)..."
	docker push $(REGISTRY)/$(ACCOUNT)/jellyhook-api:$(TAG)

push_worker: TAG ?= latest
push_worker: REGISTRY ?= ghcr.io
push_worker: build_worker tag_worker
	@[ "${ACCOUNT}" ] || ( echo "Error: ACCOUNT is required. Usage: make push_worker ACCOUNT=your-account [REGISTRY=registry-url] [TAG=version]" && exit 1 )
	@echo "Pushing Worker image to $(REGISTRY)/$(ACCOUNT)/jellyhook-worker:$(TAG)..."
	docker push $(REGISTRY)/$(ACCOUNT)/jellyhook-worker:$(TAG)

build_testing: DOCKERFILE ?= workers/Dockerfile.testenv
build_testing: TAG ?= 0.1.0
build_testing:
	@echo "Building Testing image jellyhook-testing:$(TAG)..."
	docker buildx build \
		--no-cache \
		--platform linux/amd64,linux/arm64 \
		--tag jellyhook-testing:$(TAG) \
		--load \
		--file $(DOCKERFILE) ./workers

tag_testing: TAG ?= 0.1.0
tag_testing: REGISTRY ?= ghcr.io
tag_testing:
	@[ "${ACCOUNT}" ] || ( echo "Error: ACCOUNT is required. Usage: make tag_worker ACCOUNT=your-account [REGISTRY=registry-url] [TAG=version]" && exit 1 )
	@echo "Tagging Testing image for $(REGISTRY)/$(ACCOUNT)..."
	docker tag jellyhook-testing:$(TAG) $(REGISTRY)/$(ACCOUNT)/jellyhook-testing:$(TAG)

push_testing: TAG ?= 0.1.0
push_testing: REGISTRY ?= ghcr.io
push_testing: build_worker tag_worker
	@[ "${ACCOUNT}" ] || ( echo "Error: ACCOUNT is required. Usage: make push_worker ACCOUNT=your-account [REGISTRY=registry-url] [TAG=version]" && exit 1 )
	@echo "Pushing Testing image to $(REGISTRY)/$(ACCOUNT)/jellyhook-testing:$(TAG)..."
	docker push $(REGISTRY)/$(ACCOUNT)/jellyhook-testing:$(TAG)

# Convenience target to build and push both
push_all: push_api push_worker
