**SoulSync 3.5.9 is out** :musical_note:

SoulSync can now answer as Lidarr, so Singarr and MusicSeerr 1.x can request albums from it (thanks @Topaze77 for the request). There's also an MCP endpoint so an AI client can drive SoulSync. Both are off until you turn them on in Settings > Advanced.

First-run setup is rebuilt as a guided flow that finds your servers on the network, checks every part before it finishes, and can pick up where you left off.

You can now choose what new Plex sign-ups start with (music, movies and TV, or both, plus a request limit), members can open the video Requests page, and anime can have its own library folder (thanks @PapaBearDoes).

The Downloads page is rebuilt for music and video: status on the art, one action per row, and a detail sheet that explains what happened. The Wishlist groups artists by what's stuck with a side panel that says why, and the video wishlist now lives on the same page.

Fixes: AcoustID judges files by their real length and the duration tolerance setting is easier to find (thanks @QT3496), YouTube Music tracks match on their real title and artist (thanks @andreamassaro9), and the GPU stops pegging during downloads (thanks @OlivierMartinez). Review deletes blacklist the source and append drops old wrong matches (thanks @kevin2xk), redownloads stop leaving duplicate rows (thanks @glebosotov), and collections stop wishlisting movies nobody asked for (thanks @hilburn). Plus audiobook matching fixes (thanks @SimpleSimonLA), dead file findings that close (thanks @splitsec2) and empty usenet folders cleaned up.

Under the hood: the CSS token system and shared UI primitives (thanks @Thundernerd).

Full notes on GitHub.
