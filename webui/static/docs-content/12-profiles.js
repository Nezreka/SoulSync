registerDocsSection({
    id: 'profiles',
    needs: 'admin',
    title: 'Multi-Profile',
    icon: '👥',
    pages: [
        {
            id: 'prof-overview',
            needs: 'everyone',
            title: 'How Profiles Work',
            lede: 'Everyone in the household gets their own profile, with their own lists and their own access.',
            body: `
Profiles give everyone their own space inside one SoulSync. Each profile has its own:

- Watchlist, wishlist and discovery queue
- Mirrored playlists
- Requests
- Home page and sidebar
- Queue and listening history
- Connected music accounts (see [Your Accounts](#docs/prof-accounts))
- What they can do: which sides and pages, whether they download or ask first, ratings, quality and limits

**Shared across all profiles**: settings, and the music library by default. A profile can have its **own library** instead, with its own output folder on Plex, Jellyfin or Navidrome 0.58+ (Navidrome's multiple libraries).

**Automations are per profile.** Admins run anything. A member's own automations are personal: playlist pulls, scans of their own watchlist, listening imports and notes to themselves. System jobs stay with admins. See [What each permission does](#docs/prof-permissions).

Admins manage everyone from **Settings > People**. See [People and access](#docs/prof-people).

![The profile picker: Admin, Mags and the PIN-protected Guest profile](profiles-picker.jpg)
`
        },
        {
            id: 'prof-people',
            title: 'People and Access',
            lede: 'One place for who uses SoulSync here and what each of them can do.',
            body: `
**Settings > People** is where an admin manages everyone. **Manage profiles** in the profile menu opens it too. It has five parts along the top:

- **People**: everyone, one row each
- **Global policy**: the most any member can have
- **Templates**: Adult, Teen, Kids, Guest and your own
- **Invites**: links that let someone join
- **Activity**: who changed what, and when

## How it fits together

What a person can do is worked out in three layers:

1. **Global policy**, the ceiling. Nothing below it can go above it.
2. **Template**. Every member follows one. Change the template and everyone following it changes with it.
3. **Personal changes**. Only the differences from their template are kept, so a template change still reaches them for everything they didn't change.

The result is what they can do. **Admins are never limited**: the policy, templates and changes don't apply to them.

**Sidebar & look** sits beside this and only decides what shows. It never lets anyone do more. See [Show only what you use](#docs/prof-features).

## The People list

A short sentence at the top says how many people there are, how many have changes of their own and how many requests are waiting for you. Filter by **Everyone**, **Admins**, **Has changes**, **Turned off** or **Needs a look**, or **Find someone** by name.

Each row shows the person's template (with how many changes they have), then Music, Movies, TV and Books: a dot for whether they get it right away, ask first or can't, with the quality and rating under it. The Requests column shows each limited kind and how many asks they've used this window ("Movies 1/5 · TV 3"). Click a row to open their editor (see [Editing one person](#docs/prof-editor)).

**Invite** and **Add person** sit above the list (see [Adding people](#docs/prof-manage)).
`
        },
        {
            id: 'prof-policy',
            title: 'Global Policy',
            lede: 'The ceiling: the most any member can have, whatever their template says.',
            body: `
**Settings > People > Global policy** sets the most any member can have. It uses the same categories and rows as the editor, without Sidebar & look. Each row is a ceiling:

- a switch can be on, or **Admins only**
- **Music requests**, **Movie requests**, **TV show requests**, **Audiobook requests** and **Podcast episode requests** can be held at **Require approval**, or at **Requests off** (for podcast episodes, **Follow only**). Require approval sends every one to your Requests, even for someone whose template says **Auto-approve**
- **Maximum content rating** caps the highest rating anyone gets
- **Maximum request limit** caps how many requests anyone makes, one ceiling each for music, movies and TV
- **Allowed quality profiles** limits the quality profiles anyone can ask for, and **Needs my OK** marks the ones that always come to you first. **4K always asks** covers every 4K profile, later ones too
- **Hide explicit music** can be forced on for everyone

A category with a ceiling set says **Limited here**. A template or person the policy holds lower shows the value in force, with a lock line that says what the policy keeps it at and, apart from that, what the template itself is set to.

## Saving

Before anything is saved, the save bar says who it reaches and what each of them loses, so a change never surprises you. **Save policy** applies it to every member right away.

A personal change above the ceiling isn't thrown away. The editor shows it with a lock line ("Global policy keeps this for admins. Change it there.") and it has no effect. Lift the ceiling later and it works again.

Admins are never limited by the policy.

When you're upgrading, the policy starts wide open, so nothing changes until you set it.
`
        },
        {
            id: 'prof-templates',
            title: 'Templates',
            lede: 'Adult, Teen, Kids, Guest and your own. Change one and everyone following it changes too.',
            body: `
**Settings > People > Templates** has a card for each template with who follows it. Pick one to edit its rows, the same rows as the editor minus anything personal (own library, home page). Rows the global policy caps show a lock and can't go higher.

The four built-ins:

- **Adult**: music, movies and TV, downloads right away
- **Teen**: everything, asks first, movies up to PG-13
- **Kids**: clean music, movies up to PG, asks first
- **Guest**: music only, asks first

Every built-in starts with every page on and no request limit. Edit them like any other.

**New template** starts from Adult, or **Duplicate** one you have. **Rename** and **Delete** are there too. A template people still follow can't be deleted until you pick where they go (**Move them to**); their personal changes go with them.

Saving a template shows who it reaches first, the same way the global policy does.

## New people start as

**New people start as** picks the template for profiles made by **Sign in with Plex** and **Sign in with OIDC**. Change it here, not in Security. The template it points at can't be deleted until you pick another.

When you upgrade, the old sign-up settings (the side new sign-ups start on, their request limit and the "can download" switches under Plex and OIDC) moved here once. If Plex or OIDC sign-in was making profiles, or one of those settings was saved, a **New sign-ups** template was made from them, so new people keep getting exactly what they got before. If neither, new people start as Guest. System > Security now just names this template and opens Templates.

## Sidebar & look

Each template carries its own **Sidebar & look**: **Start from** (Everything or Simple) and one switch per part, plus **Let them change it**. Everyone following the template sees that, unless you set something for one person in their editor. This replaces the old "What new profiles see".
`
        },
        {
            id: 'prof-editor',
            title: 'Editing One Person',
            lede: 'The editor: their template, their changes, and what that adds up to.',
            body: `
Click someone on the People list to open their editor. It's a wide sheet in three columns.

## The header

Their picture and name, and a line like "Member · Signs in with Plex · 2 changes from Teen". **Follows** switches their template: changes they had stay, and ones that now match the new template drop away. **Back to Teen** shows only when they have personal changes, and drops every one of them. A change that says the same as the template isn't counted as one.

## The rail

The categories, in this order: General & account, Movies, TV shows, Music, Audiobooks & podcasts, Requests & moderation, Administration, then Sidebar & look. A dot marks categories where they have changes. Each category has **Reset** for just that category.

## Every row says where it comes from

- **From Teen**: the template's value
- **Changed for Maya**: their own change, with the template's value next to it and **Reset** to go back
- a lock line: the global policy holds this down. Change it there.

A row that needs another one first says so ("Turn on Audiobooks first") rather than vanishing.

## What Maya can do

The card on the right sums it up in plain words, one line per category and a **Never** line for what's off. It's built from the real result, so it's what SoulSync will actually do.

## Admins

The **Admin** switch is in General & account. On, everything else folds away: admins can use everything, including settings and people. You can't change your own admin switch, and the main admin profile stays an admin.

Name, picture, PIN, password, devices, own library and **Turn off this profile** are in General & account too.
`
        },
        {
            id: 'prof-permissions',
            title: 'What Each Permission Does',
            lede: 'Every row in the editor, templates and global policy, by category.',
            body: `
The same rows show in the editor, on each template and in the global policy. Anything off is gone for the person: not in their sidebar, and SoulSync refuses it.

## General & account

- **Connect their own accounts**: Spotify, Tidal, ListenBrainz, Last.fm, their Plex user or Navidrome login
- **Own library**: their downloads go to their own folder and server library (set per person)
- **Chat & Arcade**
- **Their own automations**: playlist pulls, scans of their own watchlist, listening imports and notes to themselves. System jobs stay with admins. Their automations only run on their own events: their downloads finishing or failing, their wishlist and watchlist finds, their playlists syncing, their requests being approved, declined or arriving, their issues getting an answer. Someone else's events and install-wide ones (library scans, database updates, backups, maintenance) never reach them; an admin's automations hear everything. Their notifications only go to public internet addresses.

## Movies and TV shows

Movies and TV shows are separate, so a profile can have just one. A TV-only profile gets shows everywhere (search, discover, detail pages, the calendar, requests, Seerr apps) and never sees movies.

- **Movies** / **TV shows**: the side itself
- **Movie requests** / **TV show requests**: **Auto-approve** (it downloads right away), **Require approval** (it comes to you first) or **Requests off**. A season or a whole series is one request.
- **Maximum content rating**: the highest rating. Titles above it, or with no rating, stay out of sight. TV follows the movie rating unless you set its own (PG-13 allows up to TV-14).
- **Allowed quality profiles**, **Default quality profile**, **Needs my OK**: see [Request quality](#docs/prof-quality)
- **Movies & TV pages**: Dashboard, Search, Library, Discover, Calendar, Watchlist, Wishlist, Downloads and YouTube channels. They show under both Movies and TV shows.

## Music

- **Music side**
- **Use music apps**: listen from music apps paired in Settings > Devices. On for admins and members, off on the Kids template (see [Devices](#docs/devices))
- **Music requests**: **Auto-approve**, **Require approval** or **Requests off**. With Require approval, what they add becomes a request (see [Requests](#docs/prof-requests)).
- **Hide explicit music**: albums and tracks marked explicit are left out
- **Allowed quality profiles** and **Default quality profile**: the music quality profiles they can pick
- **Parts of music**: Dashboard, Search, Library, Discover, Playlist sync, Playlist Explorer, Import, Listening stats, Watchlist, Wishlist, Downloads, Music videos and Sample Studio

**Playlist sync** covers its auto-sync schedules too: a member with Playlist sync keeps their schedules even without Their own automations.

## Audiobooks & podcasts

- **Audiobooks** and **Audiobook requests**: **Auto-approve** (a book they want goes on their wishlist and downloads), **Require approval** (the heart says **Request**, and the book goes on their wishlist once you approve it) or **Requests off**
- **Podcasts** and **Podcast episode requests**: **Auto-approve** (new episodes of shows they follow download on their own), **Require approval** (they ask for episodes one at a time, and nothing downloads on its own) or **Follow only** (new episodes show up to play, nothing downloads). Following a show is always allowed.

These are separate from Music requests: someone can need approval for music and still get books right away. Book and episode requests land on the same Requests page as music.

## Requests & moderation

- **Request limit** (one under Music, Movies and TV shows each): asks per so many days. Downloads they get right away are never capped. Every ask counts from when it was made, so withdrawing one doesn't hand it back. Albums, tracks and music videos count toward the music limit, movies toward the movie limit, and shows, seasons, episodes and YouTube videos toward the TV limit (YouTube counts toward movies for someone without TV). The People list shows how many each person used of each.
- **See everyone's requests**: members see only their own unless this is on
- **Moderator**: approve, decline and manage other people's requests and issues. Never their own asks, and never settings, people or tools.
- **Cancel their own downloads** and **Report problems**: always on, for everyone

## Administration

- **Tools page** and **Movies & TV tools page**: show the page. Jobs that change the library still need an admin.
- Settings, people, tools jobs, automations for everyone and server activity: admins only, always.

What changes the whole install stays with admins whatever a member has: library details (names, years, the explicit flag), cover and artist art, the Tools page's scans and cleaners, the download blacklist, media server and service logins in Settings, and settings like the wishlist retry profile.

## Signed-in devices

Every browser or phone signed in as a profile is tracked. **Where you are signed in** in your own profile opens your device list; admins can open anyone's under **Devices** in their editor. Each device can be signed out on its own.
`
        },
        {
            id: 'prof-quality',
            title: 'Request Quality',
            lede: 'Which quality profiles each person can ask for, per movies and TV.',
            body: `
Quality is set per kind of title, in the Movies and TV shows categories:

- **Allowed quality profiles**: the quality profiles they may pick. Only profiles marked for that kind (or for both) are offered. Mark a profile **Used for** Movies, TV or Both under Settings > Quality.
- **Default quality profile**: what a request gets when they don't pick
- **Needs my OK**: profiles that always come to you first, even for someone who downloads right away. In the global policy, **4K always asks** covers every 4K profile, later ones too.

Music has **Allowed quality profiles** and **Default quality profile** as well.

## What they see when they ask

- They can't request that kind of title: no Request button for it at all.
- One quality allowed: no menu, just a line like "Comes in Movie 1080p".
- Two or more: a short list with their usual one picked and **Asks first** on the ones that need your OK.

The button says **Get it** when it will start downloading right away and **Ask for it** when it comes to you first. When a limit applies, the sheet shows how many asks are left.

## Everywhere else

Every way of getting something follows the same rules: the request sheet, a downloader's direct adds, manual search, watchlist and collection auto adds, airing auto-wishlist, import lists, automations and Seerr apps. A quality someone isn't allowed is refused when they pick it by hand, and falls back to their **Default quality profile** in the background. A show remembers its quality, so later episodes keep it.

You can change a request's quality when you approve it. Admins can pick anything.
`
        },
        {
            id: 'prof-members',
            needs: 'everyone',
            title: 'What Members See',
            lede: 'Things they can not use are gone, not greyed out.',
            body: `
A member sees only what they can use:

- no Request button on a title they can't ask for, no quality menu with one option, no Customize when it's locked
- a page they reach by link that isn't theirs shows a calm note instead of an error: "Audiobooks aren't part of your profile. Ask Boulder if you'd like them."
- request sheets say how many asks of that kind are left, like "3 of 5 movie asks left this week"
- in the music library, a member who asks first gets the Wishlist and no Automatic Search, Interactive Search, Enhance Quality or Upgrade: those download right away, so they are only there for a profile that may download (Interactive Search also needs a library of their own)
- the notification panel is their own notes, downloads and automations, never the server's health, numbers or jobs
- the dashboards (music and video) have no worker orbs and no Manage Workers: the enrichment workers are the admin's
- Help & Docs and the help sheet's search show only the guides for what they can use; setup, settings, tools, the API, troubleshooting and the diagnostics are the admin's

## What you can do

A member's own **Profile & account** has a **What you can do** card: the same plain summary the admin sees in the editor, so nobody has to guess.

## Seerr apps

Apps built for Seerr (Jellyseerr, Overseerr) act as the profile linked to the Jellyfin user they send, under that person's permissions: their ratings, their Movie requests and TV show requests, their quality and their movie and TV limits. They see only their own requests unless they can see everyone's.

A Jellyfin user with no profile is refused on new installs. Installs that had Seerr set up before keep acting as the profile they always did; Settings > System > Seerr Compatible API shows which one, with **Refuse Jellyfin users with no profile** to stop it.
`
        },
        {
            id: 'prof-manage',
            title: 'Adding People',
            lede: 'Add someone, send an invite, or let them sign in with Plex or OIDC.',
            body: `
## Add person

**Settings > People > Add person** asks who they are (name, picture, PIN, and a password when login mode is on), then which template they **Start from**. Fine-tune anything else in their editor afterwards.

## Invites

**Invite** makes a link like \`/invite/{token}\` for someone to join without you handing over admin access. Each invite picks a template, so whoever joins starts on it. Invites **run out after** 1 day, 3 days, 1 week or 30 days. The link is shown **once**: only its hash is stored, so copy it before closing. Invites read Open, Used, Revoked or Expired, and you can revoke one any time.

Accepting the link spends it and makes the profile on the invite's template. The person picks a name, an optional PIN, and a login password when login mode is on.

While sign-in is **OIDC only** there's no password: the invite page sends the person to your provider, and the account they sign in with there is linked to the new profile. The link is only spent when they come back signed in. An account that already has a profile here is turned away, and the provider's required group still applies.

## Sign in with Plex or OIDC

A new profile made by sign-in starts as the template set in **New people start as** under Templates.

## Turning a profile off

**Turn off** keeps everything of theirs, but nobody can open it until it's turned back on, and anyone using it is signed out everywhere right away. You can't turn off the owner (profile 1) or yourself.

## Activity

The **Activity** part records profile changes as plain sentences: who was added or deleted, permission, policy and template changes, PIN resets, sign-outs and invites. A device sign-out is only logged when an admin signs out someone else's device; recovery-question changes aren't logged.

## Sign out everywhere

On your own profile the menu says **Sign out other devices**; on someone else's (admin only) it says **Sign out everywhere**. Every other browser and phone signed in as that profile goes back to the profile picker.
`
        },
        {
            id: 'prof-upgrade',
            title: 'Upgrading to People',
            lede: 'Nobody can do more or less than before, and anything unclear is flagged.',
            body: `
Upgrading changes nothing about what anyone can do:

- The global policy starts wide open.
- Adult, Teen, Kids and Guest become real templates, each with the sidebar new profiles used to see.
- Every member is matched to the template their settings equal. Anything that differs (pages, a rating, a request limit) is kept as a personal change. The old one request limit becomes the music, movie and TV limits, each starting from it: music and video always counted on their own, so nobody's music allowance shrinks, and movies and TV each get what video had.
- What each person can do is checked against their old settings before it's saved. Admins skip all of it.

## Needs a look

A member that couldn't be matched exactly keeps their old settings and is marked **Needs a look** on the People list (the filter finds them all). Their editor says why, for example a rating SoulSync doesn't know or a page that no longer exists. Pick a template for them, or save any change, and they move onto it.

The old profile settings are still written as well, so an older SoulSync reading the same database sees the same access.
`
        },
        {
            id: 'prof-accounts',
            needs: 'account.connect',
            title: 'Your Accounts',
            lede: 'Each profile connects its own music services and media-server identity.',
            body: `
Service credentials are **not** shared: every profile connects its own accounts from the **My Account** modal in the sidebar. Connecting your own account only changes your playlists and stats. The admin's accounts in Settings are untouched.

![The My Account modal: your media server identity (none set up for Mags here) and your own Spotify, Tidal, ListenBrainz and Last.fm connections](profiles-accounts.jpg)

## Music services

- **Spotify**: connect your own Spotify via OAuth to sync your playlists
- **Tidal**: connect your own Tidal via OAuth
- **ListenBrainz**: paste your personal token (validated before saving); your stats use your own listening
- **Last.fm**: just your username; it's checked against real scrobbles

The admin account's services stay managed in Settings. Disconnecting a personal account falls back to the admin/server default.

## Media server identity

- **Plex**: link a **Plex Home user** from the server's user list (PIN asked if they have one); the profile's playlists are then written as that user. Unlink to go back to the app account.
- **Navidrome**: save your own Navidrome username and password (verified with one ping before saving); your playlists are written as you.
- **Library choice**: pick which Plex/Jellyfin library the profile reads from.
- **Own library on Navidrome**: a profile the admin gave its own library picks it under **My Account → Navidrome** after signing in with its own Navidrome user. The list is what that login can see (Navidrome scopes libraries per user), the shared library marked. Its downloads go to its own folder, the library scan reads only that library, and its playlists are written as that user. Setup: add the profile's folder as a second library in Navidrome (0.58 or newer), give their Navidrome user only that library, pick the shared library's music folder in Settings → Media server (so the shared scan leaves theirs out), then switch the profile to **Own library** in Settings > People. On an older Navidrome the switch stays off and says why; their downloads keep going to the shared folder.

## Your own listening

A profile's stats, Recently Played and recommendations read its own listening when it has a source of its own, and the shared history otherwise:

- a linked **Plex** account or a **Jellyfin user** of its own: the server's plays by that user are yours (a Jellyfin user read for the first time brings its recent history with it)
- your own **ListenBrainz** or **Last.fm**: your history from there

The card at the top of **My Account** says which one you are on.

## Who sees your listening

Your plays are yours. Two switches under **My Account → Your listening** (the admin has them too):

- **Share my listening**: off for everyone, including profiles that existed before this switch. A **Blend** on Discover (a mix of what you and someone else play) is only made when you both have it on. The first time you open Made For You, Discover asks once; **Not now** stops the asking and leaves it off. With several people sharing, you get blends with the three whose taste is closest to yours (most artists in common).
- **Private session**: while it's on, what you play in SoulSync's player isn't saved at all, so it never reaches your stats, mixes, blends or scrobbles. It turns itself off after 6 hours. Plays you make in a Plex, Jellyfin or Navidrome app are read from the server's own history, so those still count.

Profiles without a listening source of their own all play into the shared history, so that history only counts as shared when **everyone** on it has Share my listening on. Profiles that are turned off don't count. Install-wide shelves (the seasonal Rewind) read the shared history plus the own history of people who share; your mix recipes only ever read your own plays.

## Your sidebar weather

The weather line under your name shows your own town and its local time, never the admin's. Set it under **Profile & account → Sidebar weather**: type a town or ZIP and save. You can switch to °C and turn holiday decorations on or off there too. With no town set, you see no weather line. Admins set theirs in Settings → Appearance.

## Scrobbling your plays

Your own plays can scrobble to your own accounts, from the moment you turn it on (nothing older is sent):

- **ListenBrainz**: once your token is connected, flip **Scrobble your plays to ListenBrainz**
- **Last.fm**: **Sign in** under the Last.fm row. Writing scrobbles needs you to sign in to Last.fm once (reading them only needed your username). This needs the admin to have added a Last.fm API key and secret in Settings.

Your plays never go to the admin's accounts, and the admin's never go to yours.

When the web player starts a song, your accounts show it as **now playing**. A play is sent as soon as it counts as a scrobble (half the song or 4 minutes, whichever comes first) rather than in the next 5-minute batch; if a service is down, the batch tries again later.
`
        },
        {
            id: 'prof-security',
            needs: 'everyone',
            title: 'PIN, Password & Recovery',
            lede: 'Quick-switch PINs, login passwords, and the way back in when someone forgets.',
            body: `
A profile has two separate secrets, don't mix them up:

![The PIN entry dialog shown when opening a PIN-protected profile](profiles-security.jpg)

## PIN

A short (4 to 20 digits) code asked **when anyone opens the profile**, for privacy within the household. Admins can set or clear anyone's PIN from the profile editor. Clearing a PIN only turns off the launch lock for profile 1 (the admin profile). Clearing anyone else's PIN leaves the launch lock as it was.

## Login password

Used for **signing in when login mode is on**, separate from the PIN. Set it in your own profile editor or let an admin set it for you. Setting a password signs out every other browser on that profile; the one that changed it stays in.

## Who you can see

In login mode everyone signs in as themselves, so a member's profile menu has no **Switch to** list and SoulSync never tells them who else has a profile. Edit my profile, Profile & account and Sign out stay. Admins still see everyone and can switch with that profile's password.

Without login mode (the household picker), the picker shows every profile before anyone opens one, so the **Switch to** list stays for everyone. Turned-off profiles are left out.

## Recovery question

Any profile (or an admin for them) can set a **recovery question and answer** as a backup way in. The sign-in screen's forgot-password flow asks the question and, on a correct answer, lets the profile set a new login password.

## Forgot the admin PIN?

The owner can clear their PIN by proving they run the install: enter any configured service credential (for example the Spotify client secret or the Plex token). That only works for the main admin profile. A member's forgotten PIN is the admin's to reset from Settings > People.

## OIDC callback URL

Settings > Security > Sign in with OIDC shows the callback URL to register at your provider. It's the one SoulSync will actually send, so register it exactly. Behind a reverse proxy that serves https, SoulSync uses https when the proxy sends \`X-Forwarded-Proto\` (Nginx Proxy Manager, Traefik and Caddy do; plain nginx needs \`proxy_set_header X-Forwarded-Proto $scheme;\`). If the page warns that SoulSync sees plain http, turn on **Behind a reverse proxy** and restart, or set the **Callback URL override** to your https address.

## OIDC only

Settings > Security > Sign in with OIDC has an **OIDC only** switch. With it on, the provider is the only way in: password sign-in, the recovery question, Sign in with Plex and switching profiles with a password all stop working, and anyone signed in some other way is signed out. API keys keep working.

To turn it on you have to be signed in with OIDC yourself, and every profile that isn't turned off needs an OIDC connection (each person connects from My Account first). A profile can't be made by hand while it's on: send an invite, or let OIDC sign-up make it.

Passwords aren't deleted. They just aren't used, and work again when the switch is off. The admin's password can't be removed while login mode is on, because it's the way back in.

**If the provider is down:** set \`SOULSYNC_OIDC_ONLY_OVERRIDE=off\` on the container and restart. OIDC only is off while it's set (the saved setting stays on), so you can sign in with the admin password and fix things. An amber shield joins the buttons at the bottom right for admins as a reminder. Remove the variable and restart to turn OIDC only back on.

## Auth proxy header

Behind Authelia, Authentik or oauth2-proxy, Settings > Security > Reverse proxy > **Auth proxy user header** (for example \`Remote-User\`) lets someone the proxy already signed in skip the launch PIN.

SoulSync only believes that header when the request comes straight from an address in **Trusted proxy addresses**: your proxy's IP or a range, like \`172.18.0.2\` or \`172.18.0.0/16\` (one per line or comma separated). It checks the address that really connected, never \`X-Forwarded-For\`, so nobody can fake their way onto the list.

**The list starts empty, and an empty list means the header is ignored.** If you set the header before this list existed, people now get the PIN screen until you add the proxy's address; Help > Health shows a warning until you do. To find the address, check the proxy container's IP (\`docker inspect\`) or the Docker network's range.
`
        },
        {
            id: 'prof-requests',
            needs: 'everyone',
            title: 'Requests',
            lede: 'Members ask, admins and moderators answer, and everyone watches the request arrive.',
            body: `
Someone who **asks first** doesn't download: what they add becomes a **request**. The **Requests** page shows admins and moderators the queue, and shows members their own asks. A badge on the nav item counts what's waiting.

![The Requests page: status tabs for waiting, on the way, available and declined requests](profiles-requests.jpg)

## How asking works

Music requests are grouped the way a person thinks of them: an **album** or a single **track**. Audiobooks and podcast episodes wait beside them, one book or one episode each. Movies and shows ask through the request sheet (see [Request quality](#docs/prof-quality)). Asks land in **Waiting**, newest first, with the asks they have left.

Admins **approve** (or approve all), optionally with a reply, or **decline** with a reason. Approving lets the download start, still on the requester's own list so they watch it arrive. An approved book goes on the requester's audiobook wishlist and the wishlist search picks it up; an approved episode starts downloading. Declining takes it off and keeps a watchlist scan from filing it again. Members can **withdraw** their own waiting requests.

## Picking the release yourself

By default an approval downloads the best match on its own. **Approval settings**, at the top of the Requests page (admins), sets it for **Music**, **Movies**, **TV** and **Audiobooks** separately: **Download automatically** or **I pick the release**. Nothing changes until you switch one.

With **I pick the release**, an approved request waits on the **Pick a release** tab. Nothing automatic touches it: not the wishlist runs, not retries, not scheduled searches. The tab lists who asked, what kind it is and when you approved it, and **Pick release** opens the same picker you'd use anywhere else: Interactive Search for a track (one per track of an album), the release list for a book, manual search for a movie, an episode or a season of a show. Pick one and it downloads, imports and lands for whoever asked like any other request. If a pick fails, the request stays on the tab to pick again. **Download automatically instead**, in its menu, hands it back.

The **Approve** button follows the setting. Its menu has the other way for that one request: **Approve & pick release** or **Approve & auto-download**. Approve all always follows the settings. A held show doesn't follow new episodes on its own; hand it back, or follow it yourself, if you want that.

Moderators pick the same way, for anyone's request but their own. Music videos, YouTube videos and podcast episodes have one file each, so they always download right away.

## Moderators

A **Moderator** (Requests & moderation in the editor) approves, declines and tidies other people's requests and manages their issues, the same way an admin does. They can't answer their own asks, and they never get settings, people or tools. Approve all, for a moderator, means everyone's but their own.

**See everyone's requests** lets a member read everyone's requests without answering them.

## Watching progress

Each request shows where it stands:

- **Waiting**: with an admin or a moderator
- **Approved, waiting for a release to be picked**: approved, an admin chooses the exact release next
- **On the way**: approved, downloading
- **In your library**: arrived and matched
- **Declined**: someone said no (the reason shows)
- **Removed before it arrived**: it left the wishlist without matching the library

Admins and moderators hear about new asks, and the requester hears when theirs is approved and when it lands.
`
        },
        {
            id: 'prof-home',
            needs: 'everyone',
            title: 'Home Pages',
            lede: 'Each profile lands on the home that suits them.',
            body: `
Every profile can choose its own home page:

- **Dashboard**: the admin default: system stats, recent activity, quick actions
- **Discover**: the non-admin default: new releases, recommendations, trending

Anyone can change their own home page; admins can set or change anyone's. A home page has to be a page they can use. One they lose access to stops counting, and they land as below. Admins set it as **Opens on** under Sidebar & look in the person's editor.

With no home page (or one they can't open), a profile lands on Discover, or the first page it has if Discover is off or hidden. The same goes for the video side, and for any page a profile opens that it isn't allowed on.

![A member profile's home: Mags' dashboard with her week in listening, playlist sync and recently played](profiles-home.jpg)
`
        },
        {
            id: 'prof-features',
            needs: 'everyone',
            title: 'Show Only What You Use',
            lede: 'Every profile hides the parts of SoulSync it does not need.',
            body: `
SoulSync does a lot. **Customize**, at the bottom of the sidebar, opens **Make SoulSync yours** (admins also find it under Settings > Appearance).

![Make SoulSync yours: the Everything and Simple presets, the parts that are always here, and a switch for every optional part. All 13 extras are on for Admin here](prof-features.jpg)

- **Simple** keeps the music core plus Discover, playlist sync and Import (where downloads that need a hand wait).
- **Everything** is SoulSync the way it ships.
- Or switch single parts: Discover, Playlist sync, Playlist Explorer, Import, Listening stats, Music videos, Sample Studio, Podcasts, Audiobooks, Movies & TV (or just Video Discover and Calendar inside it), Chat & Arcade, Automations and Tools.

Each switch saves right away. What's off leaves the sidebar, its dashboard card and the tabs that lead to it (the Videos tab in search, the Podcasts and Audiobooks tabs on the watchlist and wishlist). Turning off Movies & TV also hides the music/video switch.

Search, Library, Watchlist, Wishlist, Downloads and Settings are always there, on the music side and the video side.

This is a preference, not access. Hidden parts keep running (scans, automations, new episodes), and a link to a hidden page still opens, with a note at the top and a **Show it** button to put it back. To stop someone from using a part at all, turn it off in their permissions (see [What each permission does](#docs/prof-permissions)). Customize only offers parts a person can use.

## Setting it for others

Each template has a **Sidebar & look**: **Start from** (Everything or Simple), one switch per part, and **Let them change it**. Everyone following the template sees that. Change the template's and it reaches all of them.

To set it for one person, open their editor and go to **Sidebar & look**. The editor says whether a row comes from their template or was set for them. **Opens on** picks their home page there too.

With **Let them change it** on, they can change what shows in **Customize**, and their own pick is what they see. Off, they see exactly what you set: their old pick is dropped, **Customize** goes away for them, and SoulSync refuses their changes. Admins always pick their own.

A profile that can only use the video side can't hide it, since it would have nothing left.

Once **every** profile has Podcasts or Audiobooks hidden, an admin sees **Also pause its background jobs** under it. Paused, its scheduled checks and downloads stop. If anyone turns the part back on, they start again by themselves. **Run now** on the Automations page always works. The video side's jobs have their own switch on the video Automations page.

First-run setup asks what SoulSync is for and starts you off with the rest hidden. A music-only install, for example, starts without podcasts, audiobooks and the video side. Profiles that existed before see a one-time card above **Customize** until they pick something (**Keep everything** counts).
`
        },
        {
            id: 'devices',
            title: 'Devices: Listen Anywhere',
            lede: 'Pair a phone, a car or a speaker app with a one-time code. Each device picks a profile and can only play.',
            body: `
**Settings > Devices** is where an admin pairs music apps with this server. It is for admins only.

## Add a device

1. Click **Add a device**. SoulSync shows a code like \`7KQ4-M2XD-9FHA-T3VB\` and a QR with the same thing in it.
2. Type the code into the app, or scan the square. The code works **once**, for **10 minutes**. At most 20 codes wait at a time; cancel one you don't need under **Codes waiting to be used**.
3. The app shows this server's **fingerprint** (12 characters). Check it matches the one under **This server** (and under Settings > System > Security). That is how you know it found this server and not another.
4. The app asks which profile it is for. A profile with a PIN asks for it, and in login mode the profile's password is asked for. Wrong tries are slowed down the same way as signing in.

A device that hasn't picked its profile yet shows as **No profile picked yet** with the time it has left. If nobody picks one within 15 minutes, it is dropped and needs a new code.

The code is checked on this server, never stored as itself, and pairing only works over https or from your home network.

## What a paired device can do

It acts as the profile it picked, **only to listen**: browse the library, play, lyrics, artwork, scrobbles and play history, favorites, playlists and the queue. Never settings, downloads, people, API keys or anything for admins, even when the profile is an admin's. Every part of SoulSync a device can reach is on a short list; everything else turns it away.

**Use music apps** is a permission (see [What each permission does](#docs/prof-permissions)). It is on for admins and members and off on the **Kids** template. A profile without it doesn't appear in the app's list, and turning it off stops a device that already picked that profile.

## Unpairing

- **Unpair** next to a device stops it at once.
- **Sign out everywhere** on a profile, a new password, or turning the profile off unpairs that profile's devices too. Deleting a profile removes its devices.

## Invite a person

**Invite a person** at the bottom is the same list as **Settings > People > Invites**: a link someone opens to make their own profile, following the template you pick. It works once and runs out after the time you choose (a week unless you change it). They then sign in like anyone else and pair their own devices. Person invites never use device codes.
`
        },
        {
            id: 'devices-remote',
            title: 'Reaching SoulSync From Outside',
            lede: 'The addresses a device tries, and how to check the outside world can get in.',
            body: `
A paired device is given every address this server answers on, home ones first:

- **Local**: found on their own. The address your browser is using, when it is a home one, and this machine's home-network addresses.
- **Remote**: one address for outside your home. Type it under **This server** in Settings > Devices.
- **Extra**: any others, one per line (a Tailscale name, say).

## Ways in from outside

SoulSync doesn't open anything on your router by itself. Pick one:

- **Forward a port** on your router to this server, and put https in front of it (a reverse proxy with a certificate).
- **Cloudflare Tunnel**: runs next to SoulSync, gives it an https address, no ports opened. Use the tunnel's address as the remote address.
- **Tailscale**: your phone and this server on one private network. Add the server's Tailscale name as an extra address. Every device that listens needs Tailscale too.
- **Your own domain** with a reverse proxy (Caddy, nginx, Traefik) and a certificate.

> [!WARNING]
> A remote address that starts with **http://** gets a loud warning. Over plain http, passwords, tokens and everything you play cross the internet readable by anyone on the way. Use https.

## Check reachability

**Check reachability** makes a one-time link on this server and fetches it through the remote address. An answer that carries the link's secret back proves the address leads to this very server. It runs from the server itself, so it can't prove the wider internet gets in: some routers loop their own public address back inside and some don't.

So it also shows the same link as a QR. Turn wifi off on your phone and open it. The page then says **Reached from** the phone's address, and whether that was outside your network. The link works for 10 minutes.
`
        },
        {
            id: 'music-apps',
            title: 'Music Apps',
            lede: 'Play your library in a music app that speaks the Subsonic or OpenSubsonic API, as one profile.',
            body: `
Many music apps can play from a server that speaks the **Subsonic** or **OpenSubsonic** API. SoulSync speaks it at \`/rest\`, so those apps play straight from SoulSync. No app is required; use whichever you like.

## Set one up

1. Go to **Settings > Devices > Set up a music app** (admins only).
2. Pick **whose music** it plays. Only profiles with **Use music apps** are listed (off on the Kids template; an admin can turn it on per profile).
3. Give it a name (Phone, Car) and click **Set it up**.
4. SoulSync shows the **server** address, the **username** (the profile's name), an **app password** and an **API key**, plus a QR with all of it. They are shown **once**. Copy them into the app now.

How to add it in a few apps:

- **Symfonium, Amperfy, play:Sub, Substreamer, DSub, Tempo**: add a server of the Subsonic kind, with the server, username and password.
- **Feishin**: add a server, pick OpenSubsonic, then the server, username and password.
- **Music Assistant**: add the Subsonic music provider with the server, username and password.
- **Apps that take an API key**: paste the API key and leave the username and password empty.

Away from home, use your remote address (see [Reaching SoulSync From Outside](#docs/devices-remote)).

The app password is not the profile's password and only works for that one app. The profile's real password never works in a music app, and neither does an API key from Settings. Too many wrong passwords from one address are held off for a minute.

The app shows up under **Paired devices**. **Unpair** it and it stops at once; set it up again for a new password.

## What works

- Browsing by artist, album, genre and folder, album lists (newest, A to Z, by artist, by year, by genre, random, most played, recently played, starred, top rated) and search, including apps that copy the whole library to the phone.
- Playing and downloading for offline. When an app asks for a lower bitrate, songs are converted on the fly to **MP3** or **Opus** (pick which under **Convert to**; needs ffmpeg). Seeking works.
- Cover art, lyrics (the file's own, a .lrc next to it, then LRCLib; synced lyrics stay synced), artist bios, similar songs and top songs.
- **Starring** a song adds it to the profile's **Liked Songs**. Albums and artists can be starred and rated too. Starring a song the profile doesn't have adds it to the wishlist for a profile that may download, or makes a request for one that may only ask.
- **Playlists**: the profile's own playlists can be made and edited in the app. Mirrored playlists and the Daily Mixes show up too, read-only.
- **Plays** are recorded in SoulSync for that profile, the same as the web player: they feed Listening stats and mixes, and are scrobbled when the profile scrobbles. A **private session** keeps them out. What is playing now goes to Last.fm and ListenBrainz too.
- The play queue is kept per app, so you can pick up where you left off.

Each app only ever sees its own profile: that profile's library (its own library when it keeps one), its playlists, stars, ratings, queue and plays. A profile that hides explicit music doesn't see it in the app either. An app can only play: never settings, downloads, people or anything for admins.

## Files SoulSync can't read

SoulSync plays the files it can open. If your media server has music on drives SoulSync can't reach, those songs answer **SoulSync can't read this file** in the app. The best fix is a folder override (**Settings > Library > Folders**). Otherwise, under **Set up a music app**, turn on **Stream files SoulSync can't read through** your media server (Plex, Jellyfin or Navidrome; off by default, music only). SoulSync then plays those songs from the media server, marks them in the app (\`soulsyncStreamVia\`), and still records every play itself.
`
        },
    ]
});
