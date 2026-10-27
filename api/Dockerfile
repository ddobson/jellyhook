FROM python:3.13-slim

ARG ENV="production"

WORKDIR /app

# Install build dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
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

# Copy project files (uv.lock pins the exact dependency versions)
COPY pyproject.toml uv.lock README.md ./
COPY src ./src/

# Install python dependencies exactly as locked (--frozen: never re-resolve)
RUN if [ "$ENV" = "development" ]; then \
        uv export --frozen > requirements.txt; \
    else \
        uv export --frozen --no-dev > requirements.txt; \
    fi && \
    uv pip install --system --no-cache-dir -r requirements.txt && \
    rm requirements.txt

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

# Uses the stdlib so the image needs no curl at runtime.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=3)"]

# Command to run the application
# 2 workers x 4 threads: a slow RabbitMQ connect no longer blocks every other webhook (a burst
# from a library scan), and the 60s timeout outlasts pika's connect timeout. Logs go to stdout.
CMD ["gunicorn", "api.main:app", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "2", \
     "--threads", "4", \
     "--timeout", "60", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
