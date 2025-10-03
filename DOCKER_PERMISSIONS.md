# Docker Permissions Guide

## Understanding PUID/PGID/UMASK

SoulSync supports dynamic user/group ID configuration to ensure files are created with the correct ownership, especially important when sharing files with other containers like Lidarr, Sonarr, or Plex.

### What are PUID and PGID?

- **PUID** (Process User ID): The numeric user ID the container runs as
- **PGID** (Process Group ID): The numeric group ID the container runs as
- **UMASK**: Controls default permissions for newly created files/directories

## Quick Start

### Finding Your IDs

On your host system, run:
```bash
id your_username
```

Example output:
```
uid=1000(myuser) gid=1000(myuser) groups=1000(myuser),999(docker)
```

Your PUID is `1000` and PGID is `1000`.

### Matching Lidarr/Sonarr/Plex

If you're using SoulSync with Lidarr, check Lidarr's docker-compose to see what PUID/PGID it uses:

```yaml
# Your Lidarr container
services:
  lidarr:
    environment:
      - PUID=99
      - PGID=100
```

Then set SoulSync to match:

```yaml
# Your SoulSync container
services:
  soulsync:
    environment:
      - PUID=99      # Match Lidarr
      - PGID=100     # Match Lidarr
      - UMASK=002    # Allows group write permissions
```

## Common Scenarios

### Scenario 1: Sharing with Lidarr (Unraid)

**Problem**: Lidarr can't import files downloaded by SoulSync

**Solution**: Match the PUID/PGID
```yaml
services:
  soulsync:
    environment:
      - PUID=99      # Common Unraid user
      - PGID=100     # Common Unraid group
      - UMASK=002
```

### Scenario 2: Single User System

**Problem**: Default 1000:1000 works but want to match your user

**Solution**: Use your user's IDs (run `id` command)
```yaml
services:
  soulsync:
    environment:
      - PUID=1000    # Your user ID
      - PGID=1000    # Your group ID
      - UMASK=022
```

### Scenario 3: Multiple Containers Sharing Files

**Problem**: Multiple containers (Plex, Lidarr, SoulSync) need access to same files

**Solution**: All containers should use same PUID/PGID
```yaml
services:
  plex:
    environment:
      - PUID=1000
      - PGID=1000

  lidarr:
    environment:
      - PUID=1000
      - PGID=1000

  soulsync:
    environment:
      - PUID=1000
      - PGID=1000
      - UMASK=002    # Allows all containers to write
```

## UMASK Values Explained

UMASK controls default permissions:

- **022**: Files: `644` (rw-r--r--), Directories: `755` (rwxr-xr-x)
  - Owner: read/write
  - Group: read only
  - Others: read only
  - **Use when**: Only you need to modify files

- **002**: Files: `664` (rw-rw-r--), Directories: `775` (rwxrwxr-x)
  - Owner: read/write
  - Group: read/write
  - Others: read only
  - **Use when**: Multiple containers share the same group and need write access

- **000**: Files: `666` (rw-rw-rw-), Directories: `777` (rwxrwxrwx)
  - Everyone: full access
  - **Use when**: Not recommended (security risk)

## Troubleshooting

### Permission Denied Errors

**Symptom**:
```
Permission denied: Access to the path '/music/Artist/Album/track.flac' is denied.
```

**Diagnosis**:
1. Check file ownership:
   ```bash
   ls -la /path/to/music/Artist/Album/
   ```

2. Check what user Lidarr runs as:
   ```bash
   docker exec lidarr id
   ```

3. Check what user SoulSync runs as:
   ```bash
   docker exec soulsync-webui id
   ```

**Fix**: Ensure both containers use the same PUID/PGID

### Files Created with Wrong Owner

**Symptom**: Files show as `1000:1000` even though you set `PUID=99 PGID=100`

**Cause**: Container needs to be rebuilt after changing PUID/PGID

**Fix**:
```bash
docker-compose down
docker-compose up -d
```

### Existing Files Have Wrong Permissions

**Symptom**: Old files created before changing PUID/PGID have wrong ownership

**Fix**: Manually fix ownership:
```bash
# Find your download directory
docker exec soulsync-webui ls -la /app/downloads

# Fix ownership (run on host, not in container)
sudo chown -R 99:100 /path/to/downloads
```

## Advanced: Custom Entrypoint

The container uses `/entrypoint.sh` to handle PUID/PGID. When the container starts:

1. Reads `PUID`, `PGID`, `UMASK` environment variables
2. Changes the internal `soulsync` user to match those IDs
3. Fixes permissions on app directories
4. Starts Python app as that user

You can verify this by checking container logs:
```bash
docker logs soulsync-webui
```

Look for:
```
🐳 SoulSync Container Starting...
📝 User Configuration:
   PUID: 99
   PGID: 100
   UMASK: 002
🔧 Adjusting user permissions...
   Changing group ID from 1000 to 100
   Changing user ID from 1000 to 99
🚀 Starting SoulSync Web Server...
```

## Example docker-compose.yml

```yaml
version: '3.8'

services:
  soulsync:
    image: boulderbadgedad/soulsync:latest
    container_name: soulsync-webui
    environment:
      # Match these to your Lidarr/Plex/other containers
      - PUID=99
      - PGID=100
      - UMASK=002
      - TZ=America/New_York
    ports:
      - "8008:8008"
    volumes:
      - ./config:/app/config
      - ./logs:/app/logs
      - ./downloads:/app/downloads
      - /mnt/user/Music:/music:rw    # Shared music library
    restart: unless-stopped
```

## Need Help?

If you're still experiencing permission issues:
1. Check container logs: `docker logs soulsync-webui`
2. Verify PUID/PGID: `docker exec soulsync-webui id`
3. Check file permissions: `docker exec soulsync-webui ls -la /app/downloads`
4. Open an issue on GitHub with the output of these commands
