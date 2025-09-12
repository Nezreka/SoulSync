# 🏠 SoulSync Unraid Installation Guide

Complete guide to running SoulSync on Unraid with proper path mapping and configuration.

## 🎯 Why SoulSync on Unraid?

- **24/7 Operation**: Perfect for background music automation
- **Centralized Storage**: All your media in one place
- **Docker Integration**: Native Docker support with easy management
- **Media Server Ready**: Plex/Jellyfin likely already running

## 🚀 Installation Methods

### Method 1: Docker Run Command (Quick)

```bash
docker run -d \
  --name=soulsync \
  -p 8008:8008 \
  -p 8888:8888 \
  -p 8889:8889 \
  -v /mnt/user/appdata/soulsync/config:/app/config \
  -v /mnt/user/appdata/soulsync/logs:/app/logs \
  -v /mnt/user/Music:/host/music:rw \
  -v soulsync_database:/app/database \
  --restart unless-stopped \
  boulderbadgedad/soulsync:latest
```

### Method 2: Unraid Template (Recommended)

Create `/boot/config/plugins/dockerMan/templates-user/soulsync.xml`:

```xml
<?xml version="1.0"?>
<Container version="2">
  <Name>SoulSync</Name>
  <Repository>boulderbadgedad/soulsync:latest</Repository>
  <Registry>https://hub.docker.com/r/boulderbadgedad/soulsync</Registry>
  <Network>bridge</Network>
  <MyIP/>
  <Shell>bash</Shell>
  <Privileged>false</Privileged>
  <Support>https://github.com/Nezreka/SoulSync</Support>
  <Project>https://github.com/Nezreka/SoulSync</Project>
  <Overview>Automated music discovery and collection manager. Sync Spotify/Tidal/YouTube playlists to Plex/Jellyfin via Soulseek.</Overview>
  <Category>MediaApp:Music</Category>
  <WebUI>http://[IP]:[PORT:8008]</WebUI>
  <TemplateURL/>
  <Icon>https://raw.githubusercontent.com/Nezreka/SoulSync/main/assets/trans.png</Icon>
  <ExtraParams>--restart unless-stopped</ExtraParams>
  <PostArgs/>
  <CPUset/>
  <DateInstalled>1704067200</DateInstalled>
  <DonateText>Support Development</DonateText>
  <DonateLink>https://ko-fi.com/boulderbadgedad</DonateLink>
  <Requires>slskd container or standalone installation</Requires>
  <Config Name="WebUI Port" Target="8008" Default="8008" Mode="tcp" Description="Web interface port" Type="Port" Display="always" Required="true" Mask="false">8008</Config>
  <Config Name="Spotify OAuth Port" Target="8888" Default="8888" Mode="tcp" Description="Spotify OAuth callback port" Type="Port" Display="always" Required="true" Mask="false">8888</Config>
  <Config Name="Tidal OAuth Port" Target="8889" Default="8889" Mode="tcp" Description="Tidal OAuth callback port" Type="Port" Display="always" Required="true" Mask="false">8889</Config>
  <Config Name="Config" Target="/app/config" Default="/mnt/user/appdata/soulsync/config" Mode="rw" Description="Configuration files" Type="Path" Display="always" Required="true" Mask="false">/mnt/user/appdata/soulsync/config</Config>
  <Config Name="Logs" Target="/app/logs" Default="/mnt/user/appdata/soulsync/logs" Mode="rw" Description="Log files" Type="Path" Display="always" Required="false" Mask="false">/mnt/user/appdata/soulsync/logs</Config>
  <Config Name="Music Share" Target="/host/music" Default="/mnt/user/Music" Mode="rw" Description="Your music share for downloads and library" Type="Path" Display="always" Required="true" Mask="false">/mnt/user/Music</Config>
  <Config Name="Database Volume" Target="/app/database" Default="" Mode="" Description="Database volume (leave empty for named volume)" Type="Variable" Display="always" Required="false" Mask="false"/>
</Container>
```

## 📁 Unraid Path Structure

### Typical Unraid Paths
```
/mnt/user/Music/               # Your main music share
├── Downloads/                 # slskd download folder
├── Library/                   # Organized music library
└── Transfer/                  # Processing folder

/mnt/user/appdata/soulsync/    # App configuration
├── config/                    # SoulSync settings
└── logs/                      # Application logs
```

## ⚙️ Configuration for Unraid

### 1. Service URLs
In SoulSync settings, use these URLs:

- **slskd**: `http://192.168.1.100:5030` (replace with your Unraid IP)
- **Plex**: `http://192.168.1.100:32400`
- **Jellyfin**: `http://192.168.1.100:8096`

### 2. Download/Transfer Paths
Set these paths in SoulSync settings:

- **Download Path**: `/host/music/Downloads`
- **Transfer Path**: `/host/music/Library`

