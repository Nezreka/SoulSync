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

## 🔧 Reverse Proxy Setup (Caddy, Nginx, Traefik)

If you're running SoulSync behind a reverse proxy with HTTPS, you can use the **main app port (8008)** for OAuth callbacks instead of the standalone port 8888. This is the recommended approach for reverse proxy setups.

### Step 1: Set your redirect URI to your proxy URL

**In SoulSync Settings:**
- Set Spotify redirect URI to: `https://yourdomain.com/callback`

**In your Spotify Developer Dashboard:**
- Add the same redirect URI: `https://yourdomain.com/callback`

### Step 2: Ensure your reverse proxy forwards to port 8008

Your reverse proxy should forward traffic to SoulSync's main port (8008). The `/callback` path is handled by the main Flask app — no need to expose port 8888.

**Example Caddy config:**
```
soulsync.yourdomain.com {
    reverse_proxy localhost:8008
}
```

**Example Nginx config:**
```nginx
server {
    listen 443 ssl;
    server_name soulsync.yourdomain.com;

    location / {
        proxy_pass http://localhost:8008;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### Step 3: Authenticate normally

Click "Connect Spotify" in SoulSync settings. After authorizing on Spotify, you'll be redirected back through your reverse proxy automatically.

### Important notes for reverse proxy users

- The redirect URI **must use HTTPS** for non-localhost domains (Spotify requirement)
- The redirect URI in SoulSync settings **must exactly match** the one in your Spotify Dashboard
- Port 8888 is only needed for direct/local access — you do **not** need to expose it through your proxy
- Make sure your proxy passes query parameters through unmodified (most do by default)

## 📝 Summary

The core issue is that **Spotify requires HTTPS for non-localhost** OAuth redirects.

**Choose your approach:**
- **Reverse proxy with HTTPS**: Set redirect URI to `https://yourdomain.com/callback` (recommended for production)
- **SSH tunnel**: Makes remote devices appear as localhost — set redirect URI to `http://127.0.0.1:8888/callback`
- **Local access**: No special config needed — default `http://127.0.0.1:8888/callback` works

**Key points:**
- ✅ Reverse proxy users: use `https://yourdomain.com/callback` on port 8008
- ✅ SSH tunnel users: use `http://127.0.0.1:8888/callback` on port 8888
- ✅ Redirect URI must match exactly in SoulSync settings AND Spotify Dashboard
- ✅ Query parameters must be preserved through the redirect chain