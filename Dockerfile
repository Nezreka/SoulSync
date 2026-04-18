# SoulSync WebUI Dockerfile
# Multi-architecture support for AMD64 and ARM64

# Stage 1: Builder — install Python dependencies with compilation tools
FROM python:3.11-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libc6-dev \
    libffi-dev \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Create virtualenv and install dependencies
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Stage 2: Runtime — only runtime dependencies, no build tools
FROM python:3.11-slim

# Build-time commit SHA for update detection
ARG COMMIT_SHA=""
ENV SOULSYNC_COMMIT_SHA=${COMMIT_SHA}

# Copy pre-built virtualenv from builder
COPY --from=builder /opt/venv /opt/venv
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Set working directory
WORKDIR /app

# Install runtime-only system dependencies (no gcc/build tools)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gosu \
    ffmpeg \
    libchromaprint-tools \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user for security
RUN useradd --create-home --shell /bin/bash --uid 1000 soulsync

# Copy application code
COPY . .

# Create necessary directories with proper permissions
# NOTE: /app/data is for database FILES, /app/database is the Python package
RUN mkdir -p /app/config /app/data /app/logs /app/downloads /app/Transfer /app/MusicVideos /app/scripts && \
    chown -R soulsync:soulsync /app

# Create defaults directory and copy template files
# These will be used by entrypoint.sh to initialize empty volumes
RUN mkdir -p /defaults && \
    cp /app/config/config.example.json /defaults/config.json && \
    cp /app/config/settings.py /defaults/settings.py && \
    chmod 644 /defaults/config.json /defaults/settings.py

# Create volume mount points
# NOTE: Changed /app/database to /app/data to avoid overwriting Python package
VOLUME ["/app/config", "/app/data", "/app/logs", "/app/downloads", "/app/Transfer", "/app/MusicVideos", "/app/scripts"]

# Copy and set up entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Note: Don't switch to soulsync user yet - entrypoint needs root to change UIDs
# The entrypoint script will switch to soulsync after setting up permissions

# Expose port
EXPOSE 8008

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8008/ || exit 1

# Set environment variables
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV DATABASE_PATH=/app/data/music_library.db
ENV PUID=1000
ENV PGID=1000
ENV UMASK=022

# Set entrypoint and default command
ENTRYPOINT ["/entrypoint.sh"]
CMD ["gunicorn", "-c", "gunicorn.conf.py", "wsgi:application"]