### 3. slskd Integration
If running slskd on Unraid:
```bash
# slskd container should mount the same music share
docker run -d \
  --name=slskd \
  -p 5030:5030 \
  -p 50300:50300 \
  -v /mnt/user/appdata/slskd:/app \
  -v /mnt/user/Music/Downloads:/downloads \
  -v /mnt/user/Music/Shares:/shares:ro \
  slskd/slskd:latest
```

## 🚦 Setup Steps

### 1. Install Prerequisites
- Install slskd container from Community Applications
- Ensure Plex/Jellyfin is running (if desired)
- Create Spotify API app at https://developer.spotify.com

### 2. Install SoulSync
```bash
# Option 1: Community Applications
Search for "SoulSync" in CA and install

# Option 2: Manual Docker Run
Use the docker run command above

# Option 3: Unraid Docker UI
Add container manually with repository: boulderbadgedad/soulsync:latest
```

### 3. Configure Paths
Map these volumes in Unraid Docker settings:
- Container: `/app/config` → Host: `/mnt/user/appdata/soulsync/config`
- Container: `/app/logs` → Host: `/mnt/user/appdata/soulsync/logs`  
- Container: `/host/music` → Host: `/mnt/user/Music` (or your music share)

### 4. Configure Ports
- `8008` - Main web interface
- `8888` - Spotify OAuth callback
- `8889` - Tidal OAuth callback

## 🎵 First-Time Setup

1. **Access SoulSync**: Navigate to `http://your-unraid-ip:8008`
2. **Go to Settings**: Configure your API credentials
3. **Set Paths**: Use `/host/music/Downloads` and `/host/music/Library`
4. **Test Connections**: Verify all services connect properly

## 🔧 Unraid-Specific Benefits

### File Management
- **User Shares**: Automatic organization across multiple drives
- **Cache Drive**: Fast processing for downloads
- **Parity Protection**: Your music library is protected

### Networking
- **Bridge Mode**: Simple port mapping
- **Custom Networks**: Isolate containers if desired
- **VPN Support**: Route through VPN containers if needed

### Monitoring
- **Unraid Dashboard**: Monitor container status
- **CA Auto Update**: Keep SoulSync updated automatically
- **Resource Monitoring**: Track CPU/RAM usage

## 📊 Recommended Share Setup

### Music Share Configuration
```
Share Name: Music
Allocation Method: High Water
Minimum Free Space: 10GB
Split Level: 2
Include: disk1,disk2,cache
Exclude: (none)
Use Cache: Yes (cache:yes)
```

This ensures:
- Fast downloads to cache drive
- Automatic migration to array
- Proper organization across multiple drives

## 🛠️ Troubleshooting

### ❌ ModuleNotFoundError: No module named 'config.settings'

**Problem**: Most common error - incorrect config volume mapping

**Wrong**:
```yaml
- "/mnt/cache/appdata/soulsync:/app/config"  # ❌ This overwrites the app's config module
```

**Correct**:
```yaml
- "/mnt/cache/appdata/soulsync/config:/app/config"  # ✅ This maps to the config folder only
```

**Why this happens**: Mounting the entire appdata folder to `/app/config` overwrites SoulSync's Python `config/` module, causing import errors.

### Container Won't Start
```bash
# Check Unraid logs
docker logs soulsync

# Verify paths exist
ls -la /mnt/user/appdata/soulsync/
ls -la /mnt/user/Music/
```

### Services Not Connecting
- Use Unraid server IP, not `localhost` or `127.0.0.1`
- Check firewall settings in Unraid network settings
- Verify other containers are running and accessible

### Permission Issues
```bash
# Fix ownership on appdata
chown -R nobody:users /mnt/user/appdata/soulsync/

# Fix music share permissions
chmod -R 775 /mnt/user/Music/
```

## 🚀 Advanced Configuration

### Custom Network
```bash
# Create custom network
docker network create soulsync-network

# Run with custom network
docker run --network soulsync-network ...
```

### Resource Limits
In Unraid Docker settings:
- **CPU Pinning**: Pin to specific cores
- **Memory Limit**: Set RAM limit (2GB recommended)
- **CPU Shares**: Set priority vs other containers

### Auto-Update
Install "CA Auto Update Applications" plugin:
- Automatically updates SoulSync container
- Sends notifications on updates
- Maintains configuration

## 📱 Accessing SoulSync

- **Local**: `http://tower.local:8008` (if using .local domains)
- **IP Address**: `http://192.168.1.100:8008`
- **Reverse Proxy**: Configure nginx/traefik for external access

## 🎯 Perfect Unraid Setup

```
Container Stack:
├── SoulSync (Music automation)
├── slskd (Soulseek client)  
├── Plex/Jellyfin (Media server)
├── *arr Apps (Optional: Lidarr integration)
└── Reverse Proxy (Optional: External access)
```

This creates a complete, automated music ecosystem on Unraid!

## 📝 Support

- SoulSync logs: `/mnt/user/appdata/soulsync/logs/`
- Unraid diagnostics: Tools → Diagnostics
- Container logs: Docker tab → SoulSync → Logs

Your music automation server is ready! 🎵