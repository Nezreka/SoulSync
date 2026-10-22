**SoulSync 3.6.0 is out** :musical_note:

You can now sign in with any OpenID Connect provider, like Pocket ID, Authentik, Authelia or Keycloak. Members link their own accounts, an optional required group keeps everyone else out, and new accounts start with your new sign-up defaults. Turn it on in Settings > Security (thanks @PapaBearDoes).

Anime is now picked by library instead of guessed from a genre tag, so shows like Star Wars: Visions stop landing in the anime folder. Mark any TV library as anime, scan more than one TV library, and set the type by hand for shows you don't own yet (thanks @PapaBearDoes).

Prowlarr outages are handled better. Search names the indexers that failed next to the results (thanks @glebosotov), and a search that didn't complete no longer counts as a failed attempt on the wishlist or shows "No releases found" for audiobooks (thanks @SimpleSimonLA).

Audiobook fixes: a wrong book far longer than the one you wanted fails and moves to the next release instead of sitting in staging, and artists set to treat as audiobook stop showing every release as missing. Batch complete notifications also name the track for wishlist singles (thanks @SimpleSimonLA).

Under the hood: more of the CSS token system filled in (thanks @Thundernerd).

Full notes on GitHub.
