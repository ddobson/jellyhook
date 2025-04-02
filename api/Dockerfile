FROM python:3.13-slim

ARG ENV="production"

WORKDIR /app

# Install build dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    ca-certificates \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install uv using the official installer
# Download the latest installer
ADD https://astral.sh/uv/install.sh /uv-installer.sh

# Run the installer then remove it
RUN sh /uv-installer.sh && rm /uv-installer.sh

# Ensure the installed binary is on the `PATH`
ENV PATH="/root/.local/bin/:$PATH"

# Copy project files
COPY pyproject.toml README.md ./
COPY src ./src/

# Install python dependencies
RUN if [ "$ENV" = "development" ]; then \
        uv export > requirements.txt && \
        uv pip install --system --no-cache-dir -r requirements.txt && \
        rm requirements.txt; \
    else \
        uv pip install --system --no-cache-dir .; \
    fi

# Create log directory and set permissions
RUN mkdir -p /config/logs && \
    chmod -R 777 /config

# Create non-root user for running the application
RUN useradd -m appuser
USER appuser

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV FLASK_ENV=production

# Expose the port the app runs on
EXPOSE 8000

# Command to run the application
CMD ["gunicorn", "api.main:app", "--bind", "0.0.0.0:8000"]
