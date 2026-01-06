# SoulSync WebUI - Docker Deployment Guide

## 🐳 Quick Start

### Prerequisites
- Docker Engine 20.10+
- Docker Compose 1.29+
- At least 2GB RAM and 10GB free disk space

### 1. Setup
```bash
# Clone or download the repository
git clone <your-repo-url>
cd newmusic

# Run setup script
chmod +x docker-setup.sh
./docker-setup.sh
```

### 2. Configure
Edit `config/config.json` with your API keys and server settings:
```json
{
  "spotify": {
    "client_id": "your_spotify_client_id",
    "client_secret": "your_spotify_client_secret"
  },
  "plex": {
    "url": "http://your-plex-server:32400",
    "token": "your_plex_token"
  }
}
```

### 3. Deploy
```bash
# Start SoulSync
docker-compose up -d

# View logs
docker-compose logs -f

# Access the web interface
open http://localhost:8008
```

## 📁 Volume Mounts

SoulSync requires persistent storage for:

- **`./config`** → `/app/config` - Configuration files
- **`./data`** → `/app/data` - SQLite database files  
- **`./logs`** → `/app/logs` - Application logs
- **`./downloads`** → `/app/downloads` - Downloaded music files
- **`./Transfer`** → `/app/Transfer` - Processed/matched music files

## 🔧 Configuration Options

### Environment Variables
```yaml
environment:
  - FLASK_ENV=production              # Flask environment
  - PYTHONPATH=/app                   # Python path
  - SOULSYNC_CONFIG_PATH=/app/config/config.json  # Config file location
  - TZ=America/New_York               # Timezone
```

### Port Configuration
Default port is `8008`. To change:
```yaml
ports:
  - "9999:8008"  # Access on port 9999
```

### Resource Limits
Adjust based on your system:
```yaml
deploy:
  resources:
    limits:
      cpus: '4.0'      # Max CPU cores
      memory: 4G       # Max RAM
    reservations:
      cpus: '1.0'      # Minimum CPU
      memory: 1G       # Minimum RAM
```

## 🚀 Advanced Setup

### Multi-Architecture Support
The Docker image supports both AMD64 and ARM64:
```bash
# Build for specific architecture
docker buildx build --platform linux/amd64,linux/arm64 -t soulsync-webui .
```

### Custom Network
For integration with other containers:
```yaml
networks:
  media:
    external: true
```

### External Services
Connect to external Plex/Jellyfin servers:
```yaml
extra_hosts:
  - "plex.local:192.168.1.100"
  - "jellyfin.local:192.168.1.101"
```

## 🔍 Troubleshooting

### Check Container Status
```bash
docker-compose ps
docker-compose logs soulsync
```

### Common Issues

**Permission Denied**
```bash
sudo chown -R 1000:1000 config database logs downloads Transfer
```

**Port Already in Use**
```bash
# Check what's using port 8888
sudo lsof -i :8888
# Change port in docker-compose.yml
```

**Out of Memory**
```bash
# Increase memory limits in docker-compose.yml
# Or free up system memory
```

### Health Check
The container includes health checks:
```bash
docker inspect --format='{{.State.Health.Status}}' soulsync-webui
```

## 📊 Monitoring

### View Real-time Logs
```bash
docker-compose logs -f --tail=100
```

### Container Stats  
```bash
docker stats soulsync-webui
```

### Database Size
```bash
du -sh database/
```

## 🔄 Updates

### Pull Latest Image
```bash
docker-compose pull
docker-compose up -d
```

### Backup Before Update
```bash
# Backup data
tar -czf soulsync-backup-$(date +%Y%m%d).tar.gz config/ database/ logs/

# Update
docker-compose pull && docker-compose up -d
```

## 🛠️ Development

### Build Local Image
```bash
docker build -t soulsync-webui .
```

### Development Mode
```yaml
# In docker-compose.yml
environment:
  - FLASK_ENV=development
volumes:
  - .:/app  # Mount source code for live reload
```

## 🔐 Security

### Non-Root User
The container runs as user `soulsync` (UID 1000) for security.

### Network Security
```yaml
# Restrict to localhost only
ports:
  - "127.0.0.1:8888:8888"
```

### Firewall
```bash
# Allow only local access
sudo ufw allow from 192.168.1.0/24 to any port 8888
```

## 📋 Complete Example

Here's a complete `docker-compose.yml` for production:

```yaml
version: '3.8'

services:
  soulsync:
    build: .
    container_name: soulsync-webui
    restart: unless-stopped
    ports:
      - "8888:8888"
    volumes:
      - ./config:/app/config
      - ./data:/app/data  
      - ./logs:/app/logs
      - ./downloads:/app/downloads
      - ./Transfer:/app/Transfer
      - /mnt/music:/music:ro  # Your music library
    environment:
      - FLASK_ENV=production
      - TZ=America/New_York
      - PYTHONPATH=/app
    deploy:
      resources:
        limits:
          cpus: '2.0'
          memory: 2G
        reservations:
          cpus: '0.5'
          memory: 512M
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8888/"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 60s
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
```

## 🎯 Production Checklist

- [ ] Configure proper API keys in `config/config.json`
- [ ] Set appropriate resource limits
- [ ] Configure proper volume mounts
- [ ] Set up log rotation
- [ ] Configure firewall rules
- [ ] Set up backup strategy
- [ ] Test health checks
- [ ] Verify external service connectivity