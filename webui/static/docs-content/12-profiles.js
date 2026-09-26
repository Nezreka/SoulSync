registerDocsSection({
    id: 'profiles',
    title: 'Multi-Profile',
    icon: '👥',
    pages: [
        {
            id: 'prof-overview',
            title: 'How Profiles Work',
            lede: 'Separate profiles for everyone in the household — each with their own watchlists, wishlists, and permissions.',
            body: `
Profiles give everyone their own space inside a shared SoulSync installation. Each profile has its own:

- Watchlist, wishlist, and discovery queue
- Mirrored playlists
- Request history
- Home page layout
- Queue and listening state
- Side access and allowed pages
- Connected music accounts (see [Your Accounts](#docs/prof-accounts))

**Shared across all profiles**: settings, and the music library by default — though a profile can have its **own library** instead (\`library_mode == 'own'\`), with its own output folder on Plex or Jellyfin. **Automations are per-profile**: each profile runs its own automations; only system automations are shared across profiles. Profiles are about personalization and permissions, not separate servers — and access to the Automations page is a grantable permission, not admin-only.

![Profile picker](profiles-picker.jpg)
`
        },
        {
            id: 'prof-manage',
            title: 'Creating & Managing Profiles',
            lede: 'Set up profiles with avatars, PINs, and per-profile controls.',
            body: `
Admins manage profiles from the Profiles panel, which has three tabs: **People**, **Invites**, and **Activity** (the audit log).

![Create profile](profiles-create.jpg)

## Invites

Admins can generate **invite links** — a token-based URL like \`/invite/{token}\` — and share it with family members. This is the easiest way to onboard someone without handing over admin access.

Each invite carries a **preset**: Adult (music & movies, downloads), Teen (movies up to PG-13, asks first), Kids (clean music, movies up to PG, asks first), or Guest (music only, asks first). A **More** section adds a custom page list and a request limit.

Invites **run out after** a chosen time — 1 day, 3 days, 1 week, or 30 days. The link is shown **once**: only its hash is stored, so copy it before closing the dialog. Invite states are Open, Used, Revoked, or Expired, and an admin can revoke a link at any time.

Accepting the link spends it (one use), then creates the profile with the invite's pages, download rights, side access, kids limits, and request quota. The person picks a name, an optional PIN, and a login password when login mode is on.

## Turning a profile off

Admins can **Turn off** a profile without deleting it. Everything of theirs is kept, but nobody can open it until it's turned back on — anyone using it right now is signed out everywhere immediately. You can't turn off the owner (profile 1) or yourself.

## Audit log

The **Activity** tab records profile admin actions — creation, deletion, permission changes, PIN resets, sign-out-everywhere, invites — as a plain sentence showing who did what and when. Individual device sign-outs are only logged when an admin signs out a device on someone else's profile, not their own; recovery-question changes aren't logged. Useful for shared households where more than one person has admin rights.

## Sign out everywhere

On your own profile the menu says **Sign out other devices**; on someone else's (admin only) it says **Sign out everywhere**. In both cases every other browser and phone signed in as that profile goes back to the profile picker. The browser that clicked stays signed in when signing out your own devices.
`
        },
        {
            id: 'prof-permissions',
            title: 'Permissions & Limits',
            lede: 'Control what each profile can see, download, and request.',
            body: `
![Profile permissions](profiles-permissions.jpg)

## Side access

Admins choose whether each profile sees **Music only**, **Movies & TV only**, or **Music & movies** (both). Admins always get both sides, and non-admins default to music. When a profile doesn't have both sides, the header side switcher is hidden for them.

## Page access

Admins choose exactly which pages each profile can visit (**allowed pages**). A kid's profile might only see Discover and the media player; a guest might only see the request page. Unset means all pages.

A few pages follow their own rules regardless of the list: **Help** and **Issues** are always visible, **Settings** is admin-only (it's stripped from non-admin grants even if picked), and the **Requests** page shows for admins and for profiles without download rights. **Automations** is a grantable page — non-admin profiles can get full access to it.

## Download permission

The **can download** toggle controls whether a profile can trigger downloads directly. Profiles without it can still add to their wishlist — those rows become requests for an admin to approve (see [Requests](#docs/prof-requests)).

## Request quotas

Set a **request limit** per profile — e.g. 5 requests per 7 days. The request page shows how many are left; once the quota is used up, new asks are refused until the window resets. Admins are never limited. Quotas keep one enthusiastic family member from flooding the request queue.

## Kids limits

- **Hide explicit** — explicit tracks and albums stay out of sight on the music side and can't play
- **Max rating** — caps the highest movie/TV rating visible on the video side (G, PG, PG-13, R; unset means no cap)

## Signed-in devices

Every browser or phone signed in as a profile is tracked. From your own profile panel, **Where you are signed in** opens your device list; admins can open anyone's. Each device can be signed out individually — including this one — which sends it back to the profile picker the next time it does anything.
`
        },
        {
            id: 'prof-accounts',
            title: 'Your Accounts',
            lede: 'Each profile connects its own music services and media-server identity.',
            body: `
Service credentials are **not** shared: every profile connects its own accounts from the **My Account** modal in the sidebar. Connecting your own account only changes your playlists and stats — the admin's accounts in Settings are untouched.

## Music services

- **Spotify** — connect your own Spotify via OAuth to sync your playlists
- **Tidal** — connect your own Tidal via OAuth
- **ListenBrainz** — paste your personal token (validated before saving); your stats use your own listening
- **Last.fm** — just your username; it's checked against real scrobbles

The admin account's services stay managed in Settings. Disconnecting a personal account falls back to the admin/server default.

## Media server identity

- **Plex** — link a **Plex Home user** from the server's user list (PIN asked if they have one); the profile's playlists are then written as that user. Unlink to go back to the app account.
- **Navidrome** — save your own Navidrome username and password (verified with one ping before saving); your playlists are written as you.
- **Library choice** — pick which Plex/Jellyfin/Navidrome library the profile reads from.
`
        },
        {
            id: 'prof-security',
            title: 'PIN, Password & Recovery',
            lede: 'Quick-switch PINs, login passwords, and the way back in when someone forgets.',
            body: `
A profile has two separate secrets — don't mix them up:

## PIN

A short (4–20 digits) code asked **when anyone opens the profile**, for privacy within the household. Admins can set or clear anyone's PIN from the profile editor. Clearing a PIN only turns off the launch lock for profile 1 (the admin profile) — clearing anyone else's PIN leaves the launch lock as it was.

## Login password

Used for **signing in when login mode is on** — separate from the PIN. Set it in your own profile editor or let an admin set it for you. Setting a password signs out every other browser on that profile; the one that changed it stays in.

## Recovery question

Any profile (or an admin for them) can set a **recovery question and answer** as a backup way in. The sign-in screen's forgot-password flow asks the question and, on a correct answer, lets the profile set a new login password.

## Forgot the admin PIN?

The owner can clear their PIN by proving they run the install: enter any configured service credential (for example the Spotify client secret or the Plex token). That only works for the main admin profile — a member's forgotten PIN is the admin's to reset from Manage Profiles.
`
        },
        {
            id: 'prof-requests',
            title: 'Requests',
            lede: 'Members ask, admins approve, and everyone watches the request arrive.',
            body: `
A profile without download rights doesn't download — everything it adds to its wishlist becomes a **request** for an admin. The **Requests** page is visible to admins (to work the queue) and to members who ask (to follow their own), and a badge on the nav item counts what's waiting.

## How asking works

Requests are grouped the way a person thinks of them: an **album** or a single **track**. Asks land in the **Waiting** tab, newest first, with the profile's remaining request quota shown.

Admins **approve** (or approve all with one click), optionally with a reply, or **decline** with a reason. Approving flips the rows so the scheduled run downloads them — still on the requester's own wishlist, so they watch it arrive. Declining takes the rows off and ignore-lists them so a watchlist scan doesn't file them again. Members can **withdraw** their own waiting requests.

## Watching progress

Each request shows where it stands:

- **Waiting** — sitting with an admin
- **On the way** — approved, downloading
- **In your library** — arrived and matched to the library
- **Declined** — an admin said no (the reason shows)
- **Removed before it arrived** — the rows left the wishlist without matching the library

The page has tabs for each status. Admins get notified of new asks, and the requester gets told when their ask is approved and when it lands. Members can mark updates as seen from the badge.
`
        },
        {
            id: 'prof-home',
            title: 'Home Pages',
            lede: 'Each profile lands on the home that suits them.',
            body: `
Every profile can choose its own home page:

- **Dashboard** — the admin default: system stats, recent activity, quick actions
- **Discover** — the non-admin default: new releases, recommendations, trending

Anyone can change their own home page; admins can set or change anyone's. A home page has to be in the profile's allowed pages — picking a new allowed-pages list resets a home page that was locked out.
`
        },
    ]
});
