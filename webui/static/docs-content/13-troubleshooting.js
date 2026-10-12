registerDocsSection({
    id: 'troubleshooting',
    title: 'Troubleshooting',
    icon: '🩺',
    pages: [
        {
            id: 'ts-logs',
            title: 'Logs',
            lede: 'Where to find SoulSync\'s logs and what each one contains.',
            body: `
Logs live in the \`logs/\` directory (in Docker: \`/app/logs/\`):

| File | Contents |
|------|----------|
| \`app.log\` | Main application log — requests, workers, errors |
| \`post_processing.log\` | Download post-processing activity |
| \`acoustid.log\` | AcoustID fingerprinting activity |
| \`source_reuse.log\` | Source reuse / download source decisions |

The Settings → Logs tab streams live logs with real-time operational diagnostics, so you can watch activity as it happens without SSHing into the box.

> [!TIP]
> When something breaks, start with \`app.log\` — it carries the request and worker trail for nearly every failure. Raise the [log level](#set-other) to Debug first if you need more detail, then drop it back to Info afterward.
`
        },
        {
            id: 'ts-debug',
            title: 'Debug Info',
            lede: 'One click collects everything needed to diagnose a problem.',
            body: `
The **Copy Debug Info** button — in the **Help & Docs sidebar header**, just under the search box — collects a comprehensive diagnostic snapshot. Pick how many log lines to include and which log file to sample (app, post-processing, acoustid, or source reuse), then click:

- System info: version, OS, Python version, Docker status, ffmpeg version, uptime
- Service status: media server, Soulseek, Spotify, and metadata services
- Library and database statistics
- Paths and their existence/writability checks
- Config summary (source mode, quality profile, organization template, feature toggles)
- Worker status
- Recent log lines

> [!NOTE]
> Debug output is **safe to share**: passwords, API keys, and tokens are never included — credential fields appear only as "connected / not connected" booleans. Still, give the pasted output a quick glance before posting it publicly.

Paste it into a GitHub issue or the Discord when asking for help — it answers most of the follow-up questions you'd otherwise get.
`
        },
        {
            id: 'ts-common',
            title: 'Common Issues',
            lede: 'Quick fixes for the problems people hit most often.',
            body: `
## Downloads stuck or failing

1. Check the download queue for error messages on individual tracks.
2. Verify your download source credentials in Settings (Soulseek via slskd, Tidal, Qobuz, etc.).
3. Check \`app.log\` and \`post_processing.log\` for the specific failure.
4. Try the [track redownload wizard](#lib-redownload) to search a different source.

## Import folder not detected

- Confirm the import path (Settings \u2192 Library \u2192 Folders \u2192 **Import Folder**) uses the **container path** (e.g. \`/app/Staging\` in Docker), not the host path.
- Check the folder's ownership matches the container's PUID/PGID — the import page tells you when it can't read the folder.
- Verify the volume is actually mounted in your compose file.

## Media server not syncing

1. Confirm exactly one media server is set **active** in Settings.
2. Use **Test Connection** to verify host, port, and credentials.
3. Trigger a manual **Scan Library** from the **Tools page → Media Server Scan** card (Plex only).
4. For Navidrome, changes are auto-detected — no scan needed.

## PWA install not offered / Spotify needs HTTPS

Both are browser/platform requirements, not SoulSync bugs:

- **PWA install**: Chrome (and all browsers) only offer "Install app" over **HTTPS** (or localhost). If you access SoulSync via \`http://192.168.x.x:8008\`, the install prompt won't appear. SoulSync ships a valid manifest and service worker — the only blocker is the protocol.
- **Spotify OAuth**: Spotify's developer dashboard now requires **HTTPS** redirect URIs. A local \`http://\` address won't be accepted.

**Workaround**: Serve SoulSync over HTTPS with a reverse proxy. Tailscale works well:

\`\`\`bash
sudo docker exec tailscale tailscale serve --bg --https=443 http://127.0.0.1:8008/
\`\`\`

This gives you an \`https://your-device.tailnet.ts.net\` address that satisfies both the PWA and Spotify requirements. You'll need to be on your tailnet (or have the tunnel active) to reach it.

## Metadata looks wrong

- Open the artist in the [Enhanced Library Manager](#lib-enhanced) and check the service match chips — a wrong Spotify or MusicBrainz link poisons everything downstream.
- Manually search and link the correct ID, then run enrichment again.

## Automation didn't fire

1. Check the automation card: last run time, last result, and next-run countdown.
2. Click **N runs** on the automation card to open its run-history modal.
3. For signal chains, check that the upstream automation actually fired its signal — and remember the 10-second cooldown and 5-level depth limit.
4. Look in \`app.log\` for the automation name around the expected fire time.

## Spotify: redirect URI rejected (INVALID_CLIENT / mismatch)

1. The **Redirect URI** in SoulSync Settings and the one in your [Spotify Developer Dashboard](https://developer.spotify.com/dashboard) must match **exactly** — scheme, host, port, path, and trailing slash.
2. Spotify only allows plain \`http://\` for loopback (\`http://127.0.0.1:8888/callback\` — \`localhost\` is rejected). Any other host — a NAS, a remote server, or Docker reached by hostname or LAN IP — must be **HTTPS**, served through a reverse proxy or Tailscale. See [Connecting services](#gs-connecting).
3. Save the dashboard change, then click **Authenticate** in SoulSync again.
`
        },
        {
            id: 'ts-reporting',
            title: 'Getting Help',
            lede: 'How to report a bug so it gets fixed fast.',
            body: `
## Before you report

1. Reproduce the problem once more so you can describe the exact steps.
2. Check the [logs](#ts-logs) and [debug info](#ts-debug) — include both.
3. Search existing issues to avoid duplicates.

## Where to go

- **GitHub Issues**: [github.com/Nezreka/SoulSync/issues](https://github.com/Nezreka/SoulSync/issues) — bug reports and feature requests
- **Discord**: [discord.gg/wGvKqVQwmy](https://discord.gg/wGvKqVQwmy) — community help and discussion

## What to include

- SoulSync version and how you run it (Docker, bare metal, OS)
- Steps to reproduce
- Expected vs. actual behavior
- Debug info output (credentials are already excluded)
- Relevant log lines around the failure

> [!TIP]
> A good bug report is a gift. Version + steps + logs gets most issues diagnosed in one reply instead of five.
`
        },
    ]
});
