registerDocsSection({
    id: 'automations',
    title: 'Automations',
    icon: '🤖',
    pages: [
        {
            id: 'auto-overview',
            title: 'Overview',
            lede: 'Automations run SoulSync on autopilot — schedule maintenance, react to events, and chain workflows together with a visual builder.',
            body: `
Automations follow a simple **WHEN → DO → THEN** model: a trigger fires, an action runs, and optional follow-up steps (usually notifications) happen after.

::: cards
### ⏰ Schedules
Run maintenance on a timer — process the wishlist every 30 minutes, scan the watchlist nightly, back up the database every few days.
### ⚡ Events
React the moment something happens — a download finishes, a new release is found, an import needs attention.
### 🔗 Chains
Fire custom signals from one automation and listen for them in another to build multi-step pipelines.
:::

Each automation card shows its trigger and action at a glance, the last run time and result, a countdown to the next scheduled run, and a **Run Now** button for instant execution. Cards for currently-running automations glow so you can spot activity.

SoulSync ships with [system automations](#auto-system) that handle routine maintenance out of the box — you can tune them, but you never have to build the basics yourself.

> [!TIP]
> New to automations? Start with a **Notify Only** action on an event trigger (like **Track Downloaded**). You'll see exactly when it fires, then swap in a real action once you're confident.
`
        },
        {
            id: 'auto-builder',
            title: 'Builder',
            lede: 'Build automations visually: pick a trigger, an action, and optional follow-ups.',
            body: `
Click **+ New Automation** to open the builder. Blocks from the sidebar drop into three slots:

::: steps
1. **WHEN** (trigger) — the event or schedule that starts the automation.
2. **DO** (action) — the task to perform. You can add a delay (in minutes) before it executes.
3. **THEN** (follow-ups) — up to 3 post-actions that run after DO completes, typically notifications or signals.
:::

![Automation builder](auto-builder.jpg)

## Conditions

Add **conditions** to a trigger to filter which events actually fire the automation. Match mode is **All** (AND) or **Any** (OR), with operators like \`contains\`, \`equals\`, \`starts with\`, and \`not contains\`.

For example, a **Track Downloaded** trigger with the condition \`artist contains "Miles Davis"\` only fires for Miles Davis downloads — perfect for targeted notifications.

## Groups

Automations can be organized into named **groups** (e.g. "Nightly Operations", "Download Alerts") to keep a long list tidy. Groups are just labels — they don't change behavior.

> [!NOTE]
> The same builder drives both music and video automations. On the video side it shows video triggers and actions; the concepts are identical.
`
        },
        {
            id: 'auto-triggers',
            title: 'All Triggers',
            lede: 'Every event and schedule that can start an automation.',
            body: `
## Time-based

| Trigger | What it does |
|---------|--------------|
| **Schedule** | Repeating interval — every N minutes, hours, or days |
| **Daily Time** | Every day at a specific time |
| **Weekly Schedule** | Specific days of the week at a set time |
| **Monthly Schedule** | Once a month on a chosen day |
| **App Started** | Fires when SoulSync starts up |

## Download events

| Trigger | What it does |
|---------|--------------|
| **Track Downloaded** | A track finishes downloading |
| **Download Failed** | A track permanently fails to download |
| **File Quarantined** | AcoustID verification rejects a download |
| **Batch Complete** | An album or playlist batch download finishes |

## Library & maintenance

| Trigger | What it does |
|---------|--------------|
| **Library Scan Done** | A media server library scan finishes |
| **Database Updated** | A library database refresh finishes |
| **Quality Scan Done** | Quality scan finishes (includes counts of quality-met vs low-quality) |
| **Duplicate Scan Done** | Duplicate cleaner finishes (files scanned, duplicates found, space freed) |
| **Maintenance Finding Raised** | A library maintenance job raises a new finding |
| **Maintenance Scan Done** | A library maintenance scan finishes |

## Watchlist & wishlist

| Trigger | What it does |
|---------|--------------|
| **New Release Found** | A watchlist scan finds new music |
| **Watchlist Scan Done** | The full watchlist scan completes |
| **Artist Watched** | An artist is added to the watchlist |
| **Artist Unwatched** | An artist is removed from the watchlist |
| **Wishlist Item Added** | A track is added to the wishlist |
| **Wishlist Processed** | Auto-wishlist processing finishes |

## Playlists & discovery

| Trigger | What it does |
|---------|--------------|
| **Playlist Synced** | A playlist sync completes |
| **Playlist Changed** | A mirrored playlist detects changes from the source |
| **Playlist Mirrored** | A playlist is mirrored for the first time |
| **Discovery Complete** | Playlist track discovery finishes |

## Import

| Trigger | What it does |
|---------|--------------|
| **Import Complete** | An album or track import finishes |
| **Import Needs Attention** | The import watcher leaves an item waiting for review |

## Requests & issues

| Trigger | What it does |
|---------|--------------|
| **Music Request Approved** | A music request is approved |
| **Music Request Declined** | A music request is declined |
| **Music Request Arrived** | A requested item becomes available |
| **Issue Reported** | A new issue is filed |
| **Issue Status Changed** | An issue's status changes |
| **Issue Reply** | Someone replies to an issue |

## Special

| Trigger | What it does |
|---------|--------------|
| **Signal Received** | A custom signal fired by another automation's THEN step |
| **Webhook Received** | An external POST hits the automation webhook endpoint |
`
        },
        {
            id: 'auto-actions',
            title: 'All Actions',
            lede: 'Every task an automation can perform.',
            body: `
## Wishlist & downloads

| Action | What it does |
|--------|--------------|
| **Process Wishlist** | Retry downloads — all, albums only, or singles only |
| **Clean Up Wishlist** | Remove duplicate or already-owned tracks from the wishlist |
| **Search & Download** | Search for a query and download the result |
| **Seeding Sweep** | Release music torrents once seed goals are met |

## Watchlist & discovery

| Action | What it does |
|--------|--------------|
| **Scan Watchlist** | Check watched artists and followed labels for new releases |
| **Scan Watchlist Podcasts** | Check watchlisted podcasts for new episodes and prune expired ones |
| **Scan Audiobook Library** | Index audiobook folders and loose files |
| **Update Discovery** | Refresh the discovery artist pool |

## Library

| Action | What it does |
|--------|--------------|
| **Scan Library** | Trigger a media server library scan |
| **Update Database** | Refresh the library database (incremental or full) |
| **Update Database (Hourly)** | Lightweight incremental refresh that catches files added outside SoulSync |
| **Deep Scan Library** | Full library comparison without losing enrichment data |
| **Run Quality Scan** | Scan for low-quality audio files |
| **Run Duplicate Cleaner** | Scan for and remove duplicate files |
| **Clear Quarantine** | Delete all quarantined files |
| **Clear Quarantine + Empty Recycle Bin** | Combined destructive cleanup sweep |

## Playlists

| Action | What it does |
|--------|--------------|
| **Refresh Mirrored Playlist** | Re-fetch playlist tracks from the source (one or all) |
| **Sync Playlist** | Sync a specific playlist to your media server |
| **Discover Playlist** | Find official metadata for playlist tracks |
| **Playlist Pipeline** | Full pipeline for mirrored playlists (refresh → discover → sync) |
| **Personalized Playlist Pipeline** | Sync personalized/generated playlists |

## Listening history

| Action | What it does |
|--------|--------------|
| **Import Last.fm Listening** | Pull listening history from Last.fm (supports full backfill) |
| **Import ListenBrainz Listening** | Pull listening history from ListenBrainz |

## Maintenance

| Action | What it does |
|--------|--------------|
| **Refresh Beatport Cache** | Scrape the Beatport homepage and warm the data cache |
| **Clean Search History** | Remove old Soulseek searches (keeps the 50 most recent) |
| **Clean Completed Downloads** | Clear completed downloads and empty directories from the input folder |
| **Full Cleanup** | Quarantine, download queue, import folder, and search history in one sweep |
| **Backup Database** | Create a timestamped database backup |

## Utility

| Action | What it does |
|--------|--------------|
| **Run Script** | Execute a custom script |
| **Notify Only** | No action — just fire the THEN notifications. Great for testing triggers. |
`
        },
        {
            id: 'auto-then',
            title: 'Then-Actions & Signals',
            lede: 'What happens after the action: notifications, webhooks, and signal chaining.',
            body: `
After the DO action completes, up to **3 THEN actions** run:

- **Discord Webhook** — post a message to a Discord channel
- **Pushbullet** — push notification to phone or desktop
- **Telegram** — send a message via a Telegram bot
- **Webhook** — POST to any URL (feed a dashboard, bot, or your own scripts)
- **Fire Signal** — emit a custom named signal that other automations can listen for via the **Signal Received** trigger

## Message variables

All notification messages support **variable substitution**: \`{name}\`, \`{status}\`, \`{time}\`, \`{run_count}\`, plus context-specific variables from the action result (e.g. \`{title}\`, \`{artist}\` on download events).

**Test Notifications**: use the test button next to any notification THEN step to send a test message before saving — it verifies your webhook URL, API key, or bot token actually works.

## Signal chaining

**Fire Signal** + **Signal Received** lets you build multi-step workflows: the first automation finishes its action, fires a signal like \`overlays_done\`, and a second automation listening for that signal picks up where it left off.

> [!NOTE]
> Chains are safe by design: cycle detection (DFS) prevents infinite loops, chains cap at **5 levels** deep, and there's a **10-second cooldown** between signal fires.
`
        },
        {
            id: 'auto-history',
            title: 'Execution History',
            lede: 'See what ran, when, and what happened.',
            body: `
Each automation card shows its **last run time**, **run count**, and last result. For scheduled automations, a **countdown timer** shows when the next run will occur.

![Automation execution history](auto-history.jpg)

- **Run Now** executes any automation immediately, regardless of schedule. The result updates on the card in real time, and running automations display a glow effect.
- **Stall detection**: if an action runs for more than 2 hours without completing, it's automatically flagged as stalled and terminated to prevent resource leaks.
- The **Dashboard activity feed** logs every automation execution with timestamps, so you can review the full history of what ran and when.

> [!TIP]
> Debugging a misbehaving automation? Check the Dashboard activity feed first for its recent runs and results, then look at the app logs for the full story.
`
        },
        {
            id: 'auto-system',
            title: 'System Automations',
            lede: 'Built-in maintenance automations that keep SoulSync healthy out of the box.',
            body: `
SoulSync ships with built-in **system automations** that handle routine maintenance. You can enable/disable them and adjust their schedules, but you can't delete or rename them.

![System automations](auto-system.jpg)

| Automation | Schedule |
|------------|----------|
| Auto-Process Wishlist | Every 30 minutes |
| Auto-Scan Watchlist | Every 24 hours |
| Auto-Scan Podcasts | Every 6 hours |
| Auto-Process Audiobook Wishlist | Every hour |
| Auto-Scan Audiobook Authors | Every 24 hours |
| Auto-Scan Audiobook Library | Every 24 hours |
| Empty Audiobook Recycle Bin | Every 24 hours |
| Auto-Scan After Downloads | On batch complete |
| Auto-Update Database After Scan | On library scan completed |
| Auto-Update Database (Hourly) | Every hour |
| Refresh Beatport Cache | Every 24 hours |
| Clean Search History | Every hour |
| Clean Completed Downloads | Every 5 minutes |
| Seeding Sweep | Every 30 minutes |
| Last.fm Listening Sync | Every hour |
| ListenBrainz Listening Sync | Every hour |
| Auto-Deep Scan Library | Every 7 days |
| Auto-Backup Database | Every 3 days |
| Full Cleanup | Every 12 hours |
| Weekly Cleanup | Every 7 days (off by default — it deletes files) |

> [!WARNING]
> **Weekly Cleanup** is seeded switched off because it permanently deletes files (quarantine + recycle bin). Turn it on only if you want fully automatic destructive cleanup.

> [!TIP]
> Don't duplicate what the system rows already do. To get notified about a system run, add a THEN notification to the system automation itself. To change cadence, edit its schedule — no new automation needed.
`
        },
    ]
});
