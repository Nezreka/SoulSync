# SoulSync WebUI Dockerfile
# Multi-architecture support for AMD64 and ARM64

FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    libc6-dev \
    libffi-dev \
    libssl-dev \
    curl \
    gosu \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user for security
RUN useradd --create-home --shell /bin/bash --uid 1000 soulsync

# Copy requirements and install Python dependencies
COPY requirements-webui.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements-webui.txt

# Copy application code
COPY . .

# Create necessary directories with proper permissions
RUN mkdir -p /app/config /app/database /app/logs /app/downloads /app/Transfer && \
    chown -R soulsync:soulsync /app

# Copy example config as default config.json and set proper ownership
RUN cp /app/config/config.example.json /app/config/config.json && \
    chown soulsync:soulsync /app/config/config.json

# Create volume mount points
VOLUME ["/app/config", "/app/database", "/app/logs", "/app/downloads", "/app/Transfer"]

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
ENV FLASK_APP=web_server.py
ENV FLASK_ENV=production
ENV PUID=1000
ENV PGID=1000
ENV UMASK=022

# Set entrypoint and default command
ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "web_server.py"]