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

**Shared across all profiles**: the music library itself, settings, service credentials, and automations. Profiles are about personalization and permissions, not separate servers.

![Profile picker](profiles-picker.jpg)
`
        },
        {
            id: 'prof-manage',
            title: 'Creating & Managing Profiles',
            lede: 'Set up profiles with avatars, PINs, and per-profile controls.',
            body: `
Admins create profiles from the Profiles page. Each profile can have an avatar and an optional **PIN** for privacy within the household.

![Create profile](profiles-create.jpg)

## Invites

Admins can generate **invite links** — a token-based URL like \`/invite/{token}\` — and share it with family members. Invites expire after a configurable number of hours and can be revoked at any time. This is the easiest way to onboard someone without handing over admin access.

## Audit log

Every profile admin action — creation, deletion, permission changes, PIN resets — is recorded in an **audit log** showing who did what and when. Useful for shared households where more than one person has admin rights.

## Sign out everywhere

If a device is lost or someone's access needs to be cut immediately, admins can **sign out a profile everywhere** — every browser session for that profile is invalidated at once.
`
        },
        {
            id: 'prof-permissions',
            title: 'Permissions & Limits',
            lede: 'Control what each profile can see, download, and request.',
            body: `
![Profile permissions](profiles-permissions.jpg)

## Page access

Admins choose exactly which pages each profile can visit (**allowed pages**). A kid's profile might only see Discover and the media player; a guest might only see the request page.

## Download permission

The **can download** toggle controls whether a profile can trigger downloads directly. Profiles without it can still request music for an admin to approve.

## Request quotas

Set a **request limit** per profile — e.g. 5 requests per 7 days. Once the quota is used up, new requests are refused until the window resets. Admins are never limited. Quotas keep one enthusiastic family member from flooding the request queue.

## Kids limits

- **Hide explicit** — explicit music stays out of sight for the profile
- **Max rating** — cap the highest movie/TV rating visible (on the video side)

## Admin areas

The Enhanced Library Manager, Settings, and Automations pages are admin-only regardless of page permissions.
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

Admins can set or change any profile's home page. The home page is the first thing a profile sees after signing in, so pick the one that matches how that person uses SoulSync.
`
        },
    ]
});
