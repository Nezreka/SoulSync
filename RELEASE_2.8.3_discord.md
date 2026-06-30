**SoulSync 2.8.3** is out 🎉 the headline is a full **Discover** rebuild — plus a real recommendation engine behind it.

🎨 **Discover, rebuilt** — a Spotify-level redesign (consistent cards, "mix" cards that open into full track-list modals, year/decade mixes, Last.fm Radio + ListenBrainz folded in, 2-column layout). the star is the **Adventurousness dial** — an animated living wave you drag to set how exploratory your recs are: turn it up and it loosens the genre leash, leans into the unheard, and demotes the famous, all at once. both rec rows are now scored on genre affinity + novelty + popularity, every card shows a **"why this rec"** chip (🎯 Your genres / 💎 Deep cut / 👥 N of your artists), and a background job quietly fills artist popularity (Spotify Free → Last.fm → Deezer) so the dial has real data to push against.

🐛 **Fixes** — Lyrics Filler no longer flags tracks that already have `.lrc` files on Docker/path-mapped setups (#955, thanks @diegocade1) · the import page stops re-scanning your whole staging folder on every tab switch, and album-match no longer times out on a slow NAS (#957, thanks @ramonskie) · library matching prefers an exact title over a remix variant — bare "Ratata" no longer grabs "Ratata (Afro Bros Remix)" (#958/#960, thanks @ramonskie) · Deezer no longer drops from hybrid / shows a false red dot.

🤝 **Contributor PRs** — auto-import + manual import now share one matcher, with album-variant disambiguation by track duration (#954, HellRa1SeR) · an experimental opt-in **JioSaavn** metadata source for Bollywood/Asian catalogs (#956, HellRa1SeR) · and the unit suite now passes on Windows too (#953, HellRa1SeR).

enjoy! 🎶
