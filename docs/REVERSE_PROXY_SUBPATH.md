# Hosting SoulSync under a URL path

Set `SOULSYNC_URL_BASE=/soulsync` in the SoulSync process environment and restart it.
Then open `https://media.mydomain.com/soulsync/`.

The default is empty: existing root-hosted installs need no changes. This is a
startup deployment setting, not a per-profile preference. Values must begin with
`/`; nested paths such as `/media/soulsync` work. Trailing slashes are normalized.
Use letters, numbers, hyphens and underscores in path segments. Invalid values
fail startup instead of silently producing broken links.

## Docker Compose

Add to the existing SoulSync service (keep its other settings):

```yaml
environment:
  SOULSYNC_URL_BASE: /soulsync
```

Recreate the container after changing its environment. Merely restarting a
container does not apply edited Compose environment variables.

## Nginx example

Inside the existing TLS `server` block for `media.mydomain.com`:

```nginx
location = /soulsync {
    return 308 /soulsync/;
}

location /soulsync/ {
    # No trailing URI slash: preserve /soulsync in the upstream request.
    proxy_pass http://127.0.0.1:8008;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 3600s;
    proxy_buffering off;
}
```

Replace the upstream address with the reachable SoulSync container/service.
Keep your existing authentication and TLS configuration. The prefix setting
supports proxies that preserve the prefix and proxies that strip it, so a
trailing-slash `proxy_pass http://127.0.0.1:8008/;` also works. Set the same
external prefix in SoulSync in either case. No response `sub_filter` is needed.
Forward Socket.IO traffic beneath `/soulsync/socket.io/`, including upgrades.
The configured prefix is explicit; arbitrary `X-Forwarded-Prefix` headers do
not change it.

## What the setting covers

- Flask routing and generated asset URLs, relative redirects and session-cookie path.
- React routes, legacy music/video navigation, refreshable deep links and profile redirects.
- API fetches, Request objects, XHR, event streams and Socket.IO paths.
- Dynamic image/audio/video URLs, generated markup, CSS artwork and browser exports.
- Production asset imports, app manifest, service-worker scope and per-mount caches.

Internal route IDs stay `/discover`, `/video/...`, etc. Only browser-facing URLs
receive the prefix. External provider links and absolute external URLs are not
rewritten. Root hosting keeps its existing browser APIs without wrapper installation.

For provider OAuth integrations, update the redirect URI registered with the
provider and the integration's configured callback URL to include `/soulsync`
(e.g. `/soulsync/callback` where that provider previously used `/callback`).
The deployment setting cannot edit an external provider's registered URLs.

After changing the mount, sign in at the new URL and update bookmarks or an
installed PWA. An old service worker at the host root belongs to the previous
installation; remove that old registration if you have moved the application.

## Verification

1. Load `/soulsync/` and a direct music/video deep link, then refresh the page.
2. In browser Network, SoulSync API/media/static/socket requests should stay
   beneath `/soulsync/`; requests to external artwork/provider hosts stay external.
3. Check profile login, navigation/back/forward, an image, audio playback, a
   download/export and live progress updates.
4. Check a neighboring application on the same domain still works.

Automated checks are in `tests/webui/test_url_base.py`,
`webui/tests/url-base.spec.ts`, and the router tests. The browser tests cover
root hosting, `/soulsync`, and `/media/soulsync`. The backend checks cover
preserved/stripped request prefixes, cookies, redirects and untouched range data.
