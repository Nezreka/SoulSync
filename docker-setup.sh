#!/bin/bash

# SoulSync Docker Setup Script
# This script helps set up the Docker environment for SoulSync WebUI

set -e

echo "🎵 SoulSync WebUI Docker Setup"
echo "==============================="

# Create necessary directories
echo "📁 Creating directory structure..."
mkdir -p config database logs downloads Transfer

# Create .gitkeep files for empty directories
touch downloads/.gitkeep Transfer/.gitkeep logs/.gitkeep

# Copy example config if config.json doesn't exist
if [ ! -f "config/config.json" ]; then
    if [ -f "config/config.example.json" ]; then
        echo "📋 Copying example configuration..."
        cp config/config.example.json config/config.json
        echo "⚙️  Please edit config/config.json with your API keys and settings"
    else
        echo "⚠️  Warning: No example config found. You'll need to create config/config.json manually"
    fi
fi

# Set proper permissions
echo "🔐 Setting permissions..."
chmod -R 755 config database logs downloads Transfer
chown -R $USER:$USER config database logs downloads Transfer

# Check if Docker is installed
if ! command -v docker &> /dev/null; then
    echo "❌ Docker is not installed. Please install Docker first."
    echo "   Visit: https://docs.docker.com/get-docker/"
    exit 1
fi

# Check if Docker Compose is installed
if ! command -v docker-compose &> /dev/null; then
    echo "❌ Docker Compose is not installed. Please install Docker Compose first."
    echo "   Visit: https://docs.docker.com/compose/install/"
    exit 1
fi

echo "✅ Setup complete!"
echo ""
echo "📝 Next steps:"
echo "1. Edit config/config.json with your API keys and server settings"
echo "2. Run: docker-compose up -d"
echo "3. Access SoulSync at http://localhost:8888"
echo ""
echo "🔧 Useful commands:"
echo "  docker-compose up -d          # Start in background"
echo "  docker-compose logs -f        # View logs"
echo "  docker-compose down           # Stop container"
echo "  docker-compose pull           # Update image"
echo "  docker-compose restart        # Restart container"
echo ""
echo "📂 Data locations:"
echo "  - Configuration: ./config/"
echo "  - Database: ./database/"
echo "  - Logs: ./logs/"
echo "  - Downloads: ./downloads/"
echo "  - Transfer: ./Transfer/"