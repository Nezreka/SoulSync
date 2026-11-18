registerDocsSection({
    id: 'troubleshooting',
    needs: 'admin',
    title: 'Troubleshooting',
    icon: '🩺',
    pages: [
        {
            id: 'ts-help',
            needs: 'everyone',
            title: 'The Help Button',
            lede: 'The ? at the bottom right opens help beside the page you are on.',
            body: `
## Opening it

Click the **?** at the bottom right, or press \`?\` or \`F1\` anywhere. Help opens as a panel on the right, beside the page, so you can follow along while you read. On a phone it fills the screen, with a close button at the top. Press \`Esc\` or click **?** again to close it.

## What's in it

- **Search** at the top: help articles, settings, pages and actions in one list. See [Search and Ctrl K](#ts-search).
- **Status** (admins): one line that says everything is running, or how many things need you. Click it for [Health](#ts-health).
- **On this page**: what the page you are on is for, and live answers built from what is happening right now, like why three downloads are still queued or which folder isn't writable. Each answer has the buttons that fix it. A page with nothing to say shows no answers.
- **Read more**: the help articles worth reading on that page. They open inside the panel; **Open in Help & Docs** takes you to the full page.
- **Tour**: on pages that have one, a row that starts it. See [Tours](#ts-tours).
- **What's new**, **Shortcuts**, **Help & Docs**, and **Logs** for admins.
- **Get help**: [Report a problem](#ts-debug), Discord and GitHub.

The footer shows your version, whether you run in Docker, and whether an update is out.

## The dot on the ?

When Health finds something that needs an admin, a small amber dot shows on the **?**. Only errors light it, never warnings, and only admins see it.

## For members

A member gets the same panel without the admin parts: no status line, no Health, no Logs and no diagnostics. Search finds only the pages they can open, their own My Account settings, and the actions they are allowed. Instead of Report a problem they get:

- **Report a song, album or title**: use **Report** on its **⋯** menu. It goes to your admin.
- **Your reports**: what you reported and what happened to each one.
- **Ask on Discord**.
`
        },
        {
            id: 'ts-search',
            needs: 'everyone',
            title: 'Search and Ctrl K',
            lede: 'One search for help articles, settings, pages and actions.',
            body: `
Press \`Ctrl K\` (\`⌘K\` on a Mac) on any page to open the search palette in the middle of the screen. The search field at the top of the Help panel is the same search.

## What it finds

| Group | What | Example |
|------|------|------|
| Actions | things SoulSync can do right now | Quick scan the library, Scan the watchlist now, Check folders and services now, Open the logs |
| Settings | a setting, by its own name | Download path, Plex token |
| Pages | every page you can open | Go to Wishlist |
| Help | every help article, by title, heading and text | Folder Setup |

Results for the page you are on come first, then the ones you used recently. Use the arrow keys to move, \`Enter\` to open, \`Esc\` to close. A shortcut shows beside an action that has one.

Picking a setting opens Settings on the right tab and takes you to the field. Picking an article opens it inside the Help panel.

When you type a name, the last row offers **Search music for** that name, which hands it to the Search page.

> [!NOTE]
> \`Ctrl K\` opens this search everywhere, Settings included. On the Settings page, press \`/\` to jump to the Settings search box instead.

Members see only the pages and actions their profile allows, and no settings beyond My Account.
`
        },
        {
            id: 'ts-health',
            title: 'Health',
            lede: 'Every check SoulSync runs on itself, in one list, with the fix beside each problem.',
            body: `
Health is for admins. Open it from the status line at the top of the Help panel, or search for **Check folders and services now**.

## What it says

One sentence on top, like "Two things need you. Everything else is running." Then:

- **Needs you**: errors. These light the amber dot on the **?**.
- **Worth a look**: warnings.
- **Finish setting up**: the setup items you haven't done yet.
- **Running fine**: every check that passed, folded away, so you can see what was checked.

Each problem has a plain title, a line on why, a fix button when there is one, and **Learn more** when an article explains it. **Check again** runs everything fresh. Results are kept for 30 seconds otherwise, and a check that can't answer in time says it couldn't check rather than holding up the rest.

## What it checks

| Area | Check | Fix offered |
|------|------|------|
| Services | your media server answers | Test again, or open Connections |
| Services | which metadata source you use, and whether Spotify is rate limited and for how long | nothing to do: it resumes on its own |
| Services | each download source answers, and recent download client failures | Test again, or open Downloads settings |
| Folders | the download, transfer, import and music library folders exist and SoulSync can write to them. In Docker it says when a path is probably not mounted | open that folder's setting |
| Folders | the path mapping between your download client and SoulSync, so finished files are where SoulSync looks | open the path mapping setting |
| Disk | free space on the music and download folders, and the video folders | open the folder settings |
| Video | library folders, recycle folder, maintenance errors, sources, indexers, download clients, import lists, YouTube cookies | a link to the right setting or page for each |
| Workers | enrichment workers that are paused or stopped | Resume |
| Downloads | downloads waiting in review | Open review |
| Automations | an automation whose last run failed | open it |
| Setup | unfinished setup items | each item's own action |
| Updates | an update is out, and how important it is | release notes |

Disk space warns under 2 GB free, or under the minimum free space you set for video, and turns into an error under 1 GB.

> [!TIP]
> Most problems are folders. If Health says a folder isn't writable in Docker, check that the folder's owner matches your PUID and PGID, and that the path is mounted in your compose file. See [Docker & Deployment](#gs-docker).
`
        },
        {
            id: 'ts-logs',
            title: 'Logs',
            lede: 'Where to find SoulSync\'s logs and what each one contains.',
            body: `
Logs live in the \`logs/\` directory (in Docker: \`/app/logs/\`):

![The log viewer in Settings: pick a log file, filter by level and watch entries stream live](ts-logs.jpg)

| File | Contents |
|------|----------|
| \`app.log\` | Main application log — requests, workers, errors |
| \`post_processing.log\` | Download post-processing activity |
| \`acoustid.log\` | AcoustID fingerprinting activity |
| \`source_reuse.log\` | Source reuse / download source decisions |

The Settings → Logs tab streams live logs with real-time operational diagnostics, so you can watch activity as it happens without SSHing into the box.

> [!TIP]
> When something breaks, start with \`app.log\` — it carries the request and worker trail for nearly every failure. Raise the [log level](#set-other) to Debug first if you need more detail, then drop it back to Info afterward.

## Downloading logs

Admins can download every log from the **Logs** tile in the Help panel, or with the **Download** button on the Settings › Logs tab. The list shows each log with its older copies (\`app.log.1\`, \`.2\`, \`.3\`), how big each one is and when it was last written. Download one file, one log with all its older copies, or everything as one zip with a README that says what is inside.

**Remove secrets** is on by default: every line has its keys and tokens removed as it downloads. Turning it off asks you once first, because raw logs can contain API keys and tokens.
`
        },
        {
            id: 'ts-tours',
            needs: 'everyone',
            title: "Tours, What's New and Shortcuts",
            lede: 'Short walkthroughs, the release notes, and every keyboard shortcut.',
            body: `
## Tours

Three short tours point at the real thing on screen, one step at a time:

- **Getting around**: the sidebar, the music and video sides, profiles, Server Activity, notifications, and help itself.
- **Your first download**: search, pick a result, the download window, and following it in Downloads.
- **Your first video request**: find a movie or show, open it, ask for it (or get it, if you can download), and Requests.

Start one from **Read more** in the Help panel, on a page the tour is about. **Getting around** is offered on the Dashboard. Use \`←\` and \`→\` to step, \`Esc\` to stop. A step whose target isn't on screen is skipped.

## What's new

The **What's new** tile in the Help panel lists what shipped in this version, newest first, with **Show me** when there is something to look at. It shows the version as new until you open it. The version button in the sidebar footer shows the same release notes.

## Shortcuts

The **Shortcuts** tile lists every keyboard shortcut SoulSync has: help, search, the player, the expanded player, page keys like the calendar's, and the docs. The list is built from the code that handles each key, so it stays true. On a phone it says shortcuts need a keyboard.
`
        },
        {
            id: 'ts-debug',
            title: 'Report a Problem',
            lede: 'A bug report with diagnostics you can read first, secrets removed.',
            body: `
Open **Report a problem** from the Help panel (admins), or search for it with \`Ctrl K\`. The **Report a problem** button in the Help & Docs sidebar opens the same thing.

## 1. What happened?

Write what you did and what went wrong. **Where** is filled in with the page you were on.

## 2. What's included

Every line is a switch, and the preview under it is exactly the text that gets shared. Switch a line off and it leaves the preview, the copy and the file.

| Line | On by default | What it is |
|------|------|------|
| Version and platform | yes | SoulSync, Docker, Python, ffmpeg |
| Services and Health | yes | what is connected, and today's Health findings |
| Settings, secrets removed | yes | only what you changed; keys, tokens and passwords show as \`***\` |
| Folder paths | yes | turn it off if your paths show your name |
| Last 200 log lines | yes | from \`app.log\`, secrets removed |
| Recent errors only | no | the errors from the last 24 hours, secrets removed |
| Attach full logs (zip) | no | every log as a zip, downloaded beside the bundle |

Secrets show as \`***\` so you can see the redaction happened. API keys, tokens, passwords and logins inside URLs are removed from settings, from every log line and from the whole text at the end.

## 3. Send it

- **Open a GitHub issue**: opens a new issue with the title, version, platform and your summary filled in, and downloads the full bundle as a file. GitHub links can't carry the whole thing, so drag the file from your downloads into the issue.
- **Copy for Discord**: a short block that fits in one message.
- **Download the bundle**: the file, named like \`soulsync-diagnostics-3.6.7-2026-11-17.txt\`.

> [!NOTE]
> Give the preview a quick read before you post it publicly. It is redacted, but you know your own setup best.
`
        },
        {
            id: 'ts-common',
            title: 'Common Issues',
            lede: 'Quick fixes for the problems people hit most often.',
            body: `
![Status indicators on the dashboard: a media server shown as disconnected, with Soulseek disconnected too](ts-common.jpg)

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
2. Look at [Health](#ts-health): it may already name the problem and the fix.
3. Search existing issues to avoid duplicates.

## Where to go

- **Report a problem** in the Help panel: writes the GitHub issue for you, with the diagnostics attached. See [Report a problem](#ts-debug).
- **GitHub Issues**: [github.com/Nezreka/SoulSync/issues](https://github.com/Nezreka/SoulSync/issues) — bug reports and feature requests
- **Discord**: [discord.gg/wGvKqVQwmy](https://discord.gg/wGvKqVQwmy) — community help and discussion

## What to include

- SoulSync version and how you run it (Docker, bare metal, OS)
- Steps to reproduce
- Expected vs. actual behavior
- The diagnostics bundle from [Report a problem](#ts-debug) (secrets are already removed)
- Relevant log lines around the failure

If you are a member, your admin runs the server: report a wrong song, album or title from its **⋯** menu and it goes to them. **Your reports** in the Help panel shows what happened to each one.

> [!TIP]
> A good bug report is a gift. Version + steps + logs gets most issues diagnosed in one reply instead of five.
`
        },
    ]
});
