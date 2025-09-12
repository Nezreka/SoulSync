# 🐳 SoulSync Docker Installation Guide

Complete guide to running SoulSync in Docker with persistent data and proper configuration.

## 🚀 Quick Start

```bash
# Clone the repository
git clone https://github.com/Nezreka/SoulSync
cd SoulSync

# Start the container
docker-compose up -d

# Access SoulSync at http://localhost:8008
```

## 📋 Prerequisites

Before running SoulSync in Docker, ensure you have:

- **Docker & Docker Compose** installed on your system
- **slskd** running on your host machine (port 5030)
- **Spotify API credentials** (Client ID/Secret)
- **Media server** (Plex/Jellyfin) - optional but recommended

## 🔧 Configuration Setup

### 1. Initial Configuration

On first run, SoulSync will create a default config. You need to update it with your credentials:

1. Start the container: `docker-compose up -d`
2. Open http://localhost:8008 in your browser
3. Go to **Settings** page
4. Fill in your API credentials:
   - **Spotify**: Client ID and Client Secret
   - **Soulseek**: slskd URL (`http://host.docker.internal:5030`) and API key
   - **Media Server**: Plex/Jellyfin connection details
   - **Paths**: Download and transfer folder paths

### 2. Path Configuration

**Important**: Your download and transfer paths must be accessible to the Docker container.

#### Default Setup (E: Drive)
The docker-compose.yml mounts the E: drive by default:
```yaml
volumes:
  - /mnt/e:/host/mnt/e:rw
```

If your paths are on E: drive, you can use paths like:
- Download Path: `E:/Music/Downloads`  
- Transfer Path: `E:/Music/Library`

#### Other Drives
If your music folders are on different drives, update docker-compose.yml:

```yaml
volumes:
  # Add drive mounts as needed
  - /mnt/c:/host/mnt/c:rw  # For C: drive
  - /mnt/d:/host/mnt/d:rw  # For D: drive
  - /mnt/e:/host/mnt/e:rw  # For E: drive
```

Then restart: `docker-compose down && docker-compose up -d`

### 3. Service URLs for Docker

When configuring services in Docker mode, use these URLs:

- **slskd**: `http://host.docker.internal:5030`
- **Plex**: `http://host.docker.internal:32400` 
- **Jellyfin**: `http://host.docker.internal:8096`

Docker automatically resolves `host.docker.internal` to your host machine.

## 📊 Data Persistence

SoulSync Docker uses a named volume for the database:

- **Database**: Stored in `soulsync_database` volume (persists across container rebuilds)
- **Config**: Stored in `./config/` folder (mapped to host)
- **Logs**: Stored in `./logs/` folder (mapped to host)

**Note**: The Docker database is separate from GUI/WebUI versions.

## 🛠️ Common Commands

```bash
# Start SoulSync
docker-compose up -d

# View logs
docker-compose logs -f

# Stop SoulSync
docker-compose down

# Restart SoulSync
docker-compose restart

# Update to latest image
docker-compose pull && docker-compose up -d

# Rebuild from source
docker-compose down && docker-compose up -d --build
```

## 🔍 Troubleshooting

### Container Won't Start
```bash
# Check container status
docker-compose ps

# View error logs
docker-compose logs
```

### Can't Access Services
- Ensure slskd is running on host machine
- Use `host.docker.internal` URLs in settings
- Check firewall isn't blocking ports 8008, 8888, 8889

### Files Not Processing
- Verify download/transfer paths are mounted correctly
- Check paths use correct drive letters (E:/, C:/, etc.)
- Ensure directories exist and have proper permissions

### Database Issues
```bash
# Reset database (WARNING: deletes all data)
docker volume rm soulsync_database
docker-compose up -d
```

## 📁 Directory Structure

```
SoulSync/
├── config/           # Configuration files (mapped to container)
├── logs/             # Application logs (mapped to container)
├── docker-compose.yml
└── Dockerfile
```

## 🔐 Security Notes

- Config files contain API keys - keep them secure
- The database volume persists your library data
- Only required ports (8008, 8888, 8889) are exposed

## 🆕 Updates

To update SoulSync:

```bash
# Pull latest image
docker-compose pull

# Restart with new image
docker-compose up -d
```

Your configuration and database will be preserved.

## ❓ Need Help?

- Check logs: `docker-compose logs -f`
- Verify service connections in Settings page
- Ensure all prerequisites are running
- Database and config persist between restarts

## 🎵 Ready to Go!

Once configured, SoulSync will:
- Automatically sync your playlists
- Download missing tracks with FLAC priority
- Organize files with proper metadata
- Retry failed downloads every hour
- Monitor watchlist artists for new releases

Access your SoulSync instance at **http://localhost:8008**