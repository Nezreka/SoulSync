#!/bin/bash
# SoulSync Docker Entrypoint Script
# Handles PUID/PGID/UMASK configuration for proper file permissions

set -e

# Default values
PUID=${PUID:-1000}
PGID=${PGID:-1000}
UMASK=${UMASK:-022}

echo "🐳 SoulSync Container Starting..."
echo "📝 User Configuration:"
echo "   PUID: $PUID"
echo "   PGID: $PGID"
echo "   UMASK: $UMASK"

# Get current soulsync user/group IDs
CURRENT_UID=$(id -u soulsync)
CURRENT_GID=$(id -g soulsync)

# Only modify user/group if they differ from requested values
if [ "$CURRENT_UID" != "$PUID" ] || [ "$CURRENT_GID" != "$PGID" ]; then
    echo "🔧 Adjusting user permissions..."

    # Modify group ID if needed
    if [ "$CURRENT_GID" != "$PGID" ]; then
        echo "   Changing group ID from $CURRENT_GID to $PGID"
        groupmod -o -g "$PGID" soulsync
    fi

    # Modify user ID if needed
    if [ "$CURRENT_UID" != "$PUID" ]; then
        echo "   Changing user ID from $CURRENT_UID to $PUID"
        usermod -o -u "$PUID" soulsync
    fi

    # Fix ownership of app directories
    echo "🔒 Fixing permissions on app directories..."
    chown -R soulsync:soulsync /app/config /app/data /app/logs /app/downloads /app/Transfer /app/Staging 2>/dev/null || true
else
    echo "✅ User/Group IDs already correct"
fi

# Set umask for file creation permissions
echo "🎭 Setting UMASK to $UMASK"
umask "$UMASK"

# Initialize config files if they don't exist (first-time setup)
echo "🔍 Checking for configuration files..."

if [ ! -f "/app/config/config.json" ]; then
    echo "   📄 Creating default config.json..."
    cp /defaults/config.json /app/config/config.json
    chown soulsync:soulsync /app/config/config.json 2>/dev/null || true
else
    echo "   ✅ config.json already exists"
fi

if [ ! -f "/app/config/settings.py" ]; then
    echo "   📄 Creating default settings.py..."
    cp /defaults/settings.py /app/config/settings.py
    chown soulsync:soulsync /app/config/settings.py 2>/dev/null || true
else
    echo "   ✅ settings.py already exists"
fi

# Ensure all directories exist and have proper permissions
mkdir -p /app/config /app/data /app/logs /app/downloads /app/Transfer /app/Staging
chown -R soulsync:soulsync /app/config /app/data /app/logs /app/downloads /app/Transfer /app/Staging 2>/dev/null || true

echo "✅ Configuration initialized successfully"

# Display final user info
echo "👤 Running as:"
echo "   User: $(id -u soulsync):$(id -g soulsync) ($(id -un soulsync):$(id -gn soulsync))"
echo "   UMASK: $(umask)"
echo ""
echo "🚀 Starting SoulSync Web Server..."

# Execute the main command as the soulsync user
# If already running as the correct user (e.g. Podman rootless with keep-id), skip gosu
if [ "$(id -u)" = "$PUID" ]; then
    exec "$@"
else
    exec gosu soulsync "$@"
fi
