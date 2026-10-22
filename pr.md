# soulsync 3.6.0: `dev` → `main`

you can sign in with any openid connect provider now, anime is picked by library instead of guessed from a genre tag, search names the prowlarr indexers that failed, and a prowlarr outage stops getting counted as "nothing found". scope: everything on dev since 3.5.9.

## sign in with oidc

- any openid connect provider works: pocket id, authentik, authelia, keycloak. give soulsync the issuer url, a client id and secret, and it reads the rest from the provider's discovery document. authorization code flow with pkce, no new dependency (thanks @PapaBearDoes) (#1834).
- identity is the provider's issuer plus the account's sub, never an email, and claims never make an admin. the admin connects their own account from the last row of the oidc settings, members connect or disconnect theirs from my account, and a disconnect that would leave a profile with no way in is refused.
- an optional required group keeps everyone else out. an account already linked signs in as its profile, anyone else gets a new profile if sign-ups are allowed, starting with what settings > security > new sign-ups sets, same as plex sign-up.
- settings > security has the toggle and one block for the provider: issuer, client, button label, an optional icon for the sign-in card, required group and callback override. the secret is encrypted at rest.

## anime by library

- 3.5.9's anime folder followed a show's series type, and that fell back to the anime genre tag. tmdb tags shows like star wars: visions as anime that most people file with tv, so their episodes went to the wrong folder. the genre now only steers search (thanks @PapaBearDoes) (#1835).
- where an episode is filed comes from, in order: a type set by hand, a tv library the admin marked as anime, or, for a show in no library yet, a stored hint.
- the tv pick can be several libraries now, the main one plus other tv libraries ticked to scan, and any of them can be ticked anime. plex and jellyfin scan every picked library and the post-download refresh covers all of them.
- a show that's followed, wished or approved before it's in any library gets one background tmdb lookup for a suggested type. an admin can change it from the show page's type chip or the approve sheet, and a tmdb suggestion never replaces an admin's choice.

## prowlarr outages

- search names the indexers that failed. results from the indexers that answered already came through, but the failures only reached app.log. the search page shows a note over the results naming each failed indexer, with its error under details (thanks @glebosotov) (#1831).
- a prowlarr search that didn't complete stops counting as a failed attempt. while an indexer is down prowlarr answers every search with a 400, and the wishlist stamped that against every track of the album, pushing it into retry backoff for an outage. those tracks just get tried again next cycle (thanks @SimpleSimonLA) (#1826).
- audiobooks: when every prowlarr query errors, the wishlist row shows the real error instead of "no releases found" for a book nobody actually looked for (thanks @SimpleSimonLA) (#1832).

## audiobooks

- a wrong book far longer than the wanted one fails instead of staging. a download past 1.6x the book's runtime sat on grabbed for the whole staging window, but a long download never gets shorter. it fails now, the release is blocked for that book and the next release is tried. short books still stage (thanks @SimpleSimonLA) (#1833).
- an artist set to treat as audiobook stops showing every release as missing on the artist page. ownership counts completed downloads whose files still exist and the audiobook library's own entry for the book (thanks @SimpleSimonLA) (#1825).

## automations

- batch complete names the track for the wishlist's catch-all batches. singles and leftover tracks carry no artist or album, so the notification could only say "wishlist (auto - albums)". it falls back to the tracks that finished, and artist, title and album are listed as trigger variables (thanks @SimpleSimonLA) (#1823).

## under the hood

- more of the css token system filled in: exact literals swapped for tokens across style.css, chat and the react modules, and repeated selectors merged where the cascade allows, with zero winners changed (thanks @Thundernerd) (#1827, #1828, #1829, #1830).
- a flaky musicbrainz pacing test stopped counting sleeps from unrelated threads.

## validation

- every fix shipped with regression tests, and the indexer warnings and the long-book gate were negative-checked against the old code. oidc and the anime libraries are not live-tested against a real provider or a real multi-library server on this side yet.
