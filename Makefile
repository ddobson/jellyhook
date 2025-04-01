.PHONY: lint typecheck test

lint:
	uv run ruff check --config ruff.toml .

format:
	uv run ruff check --select I --fix --config ruff.toml .
	uv run ruff format --config ruff.toml .

typecheck:
	uv run mypy .

test:
	@echo "Running tests for api and workers directories..."
	@for dir in api workers; do \
		echo "Testing $${dir}..."; \
		cd $${dir} && uv run pytest && cd ..; \
	done
