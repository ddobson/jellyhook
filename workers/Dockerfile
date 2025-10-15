FROM python:3.13-slim

ARG ENV="production"

# Install system dependencies, ffmpeg, mkvtoolnix, dovi_tool, and uv
# Remove curl after installation to reduce image size
# Clean up apt cache and lists to reduce image size
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ffmpeg \
    mkvtoolnix \
    curl && \
    curl -L https://github.com/quietvoid/dovi_tool/releases/download/2.1.3/dovi_tool-2.1.3-x86_64-unknown-linux-musl.tar.gz | tar xz -C /usr/local/bin && \
    chmod +x /usr/local/bin/dovi_tool && \
    curl -L https://astral.sh/uv/install.sh -o /uv-installer.sh && \
    sh /uv-installer.sh && \
    rm /uv-installer.sh && \
    apt-get purge -y curl && \
    rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Ensure the installed binary is on the `PATH`
ENV PATH="/root/.local/bin/:$PATH"

# Copy poetry files
COPY pyproject.toml README.md ./
COPY src ./src/

# Install python dependencies
RUN if [ "$ENV" = "development" ]; then \
        uv export --no-hashes > requirements.txt; \
    else \
        uv export --no-hashes --no-dev > requirements.txt; \
    fi && \
    pip3 install --no-cache-dir -r requirements.txt && \
    rm requirements.txt

# Run the application
CMD [ "python3", "-m", "workers.main" ]
