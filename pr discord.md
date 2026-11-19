**SoulSync 3.6.8 is out** :musical_note:

**Last.fm without Pro.** From 1 December 2026 a Last.fm API key needs a Last.fm Pro subscription. With a working key nothing changes. Without one, top tracks, similar artists, radio and Your Artists come from ListenBrainz and Deezer, and artist bios and tags from TheAudioDB, Wikipedia and MusicBrainz. Answers are cached, scrobbles wait for a working key, and ListenBrainz is the recommended scrobbler.

**Phones.** Worker status opens on a tap, the menu button stays on an open playlist, every page reaches its end, menus, import cards and long confirm boxes fit the screen, and Re-identify is a full-screen sheet (thanks Thibs, c5pie and wolf39us, #2180 to #2187, #2197).

**People and permissions.** Audiobook and podcast episode requests can require approval, controls show the value the policy keeps them at, chat only reaches people allowed to chat, the enrichment workers are admin only (#2196), and requests read Auto-approve, Require approval or Requests off everywhere (thanks SeadogsBooty, #2188 to #2192). The admin menu no longer flashes after signing back in (#2156).

**Library.** Titles beat track numbers when lining up a tracklist, a song's number heals from its file, and a deleted file stops borrowing another album's (thanks wolf39us). Rename / Organize keeps $quality (thanks Maegfea, #2195) and skips a removed album (#2194). A track you own can move into an album's missing slot, and Reassign moves instead of copying (#2193).

The transfer folder is now called the music library folder everywhere. In Docker it is still the /app/Transfer mount, so your setup doesn't change.

Full notes on GitHub.
