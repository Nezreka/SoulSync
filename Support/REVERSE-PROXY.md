# Running SoulSync behind a reverse proxy (nginx / Caddy / Traefik)

Putting SoulSync behind a reverse proxy lets you serve it over **HTTPS** and — the
important part — put **authentication** in front of it before exposing it to the
internet. This guide covers the safe setup.

> **The golden rule:** the safest way to expose *any* self-hosted app publicly is
> to require authentication at the proxy (an auth layer), **not** to rely on the
> app's own protection. SoulSync's launch PIN is a useful fallback, but it is not
> a substitute for a real auth layer on a public instance.

---

## 1. Turn on reverse-proxy mode

By default SoulSync does **not** trust proxy headers (so a direct client can't spoof
its IP or pretend the connection is HTTPS). If you're behind a proxy that
terminates TLS, turn on **Settings → Security → "Behind a reverse proxy"** and
**restart SoulSync** (this option applies at startup).

When enabled, SoulSync:
- trusts `X-Forwarded-For/Proto/Host/Port` from **one** proxy hop (correct client
  IP, HTTPS detection, redirects),
- marks its session cookie `Secure` (HTTPS-only) + `SameSite=Lax`, and
- sends conservative security headers (`X-Content-Type-Options: nosniff`,
  `X-Frame-Options: SAMEORIGIN`, `Strict-Transport-Security`). No CSP is set — tune
  one at your proxy if you want it.

**Leave it off if you access SoulSync directly over http:// on your LAN** — turning
it on would make the session cookie HTTPS-only and break plain-HTTP access. With it
off, none of the above applies and SoulSync behaves exactly as before.

> The launch PIN is also brute-force limited (10 wrong attempts from an IP → a
> short cooldown), regardless of this setting — a correct PIN is never affected.

Restart SoulSync after changing it.

---

## 2. nginx

SoulSync uses WebSockets (Socket.IO), so the `Upgrade`/`Connection` headers are
**required** — without them live updates silently stop working.

```nginx
server {
    listen 443 ssl;
    server_name soulsync.example.com;

    ssl_certificate     /etc/letsencrypt/live/soulsync.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/soulsync.example.com/privkey.pem;

    # Large library scans / uploads
    client_max_body_size 0;

    location / {
        proxy_pass http://127.0.0.1:8008;

        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host  $host;

        # Required for Socket.IO / live updates
        proxy_http_version 1.1;
        proxy_set_header Upgrade    $http_upgrade;
        proxy_set_header Connection "upgrade";

        proxy_read_timeout 3600s;   # long-running scans
        proxy_send_timeout 3600s;
    }
}
```

---

## 3. Caddy

Caddy handles TLS automatically and proxies WebSockets out of the box:

```caddy
soulsync.example.com {
    reverse_proxy 127.0.0.1:8008
}
```

Caddy sets `X-Forwarded-*` for you. (Add an auth provider directive if you want
auth at the proxy — see below.)

---

## 4. Traefik

Traefik proxies WebSockets automatically and forwards the headers. Point a router
at the SoulSync service on port `8008` with your TLS resolver; no extra WebSocket
config is needed.

---

## 5. Add authentication in front (recommended for public instances)

Pick one:

- **Auth proxy** — [Authelia](https://www.authelia.com/),
  [Authentik](https://goauthentik.io/), or
  [oauth2-proxy](https://oauth2-proxy.github.io/oauth2-proxy/). These sit in front
  of SoulSync and force a login (with 2FA) before any request reaches it. Best
  option for internet exposure.

  SoulSync can **trust the proxy's authenticated-user header** so the launch PIN is
  skipped once the proxy has logged you in. Set the header name in **Settings →
  Security → "Auth proxy user header"** (e.g. `Remote-User`).

  > ⚠️ **Only enable this behind a proxy you control that STRIPS any client-supplied
  > copy of that header.** Otherwise a direct visitor could send `Remote-User: admin`
  > and walk straight in. It's **off by default** — an unset header name means
  > SoulSync ignores the header entirely (a spoofed one does nothing).
- **HTTP Basic Auth** — quick and simple (nginx `auth_basic` / Caddy `basicauth`).
  Better than nothing; weaker than an auth proxy.
- **SoulSync launch PIN** — set an admin PIN in Settings. Enforced server-side, so
  it can't be bypassed by hitting the API directly — but it's a shared PIN, so
  treat it as a fallback, not your only gate.

---

## Troubleshooting

- **Live updates / progress bars don't move** → the WebSocket `Upgrade`/`Connection`
  headers are missing (nginx) or your proxy is buffering. Check section 2.
- **Login won't stick / "session expired"** → you enabled `trust_reverse_proxy` but
  are reaching SoulSync over plain `http://`. The session cookie is now HTTPS-only;
  use `https://`, or turn the setting off for direct HTTP access.
- **Scans time out** → raise `proxy_read_timeout` / `proxy_send_timeout`.
