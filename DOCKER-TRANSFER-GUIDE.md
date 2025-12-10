# Docker Transfer Folder Setup Guide

## Problem: Files Download but Don't Transfer to Music Library

This happens when the transfer path isn't properly configured in Docker.

## Solution

### 1. Docker Compose Volume Setup

Your `docker-compose.yml` should look like this:

```yaml
version: '3.8'

services:
  soulsync:
    image: boulderbadgedad/soulsync:latest
    container_name: soulsync-webui
    ports:
      - "8008:8008"
      - "8888:8888"
      - "8889:8889"
    volumes:
      # Config and logs
      - /home/myname/apps/soulsync/config:/app/config
      - /home/myname/apps/soulsync/logs:/app/logs

      # Downloads from slskd
      - /mnt/data/slskd/downloads:/app/downloads

      # Your Plex music library (transfer destination)
      - /mnt/data/media/music:/app/Transfer:rw

      # Database - USE NAMED VOLUME (critical!)
      - soulsync_database:/app/database

    extra_hosts:
      - "host.docker.internal:host-gateway"

    restart: unless-stopped

volumes:
  soulsync_database:
    driver: local
```

**IMPORTANT**:
- Do NOT mount `/app/database` to a host path
- Use a named volume `soulsync_database:/app/database`
- Host path mounts cause database corruption

### 2. SoulSync Settings Configuration

In SoulSync Web UI → Settings:

1. **Download Path**: `/app/downloads`
2. **Transfer Path**: `/app/Transfer`

**DO NOT use host paths** like `/mnt/data/...` in settings. Use the **container paths** (`/app/...`).

### 3. Enable Debug Logging

**New Feature**: You can now change log level without restart!

1. Go to Settings page
2. Scroll to **"Log Level"** dropdown
3. Change from **INFO** to **DEBUG**
4. Click Save Settings
5. Check logs at `logs/app.log` for detailed transfer information

### 4. HTTP 429 (Too Many Requests) Fix

This happens when too many concurrent downloads hit slskd. SoulSync has rate limiting, but slskd may need adjustment.

**Option 1: Increase slskd limits** (recommended)
Edit your slskd config (`appsettings.yml`):
```yaml
limits:
  download:
    maximum_concurrent_downloads: 10  # Increase from default
```

**Option 2: Reduce SoulSync workers**
In SoulSync settings → `config.json`:
```json
{
  "database": {
    "path": "database/music_library.db",
    "max_workers": 3  // Reduce from 5 to 3
  }
}
```

### 5. Understanding the Workflow

```
1. slskd downloads → /mnt/data/slskd/downloads (host)
                  → /app/downloads (container)

2. SoulSync processes files

3. SoulSync moves to → /app/Transfer (container)
                    → /mnt/data/media/music (host)
                    → Your Plex library sees the files!
```

### 6. Troubleshooting

**Files stay in downloads folder:**
- Check transfer path is `/app/Transfer` (not host path)
- Enable DEBUG logging and check for errors
- Verify permissions: `chmod -R 755 /mnt/data/media/music`

**Database errors when mounting volume:**
```yaml
# ❌ WRONG - causes corruption
volumes:
  - /home/myname/apps/soulsync/database:/app/database

# ✅ CORRECT - use named volume
volumes:
  - soulsync_database:/app/database
```

**Tracks marked as failed but downloaded:**
- This is the HTTP 429 issue
- Files still download, but API reports failure
- Check wishlist - these tracks can be removed
- Fix by adjusting rate limits (see #4 above)

**Excessive M3U saves:**
- This is expected behavior for playlist sync
- Not an error, just verbose logging
- Change to INFO level if it bothers you

### 7. Verify Setup

Run these commands to verify:

```bash
# Check container can write to music library
docker exec soulsync-webui touch /app/Transfer/test.txt
ls -la /mnt/data/media/music/test.txt  # Should exist

# Check downloads folder
docker exec soulsync-webui ls -la /app/downloads

# View logs in real-time
docker logs -f soulsync-webui

# Check database volume exists
docker volume ls | grep soulsync
```

### 8. Complete Example

**docker-compose.yml**:
```yaml
version: '3.8'

services:
  soulsync:
    image: boulderbadgedad/soulsync:latest
    container_name: soulsync-webui
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
    ports:
      - "8008:8008"
      - "8888:8888"
      - "8889:8889"
    volumes:
      - ./config:/app/config
      - ./logs:/app/logs
      - /mnt/data/slskd/downloads:/app/downloads
      - /mnt/data/media/music:/app/Transfer:rw
      - soulsync_database:/app/database
    extra_hosts:
      - "host.docker.internal:host-gateway"
    restart: unless-stopped

volumes:
  soulsync_database:
    driver: local
```

**SoulSync Settings**:
- slskd URL: `http://host.docker.internal:5030`
- Download Path: `/app/downloads`
- Transfer Path: `/app/Transfer`
- Log Level: `DEBUG` (for troubleshooting)

---

## Still Having Issues?

1. Enable DEBUG logging
2. Download a single track
3. Check `logs/app.log` for the complete flow
4. Post relevant log excerpts in the GitHub issue

The logs will show exactly where the transfer is failing.
