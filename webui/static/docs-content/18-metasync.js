registerDocsSection({
    id: 'metasync',
    title: 'MetaSync',
    icon: '🌐',
    pages: [
        {
            id: 'ms-overview',
            title: 'What MetaSync Is',
            lede: 'An optional companion app that connects your SoulSync to SIN, a network of SoulSync installs that share anonymous taste signals and get weekly charts back.',
            body: `
## The short version

**MetaSync** is a small program you run next to SoulSync. Its first network is **SIN, the SoulSync Intelligence Network**. Once a week your MetaSync tells SIN, anonymously, which albums your install wishlisted, kept, or downloaded. SIN adds up what every install sent and turns it into charts: **Trending**, **Most Wanted**, **Just Landed**, and more.

SoulSync fetches those charts every day, matches every song in the background, and shows them under **SoulSync Charts**. Opening one gives you a normal playlist. Nothing is synced or downloaded until you press **Sync & download** yourself.

MetaSync is completely optional. SoulSync works the same without it, and nothing is shared until you install MetaSync, join a pool **and** turn off Dry run.

![How MetaSync works: SoulSync, your MetaSync node, and the SIN network](metasync-flow.svg)

## How it flows

::: steps
1. **Weekly read (on your machine).** Early each Monday your MetaSync node reads last week's activity from SoulSync's local API: what you watchlisted, wishlisted, added, and downloaded. It also reads what you listened to, if you turned that on.
2. **One report a week (the only thing that leaves).** The node turns that week into yes/no signals keyed only by MusicBrainz IDs, such as "this install wishlisted album X". No titles, file paths, play counts or library listings are sent.
3. **Counted on arrival.** The pool server checks the report, adds +1 to each album's count for the week, and keeps nothing else from it.
4. **Only crowds count.** An album appears in a chart only once **15 or more different installs** reported it. Below that, it is never shown to anyone.
5. **Charts are built once, for everyone.** The composer looks up each charted album's songs on MusicBrainz and picks its 2 most popular by ListenBrainz's public statistics. Your node then pulls the finished charts.
6. **SoulSync shows them.** SoulSync asks your node for the charts once a day, matches every song in the background, and shows the ready ones on your dashboard and Discover page.
:::

## The pieces

::: cards
### 🖥️ MetaSync node
Runs on your machine (Docker or bare metal) with its own web page on port 8790. Reads SoulSync locally, builds the weekly report, talks to the pool.
### ☁️ SIN pool
The network's server. Takes reports, keeps only counts, and serves the charts back to nodes.
### 🔑 Portal
metasync.ssync.net. Where you sign in, create your pool key, and approve your node. It is the only place a key is tied to an account.
### 🎵 SoulSync
Pulls the charts from your node, matches them, and offers them as playlists. It never contacts the pool itself.
:::

> [!NOTE]
> Connecting MetaSync to SoulSync joins **nothing**. It only lets the two programs on your machine talk. Joining a pool is always a separate step that needs both a pool URL and your pool key.
`
        },
        {
            id: 'ms-setup',
            title: 'Setting It Up',
            lede: 'From nothing to your first charts: get a key, run the node, connect, approve, report.',
            body: `
## Before you start

You need a running SoulSync and somewhere to run MetaSync: the same machine, or another one on your network. MetaSync is a separate install. See the MetaSync repository for its Docker compose example.

## Setup steps

::: steps
1. **Get a pool key.** Sign in at metasync.ssync.net and create a key. It is shown **once**, so copy it somewhere safe. The portal only stores a hash of it.
2. **Run MetaSync.** Start the node (Docker, or \`python3 run.py\` on bare metal), open http://localhost:8790, and set an admin password on first run.
3. **Connect it to SoulSync.** In SoulSync go to **Settings → Connections → MetaSync → Connect**. SoulSync looks for the node and shows a pairing code. Approve the code on the MetaSync dashboard and the two programs swap keys automatically. Pasting the keys by hand works too.
4. **Join the pool.** Paste your pool key into the same MetaSync settings. SoulSync hands it to your node; SoulSync never contacts the pool itself. You can also add the pool on the node's Pools page.
5. **Approve your node.** The first time your node uses the key, it shows **waiting for approval**. Go back to the portal and approve it, and check that the fingerprint matches the one on your node's dashboard. Only a node you approved can ever use your key.
6. **Check the report, then send it.** The node starts in **Dry run**: it builds the exact weekly report and shows it on the **Payload preview** page, but sends nothing. When you're happy with it, turn Dry run off and run it. After that the node reports once a week on its own.
:::

## Getting charts into SoulSync

Nothing else to set up. The system automation **Refresh MetaSync Charts** runs once a day and pulls whatever charts your node has. To see them right away, run it from **Automations**. Charts appear under **SoulSync Charts** once their songs are matched.

> [!TIP]
> Very large library? Your node bridges album editions to MusicBrainz album groups at about one lookup per second, at most 5,000 per run, and caches every answer forever. SoulSync's MusicBrainz worker fills in most of those IDs ahead of time, so most libraries finish in the first run.

## Disconnecting

**Settings → Connections → MetaSync → Disconnect** removes the connection and revokes the key SoulSync issued to the node. To leave the network completely, also revoke your pool key or unbind your node on the portal. That cuts access on the very next request.
`
        },
        {
            id: 'ms-shared',
            title: 'What Gets Shared',
            lede: 'Exactly eight yes/no signals per album or artist per week, keyed by MusicBrainz IDs. Two of them are off unless you switch them on.',
            body: `
## The eight signals

Each weekly report covers the **last full week** (Monday to Monday, UTC) and lists that week's events. It never lists your library or your wishlist.

| Signal | What it means | Default |
|---|---|---|
| \`watchlist_add\` | You started watching this artist this week | On |
| \`wishlist_add\` | You wishlisted this album this week | On |
| \`hunt_demand\` | Wishlisted 7+ days ago and still not downloaded | On |
| \`recent_add\` | This album landed in your library this week | On |
| \`import_keep\` | This album reached 14 days in your library this week (sent once per album, ever) | On |
| \`download_grab\` | You re-grabbed, upgraded or gap-filled an album you already had | On |
| \`download_churn\` | Downloaded 14+ days ago, then removed, confirmed by two weekly checks | **Off**: "Share drop signals" |
| \`listen_album\` | You played 3+ different songs from this album this week (your 50 most-played at most) | **Off**: "Share listening signals" |

Each signal is one bit: per album, per week, your install says **yes or nothing**. "Played it 40 times" and "played it 3 times" look exactly the same.

## The two switches

Both live on the MetaSync dashboard and both start **off**:

- **Share drop signals** reports albums you deleted after downloading. It helps rank **Loved & Kept** by subtracting albums people got rid of.
- **Share listening signals** feeds **On Repeat**. With it off, MetaSync does not read your listening history at all. With it on, SoulSync gives the node per-album counts for the one week, from the shared listening history only; a profile that keeps its own listening history is never included. The node reduces those counts to the one-bit signal above.

## Big weeks are held back

If one kind of event happens more than 500 times in a week (a bulk import, a migration, a drive remounting), that kind is **not reported** that week, and the preview says so. A report can never become a copy of your library.

## Never collected

- Listening history, play counts or play times (beyond the optional one bit per album)
- Song, artist or album names as text
- File paths, folder names, library size or library listings
- Usernames, emails, your SoulSync URL, or your IP address as stored data

> [!TIP]
> You can always see exactly what will be sent. The node's **Payload preview** page shows the complete last report, word for word.
`
        },
        {
            id: 'ms-privacy',
            title: 'How Your Privacy Is Protected',
            lede: 'Safeguards that work together, plus the honest limits of the design.',
            body: `
## The safeguards

::: cards
### 1️⃣ One bit per album
Your install reports each album once a week at most, as "yes". A chart count means "this many different installs", nothing more.
### 👥 15 installs or it doesn't exist
An album shows up only when 15+ different installs reported it the same week (k = 15). Below that, nobody can see it.
### 🧮 Counted, not stored
The pool adds +1 per album when your report arrives and keeps only the totals. No copy of your report is kept.
### 🔄 No week-to-week tracking
Each report carries a token that changes every week, used only to stop a double send. This week's token can't be linked to last week's.
### 🔐 Keys stay yours
The portal stores only hashes of keys. Your node signs every request with its own key, and your pool key works only from the one node you approved.
### 🚫 Members can't publish
Nodes can only send their own signals and read charts. Only the official MetaSync composer builds and publishes charts.
:::

## What the network stores

- **Counts**: per week, per signal, per album: how many installs reported it. Nothing that says which installs.
- **A token per report**: only so the same install can't be counted twice in one week. It holds no signals.
- **Published charts**: the playlists, built from MusicBrainz's public catalogue.

Counts under 15 are kept for 3 weeks, used for nothing public, then deleted.

## What MusicBrainz and ListenBrainz see

Only public questions such as "what songs are on album X". Your node asks MusicBrainz which album group an edition belongs to. The composer asks for the track lists of charted albums and how popular each song is. Nothing in those requests comes from you.

## Honest limits

No design hides everything. These are the trade-offs as built:

- **The server sees connections.** Like any website, the pool server sees a request arrive from an IP address at a time. It doesn't store it.
- **A report arrives whole.** Inside one request the server briefly holds your whole weekly report before counting it. That is why reports are capped, describe one week of events, and never list your library.
- **Popular is public.** Above 15 installs, counts are the product, and anyone in the network can see them. Which installs is never in the data.
- **Your node's files are yours to protect.** The node keeps your SoulSync key and pool key on your disk. Anyone with the node's data folder has its identity.

> [!NOTE]
> The complete data map, covering every field, every table and every third party, is DATA.md in the MetaSync repository.
`
        },
        {
            id: 'ms-charts',
            title: 'SoulSync Charts',
            lede: 'What the charts are, how they are built, and what happens when you open one.',
            body: `
## Where they show up

- **Dashboard**: the **SoulSync Charts** tab in the rails beside Recently Added and Fresh Releases.
- **Discover**: the **SoulSync Charts** shelf. It hides itself when MetaSync isn't connected or nothing is ready yet, and you can move or hide it like any Discover shelf.

A chart appears only after **every song in it is matched**, so anything you open is ready to sync.

## The charts

| Chart | What puts an album on it |
|---|---|
| **Trending in SoulSync** | Wishlisted or grabbed this week, compared with recent weeks, so a riser beats a steady seller |
| **Most Wanted** | Wishlisted, or still hunted after a week |
| **Just Landed** | Newly added to libraries this week |
| **Loved & Kept** | Kept past 14 days, minus albums people deleted |
| **Rising** | Demand at least doubled since last week |
| **Breakout Artists** | Artists whose watchlist adds at least doubled (20+ installs) |
| **On Repeat** | Played by 15+ installs that share listening signals |
| **Top Tracks · from ListenBrainz** | ListenBrainz's public weekly song chart, at most 2 songs per artist |

Charts also come in **genre and decade editions** ("Trending: Hip Hop", "Trending: 2020s") when at least 8 charted albums share a tag. An edition that would mostly repeat its main chart is skipped.

## How a chart is built

- **25 albums, 50 songs.** Each charted album contributes its **2 most popular songs**, picked from ListenBrainz's public listening statistics, or its first 2 songs when ListenBrainz has no data.
- **Best songs first.** The order is every album's best song in chart order, then every album's second song. No album plays twice in a row, and the order only changes when the chart does, so a synced copy doesn't churn every day.
- **Early gaps say where they came from.** While the network is young, Trending and Just Landed fill empty slots from ListenBrainz's public weekly charts, and the name tells you: "Trending · from ListenBrainz" (all public), "Trending · SoulSync + ListenBrainz" (mixed), or plain "Trending in SoulSync". As more installs report, network albums push the public ones out automatically.
- **Fresh every week.** Each chart describes one week. Trending compares against the 3 weeks before; nothing older counts.

## Opening a chart

::: steps
1. Click a chart under **SoulSync Charts**.
2. SoulSync makes **your own copy** for your profile, with every match already in it, and opens it on the **Sync** page.
3. Nothing happens to your media server or wishlist until you press **Sync & download** (or turn on Auto-Sync for it).
4. When the chart updates, your copy updates in place and keeps its matches. If a chart leaves the network, your copy stays yours.
:::

> [!IMPORTANT]
> Charts are offers, never orders. SoulSync never syncs, wishlists or downloads anything from a chart on its own.
`
        },
        {
            id: 'ms-troubleshooting',
            title: 'Troubleshooting',
            lede: 'The common snags and what each message means.',
            body: `
## No charts showing

::: steps
1. Open **Automations** and run **Refresh MetaSync Charts**. Its result says what happened: \`stored: N\` means N charts arrived.
2. Charts appear only after all their songs are matched. A large first batch can take a few minutes.
3. Check your node's dashboard. "Charts offered to SoulSync" should list charts ready. If it doesn't, press **Refresh playlists now** there.
:::

## What the automation's messages mean

| Message | Meaning and fix |
|---|---|
| \`MetaSync is not connected\` (skipped) | No node set up in **Settings → Connections → MetaSync**. Normal if you don't use MetaSync. |
| \`MetaSync is unreachable at …\` | The node is off, or the URL changed. Start it, or reconnect. |
| \`MetaSync rejected the stored local key\` | The node was reset or re-paired elsewhere. **Reconnect MetaSync**. |
| \`This MetaSync is too old to offer charts\` | Update the MetaSync node. |
| \`MetaSync answered HTTP …\` | The node hit an error. Check its dashboard's recent errors. |

## The node says "waiting for approval"

Your pool key reached the network, but the node hasn't been approved yet. Approve it on the portal and check that the fingerprint matches the one on the node's dashboard.

## A report wasn't sent

- **Dry run is on.** Nothing is sent in Dry run. Turn it off on the node's dashboard.
- **It isn't Monday yet.** Each node reports the closed week at its own time early Monday (UTC). A failed send retries 6 hours later. **Run weekly report now** always works.
- **The preview says a signal was held back.** More than 500 events of one kind that week counts as a bulk operation and isn't reported. This is on purpose.

## A chart I opened disappeared from the shelf

Charts come and go with the week. Your own copy stays in your playlists until you delete it.
`
        },
    ],
});
