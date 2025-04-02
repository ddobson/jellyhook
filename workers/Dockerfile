FROM python:3.12-slim

ARG ENV="production"

# Install system dependencies
RUN apt-get update && apt-get install -y gpg apt-transport-https ca-certificates && \
    apt-get update && apt-get install -y \
    ffmpeg \
    mkvtoolnix \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install dovi_tool
RUN curl -L https://github.com/quietvoid/dovi_tool/releases/download/2.1.3/dovi_tool-2.1.3-x86_64-unknown-linux-musl.tar.gz | tar xz -C /usr/local/bin \
    && chmod +x /usr/local/bin/dovi_tool

# Set working directory
WORKDIR /app

# Install uv
ADD https://astral.sh/uv/install.sh /uv-installer.sh

# Run the installer then remove it
RUN sh /uv-installer.sh && rm /uv-installer.sh

# Ensure the installed binary is on the `PATH`
ENV PATH="/root/.local/bin/:$PATH"

# Copy poetry files
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

# Run the application
CMD [ "uv", "run", "-m", "workers.main" ]
