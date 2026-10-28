# Sign in with OIDC

SoulSync can hand sign-in to your own identity provider: Pocket ID, Authentik, Authelia, Keycloak, or anything else that speaks OpenID Connect. People sign in at the provider and come back signed in. SoulSync never sees their password.

This guide covers setting it up, how members get in, how long sessions last, OIDC only, and the things that usually go wrong. The settings themselves live in **Settings → Security → Sign in with OIDC**.

## How it works

SoulSync uses the authorization code flow with PKCE. A click on the sign-in button sends the browser to the provider. The provider sends it back to SoulSync's callback with a one-time code, and SoulSync swaps that code for the user's ID token, server to server.

A few rules shape everything below:

- **Accounts are matched by the provider's account ID** (the issuer plus the `sub` claim), never by email. People can usually change their email at the provider, and matching on it would let one person take over another's profile.
- **Nothing from the provider makes anyone an admin.** No group or claim does it. The admin profile gets OIDC only when the admin, already signed in, connects their own account to it.
- **One provider account per profile.** Sign-in has to know where it lands.

## Before you start

1. **Login mode.** The sign-in button lives on the sign-in screen, which only exists in login mode. In **Settings → Security → User accounts (login)**, save an admin password and turn on **Require login**. Keep that password: it's how you get back in if the provider is ever down.
2. **HTTPS.** The provider's issuer URL and its token endpoint have to be `https`. TLS is what vouches for the ID token, so SoulSync refuses plain `http`, except for `localhost`, `127.0.0.1` and `::1` when you're testing against a local provider.
3. **Behind a reverse proxy?** Turn on **Behind a reverse proxy** in the same Security page and restart SoulSync. SoulSync then reads the public address and the client's IP from the proxy's `X-Forwarded-*` headers. Without it, the callback address it sends to the provider is the internal one, and every user looks like they come from the proxy. See [Support/REVERSE-PROXY.md](../../Support/REVERSE-PROXY.md) for proxy examples.

## 1. Create a client at the provider

Create an OpenID Connect client (some providers call it an application or a relying party) with these settings:

| Setting | Value |
|---|---|
| Client type | Confidential, with a client secret. Not a public client. |
| Redirect / callback URL | `https://media.example.com/api/auth/oidc/callback` |
| Grant type | Authorization code |
| PKCE | On (S256), if the provider asks. SoulSync always sends it. |
| Token endpoint authentication | Client secret, sent either as HTTP basic auth or in the request body |

The callback is your SoulSync address followed by `/api/auth/oidc/callback`. If you serve SoulSync under a sub-path with `SOULSYNC_URL_BASE`, the sub-path goes in too: `https://example.com/soulsync/api/auth/oidc/callback`.

Note the client ID and secret, and the issuer URL. The issuer is the address the provider's discovery document lives under: SoulSync reads `<issuer>/.well-known/openid-configuration`.

## 2. Fill in SoulSync

Turn on **Sign in with OIDC** and fill in the block that opens:

