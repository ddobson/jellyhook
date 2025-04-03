.PHONY: lint typecheck test build tag push

lint:
	@echo "Running linting for api and workers directories..."
	@for dir in api workers; do \
		echo "Linting $${dir}..."; \
		cd $${dir} && uv run ruff check --config ../ruff.toml . && cd ..; \
	done

format:
	@echo "Formatting api and workers directories..."
	@for dir in api workers; do \
		echo "Formatting $${dir}..."; \
		cd $${dir} && uv run ruff check --select I --fix --config ../ruff.toml . && \
		uv run ruff format --config ../ruff.toml . && cd ..; \
	done

typecheck:
	@echo "Type checking api and workers directories..."
	@for dir in api workers; do \
		echo "Type checking $${dir}..."; \
		(cd "$${dir}" && uv run mypy --config-file=../mypy.ini . || true); \
	done

test:
	@echo "Running tests for api and workers directories..."
	@for dir in api workers; do \
		echo "Testing $${dir}..."; \
		cd $${dir} && uv run pytest && cd ..; \
	done

all: format lint typecheck test

# Docker commands
build_api: DOCKERFILE ?= api/Dockerfile
build_api: TAG ?= latest
build_api:
	@echo "Building API image jellyhook:$(TAG)-api..."
	docker buildx build \
		--no-cache \
		--platform linux/amd64,linux/arm64 \
		--tag jellyhook:$(TAG)-api \
		--file $(DOCKERFILE) ./api

build_worker: DOCKERFILE ?= workers/Dockerfile
build_worker: TAG ?= latest
build_worker:
	@echo "Building Worker image jellyhook:$(TAG)-worker..."
	docker buildx build \
		--no-cache \
		--platform linux/amd64,linux/arm64 \
		--tag jellyhook:$(TAG)-worker \
		--file $(DOCKERFILE) ./workers

tag_api: TAG ?= latest
tag_api:
	@[ "${ACCOUNT}" ] || ( echo "Error: ACCOUNT is required. Usage: make tag_api ACCOUNT=your-docker-account TAG=version" && exit 1 )
	@echo "Tagging API image for $(ACCOUNT)..."
	docker tag jellyhook:$(TAG)-api $(ACCOUNT)/jellyhook:$(TAG)-api

tag_worker: TAG ?= latest
tag_worker:
	@[ "${ACCOUNT}" ] || ( echo "Error: ACCOUNT is required. Usage: make tag_worker ACCOUNT=your-docker-account TAG=version" && exit 1 )
	@echo "Tagging Worker image for $(ACCOUNT)..."
	docker tag jellyhook:$(TAG)-worker $(ACCOUNT)/jellyhook:$(TAG)-worker

push_api: TAG ?= latest
push_api: build_api tag_api
	@[ "${ACCOUNT}" ] || ( echo "Error: ACCOUNT is required. Usage: make push_api ACCOUNT=your-docker-account TAG=version" && exit 1 )
	@echo "Pushing API image to $(ACCOUNT)/jellyhook:$(TAG)-api..."
	docker push $(ACCOUNT)/jellyhook:$(TAG)-api

push_worker: TAG ?= latest
push_worker: build_worker tag_worker
	@[ "${ACCOUNT}" ] || ( echo "Error: ACCOUNT is required. Usage: make push_worker ACCOUNT=your-docker-account TAG=version" && exit 1 )
	@echo "Pushing Worker image to $(ACCOUNT)/jellyhook:$(TAG)-worker..."
	docker push $(ACCOUNT)/jellyhook:$(TAG)-worker

# Convenience target to build and push both
push_all: push_api push_worker
