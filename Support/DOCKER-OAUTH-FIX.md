# 🔐 Docker OAuth Authentication Fix

## Problem: "Insecure redirect URI" Error

When accessing SoulSync from a **different device** than the Docker host, you may encounter:
- `INVALID_CLIENT: Insecure redirect URI`
- `Spotify authentication failed: error: invalid_client`

**Why this happens:** Spotify requires HTTPS for OAuth callbacks when not using localhost.

## ✅ Simple Solution: SSH Port Forwarding

### Step 1: Set up SSH tunnel from your device to Docker host

**On the device you're browsing from** (laptop/phone/etc):

```bash
# Replace 'user' and 'docker-host-ip' with your actual values
ssh -L 8888:localhost:8888 -L 8889:localhost:8889 user@docker-host-ip

# Example:
ssh -L 8888:localhost:8888 -L 8889:localhost:8889 john@192.168.1.100
```

**Keep this SSH connection open** while using SoulSync.

### Step 2: Configure OAuth redirect URIs

**In your Spotify Developer App:**
- Set redirect URI to: `http://127.0.0.1:8888/callback`

**In your Tidal Developer App:**
- Set redirect URI to: `http://127.0.0.1:8889/tidal/callback`

**In SoulSync Settings:**
- Set Spotify redirect URI to: `http://127.0.0.1:8888/callback`
- Set Tidal redirect URI to: `http://127.0.0.1:8889/tidal/callback`

### Step 3: Use SoulSync normally

- Access SoulSync: `http://docker-host-ip:8008` (normal HTTP)
- OAuth callbacks will tunnel through SSH to localhost
- Authentication will work without HTTPS requirements

## 🖥️ Alternative: Direct Access from Docker Host

If you can access SoulSync directly from the Docker host machine:
- Use: `http://127.0.0.1:8008`
- Set OAuth redirect URIs to localhost (as above)
- No SSH tunnel needed

## 🔧 For Advanced Users: Reverse Proxy

Set up nginx/traefik with proper SSL certificates for true HTTPS support. See community guides for Docker reverse proxy setups.

## 📝 Summary

The core issue is that **Spotify requires HTTPS for non-localhost** OAuth redirects. The SSH tunnel makes remote devices appear as localhost to bypass this requirement.

**Key points:**
- ✅ Always use `127.0.0.1` in OAuth redirect URIs  
- ✅ Use SSH tunnel when accessing from different device
- ✅ Keep tunnel open during authentication
- ✅ Works with existing Docker setup - no changes needed