| Field | What it does |
|---|---|
| Issuer URL | The provider's issuer, for example `https://id.example.com`. It has to match the `issuer` in the discovery document exactly, apart from a trailing slash. |
| Client ID, Client secret | From step 1. The secret is stored encrypted. |
| Button label | The text on the sign-in button, for example "Sign in with Pocket ID". |
| Icon URL | Optional. The provider's logo, shown in My Account. `https` only; anything else is dropped. |
| Required group | Optional. Only accounts in this group get in, at sign-in and again every time a session is renewed. Leave it empty to let every account at the provider in. |
| Groups claim | The claim that carries the account's groups. Defaults to `groups`. |
| Scopes | Defaults to `openid profile email`. Add whatever your provider needs to send groups (Pocket ID: `groups`), and `offline_access` on providers that only give refresh tokens when asked (Authentik, Authelia; see [Sessions](#how-long-a-session-lasts)). |
| Callback URL override | Optional. Pins the callback SoulSync sends to the provider. Only needed when your proxy doesn't pass the public address through. It must match what you registered in step 1, character for character. |
| Let new OIDC users sign up | On: an account that signs in for the first time gets a new profile. Off: only accounts already connected to a profile get in. |
| New OIDC profiles can download | Off means new profiles are request-only. What else they start with comes from **New sign-ups** further down the page. |

Save. The **Sign in with OIDC** button now shows on the sign-in screen.

## 3. Connect your admin account

Still in the OIDC block, click **Connect my admin account**. You go to the provider, sign in, and come back with your admin profile linked to that account. From then on the sign-in button lands you on the admin profile.

This is the only way the admin profile gets OIDC. Do it before you sign out, or you'll be signing in with your password to come back and do it.

## 4. Members

There are three ways a member ends up with OIDC:

- **They sign up through it.** With **Let new OIDC users sign up** on, a first sign-in makes a profile named after the account's `preferred_username` (then `name`, then the part of the email before the `@`). A name that's already taken gets a number.
- **They connect an existing profile.** A member signed in some other way opens **My Account → Sign-in → Connect**, signs in at the provider, and comes back connected.
- **An invite.** While sign-in is OIDC only, an invite sends the person to the provider instead of asking for a password, and the account they sign in with is linked to the new profile.

A member can **Disconnect** from the same My Account row, unless it's the only way that profile can sign in. A profile that OIDC sign-up made has no password, so disconnecting would lock its owner out; SoulSync refuses and says so. Set a password for them in **Manage Profiles** first if they really need to disconnect.

A profile that's turned off can't sign in with OIDC.

## How long a session lasts

A browser that signs in with OIDC stays signed in for as long as the provider says, and no longer:

- When the access token runs out (or the ID token, if the provider doesn't say how long the access token lasts), SoulSync renews the session in the background with the refresh token, server to server. The refresh token is stored encrypted on the server, never in the cookie.
- A renewal has to come back for the same account, and the account has to still be in the required group.
- **If the provider refuses, or can't be reached, the session ends** and the user signs in again. So revoking someone's access at the provider, or removing their account, reaches SoulSync at the next renewal.
- **If the provider never gave a refresh token, the session stays** until the user signs out. SoulSync logs `the provider gave no refresh token, so this session won't follow the provider` at sign-in when this happens. Authentik and Authelia only hand out refresh tokens when the scopes include `offline_access`. Pocket ID (checked on 2.17) gives one without it.
- Turning OIDC sign-in off ends every OIDC session. Disconnecting a profile from its account ends that profile's.

## OIDC only

Once everyone signs in with OIDC, you can make it the only way in: **OIDC only**, at the bottom of the OIDC block. Password sign-in, the recovery question, Sign in with Plex and switching profiles with a password stop working, and anyone signed in some other way is signed out. API keys keep working.

To turn it on, you have to be signed in with OIDC yourself, and every profile that isn't turned off needs an OIDC connection. The Settings row lists who's still missing. Passwords are kept, and work again if you turn it off.

**If the provider is down** and nobody can sign in, set `SOULSYNC_OIDC_ONLY_OVERRIDE=off` on the container and restart. OIDC only is off while it's set (the saved setting stays on), so you can sign in with the admin password and fix things. Remove the variable and restart to turn it back on. An amber shield in the bottom-right buttons reminds admins it's still set.

The in-app help has the rest: **Help & Docs → Multi-Profile → PIN, Password & Recovery**, under OIDC only.

## Example: Pocket ID

1. In Pocket ID, add an OIDC client named SoulSync. Callback URL `https://media.example.com/api/auth/oidc/callback`, **Public client** off, **PKCE** on. Copy the client ID and create a client secret.
2. Optional: restrict the client to the Pocket ID groups that should get in. Pocket ID then refuses everyone else before they ever reach SoulSync, so you can leave SoulSync's **Required group** empty.
3. In SoulSync: Issuer URL `https://id.example.com` (your Pocket ID address), the client ID and secret, and a button label such as "Sign in with Pocket ID".
4. If you'd rather check the group in SoulSync, set **Required group** to the group's name and add `groups` to the scopes: `openid profile email groups`. Pocket ID's `groups` claim carries group names.
5. Save, then **Connect my admin account**.

Pocket ID gives a refresh token without `offline_access`, so sessions follow it out of the box: when someone revokes SoulSync under their apps in Pocket ID, their SoulSync session ends at its next renewal. How often that happens is the access token lifetime on the client in Pocket ID.

## Gotchas

**The provider says the redirect URI doesn't match.** SoulSync builds the callback from the address the request came in on. Behind a proxy with **Behind a reverse proxy** off, or with a proxy that doesn't send `X-Forwarded-Host` and `X-Forwarded-Proto`, that's the internal address. Fix the proxy, or set **Callback URL override** to exactly what you registered at the provider.

**Under `SOULSYNC_URL_BASE`, register the callback with the sub-path.** The provider must have `https://example.com/soulsync/api/auth/oidc/callback`, not the root path.

**Everyone gets "Too many attempts" at once.** Starting a sign-in is limited to 8 tries per 5 minutes per IP address. With **Behind a reverse proxy** off, every user shares the proxy's address and the same limit. SoulSync trusts exactly one proxy hop; with two proxies in a row, the address it sees is the inner proxy's.

**The issuer has to be exact.** `https://id.example.com` and `https://id.example.com/application/o/soulsync/` are different issuers. Copy it from the `issuer` field of the provider's discovery document. A mismatch shows as "The sign-in provider's settings don't match its issuer url".

**Clocks.** SoulSync allows 60 seconds of difference between its clock and the provider's. Beyond that, fresh tokens look expired. Keep both hosts on NTP.

**Discovery is cached for an hour.** If you change the provider's endpoints without changing its issuer URL, restart SoulSync to pick them up straight away.

**The required group never matches.** SoulSync looks for the group in the ID token first, then at the provider's userinfo endpoint. If neither carries the claim you set as **Groups claim**, nobody gets in. Check which scope your provider needs to include groups, and whether it sends names or IDs.

**Signing in with OIDC from two profiles.** One provider account can connect to only one profile. Connecting it to a second says "That account is connected to another SoulSync profile". Disconnect it from the first one.

**The device list.** Each profile's list of signed-in devices keeps an entry for every sign-in until that device signs out, is revoked there, or the profile is signed out everywhere (a new password, a reset or turning the profile off does the same). A browser that was closed or had its cookies cleared never tells SoulSync, so it stays listed and the list can overstate who's actually signed in. The OIDC only row's device count reads the same entries, which is why it says how many were used lately rather than how many are signed in.

**Upgrading from 3.5.9 with OIDC already in use.** The first start on 3.6.0 or later signs out every profile connected to OIDC, once. Sessions from before then don't know when the provider expires them, so they start over.

## Troubleshooting

What the user sees, what usually causes it, and where to look. SoulSync's log lines for sign-in come from `security.oidc_signin` and `security.oidc_session`.

| Message | Usual cause |
|---|---|
| Couldn't reach the sign-in provider. Try again in a moment. | SoulSync can't fetch the discovery document or reach the token endpoint. Check DNS and the network from the SoulSync container, and the issuer URL. The log says which request failed. |
| The provider's issuer url (or token endpoint) must use https | That address is plain `http` on a host other than localhost. |
| The sign-in provider's settings don't match its issuer url | The issuer URL in SoulSync isn't exactly the `issuer` in the discovery document. |
| The sign-in provider refused the sign-in. Try again. | The provider rejected the code exchange. The log has the HTTP status and the provider's error code: `invalid_client` usually means a wrong client ID or secret, and `invalid_grant` a code that was already used or a callback mismatch. |
| The sign-in provider needs a client authentication SoulSync doesn't support | The client is set to something other than a client secret (a private key JWT, for example). Switch it to client secret, basic or post. |
| Sign-in expired. Try again. | The round trip took longer than 10 minutes, or the browser came back without SoulSync's session cookie. With **Behind a reverse proxy** on, the cookie is HTTPS-only, so plain-`http` access loses it. |
| The sign-in token wasn't meant for SoulSync | The client ID in SoulSync doesn't match the token's audience. |
| The sign-in token has expired. Try again. | The clocks are more than 60 seconds apart. |
| Your account isn't allowed to use SoulSync. Ask your admin. | The account isn't in the required group, or the groups claim never arrived. |
| Your account isn't linked to a SoulSync profile yet. Ask your admin. | Sign-up is off and this account isn't connected to a profile. |
| This profile is turned off. Ask your admin. | The profile this account is linked to has been turned off. |
| Too many attempts | 8 sign-in starts from one address in 5 minutes. See the proxy gotcha above. |

If the provider shows its own error page before ever sending the browser back, the problem is on the provider's side: usually the callback URL, or a client that isn't allowed for that user.
