FROM python:3.13-slim

ARG ENV="production"
# Set automatically by buildx (amd64 | arm64); selects the matching dovi_tool release asset.
ARG TARGETARCH

# Install system dependencies, ffmpeg, mkvtoolnix, dovi_tool, and uv
# Remove curl after installation to reduce image size
# Clean up apt cache and lists to reduce image size
RUN case "${TARGETARCH:-amd64}" in \
        amd64) DOVI_ARCH="x86_64" ;; \
        arm64) DOVI_ARCH="aarch64" ;; \
        *) echo "Unsupported architecture: ${TARGETARCH}" >&2; exit 1 ;; \
    esac && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
    ffmpeg \
    mkvtoolnix \
    curl && \
    curl -fL "https://github.com/quietvoid/dovi_tool/releases/download/2.1.3/dovi_tool-2.1.3-${DOVI_ARCH}-unknown-linux-musl.tar.gz" | tar xz -C /usr/local/bin && \
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

# Copy project files (uv.lock pins the exact dependency versions)
COPY pyproject.toml uv.lock README.md ./
COPY src ./src/

# Install python dependencies exactly as locked (--frozen: never re-resolve)
RUN if [ "$ENV" = "development" ]; then \
        uv export --frozen --no-hashes > requirements.txt; \
    else \
        uv export --frozen --no-hashes --no-dev > requirements.txt; \
    fi && \
    pip3 install --no-cache-dir -r requirements.txt && \
    rm requirements.txt

# Run the application
CMD [ "python3", "-m", "workers.main" ]
