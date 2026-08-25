# discover elevation - beat aurral on every front

goal: best-in-class music discovery. aurral's pitch is recommendations +
rotating flows on top of lidarr. our structural edge: we OWN the library,
the downloads, the media servers and the listening history - so our mixes
can blend tracks you can PLAY RIGHT NOW with tracks one click from
downloading. nothing else self-hosted can do that. every phase below
leans on that edge.

## where we already win (don't break it)
17 playlist mechanisms, adventurousness dial, artist maps/webs, genre
deep dives, label explorer, seasonal, decades, listening recs with why-
chips, LB + lastfm integration, per-mix download AND sync to media
server. aurral has nothing like the maps or the owned-library depth.

## the gaps (from the aug 25 inventory)

1. NOTHING ON DISCOVER PLAYS AUDIO. window.playTrackList and
   startLibraryRadio exist and nothing on the page calls them. spotify's
   page works because everything is instantly listenable.
2. DAILY MIXES ARE DEAD CODE. -discover.mixes.ts marks daily_mix_* live:
   false; the generator's "50% your library" half returns nothing and it
   silently degrades to a relabeled genre playlist.
3. NO STATIONS. artist radio seam exists (playArtistRadioById) with no
   surface. spotify's "recommended stations" row is boulder's explicit
   ask.
4. generator audit debt: fresh tape/archives silently shrink when the
   pool rotates (hydration fragility), hidden gems is ORDER BY RANDOM(),
   shuffle repeats what other rows showed, v2 time machine still
   hardcodes decades.
5. two parallel personalized stacks (legacy on discover, v2 on sync).

## phases

P1 - MAKE IT PLAY (small, transformative)
  every mix modal + station card gets a play affordance. owned tracks
  resolve by title/artist against the library and feed
  window.playTrackList; mixes play what you own and say how many tracks
  are missing (download button right there). library radio gets a card
  in the tools zone.

P2 - REAL DAILY MIXES (the marquee)
  new core/personalized/daily_mixes.py:
  - cluster recency-weighted top artists (listening_history) via
    similar_artists adjacency + artist_genres into 2-6 taste clusters
  - each mix: ~40 owned tracks (play-weighted, artist-spaced) + ~10
    discovery tracks from the cluster's similar artists (respecting
    adventurousness), anti-repeat vs personalized_track_history
  - persisted in the v2 manager (first-class rows, per-instance config)
  - refreshed by a SYSTEM automation (daily) - all five wiring points
  - discover shelf: "Made For <profile>" row, cluster-named cards
    ("Daily Mix 1 · <top artists>"), play/download/sync
P3 - RECOMMENDED STATIONS
  a row of station cards: top ~8 recency-weighted artists + 2 discovery
  artists (adventurousness-scaled). card = artist art + "with X, Y and
  more" from similar_artists. click = artist radio plays NOW; download-
  missing per station.

P4 - GENERATOR QUALITY PASS
  - hydration fallback to track_data_json for fresh tape + archives
    (the plan doc's "single most robust fix")
  - hidden gems: rank by genre affinity + recency instead of RANDOM()
  - discovery shuffle: cross-row seen-set
  - v2 time machine: decades-with-data only

P5 - ONE PERSONALIZED STACK
  discover's shelf reads the v2 manager (anti-repeat history, per-
  instance config) instead of the legacy service; legacy endpoints stay
  for compat until the modal paths move.

P6 - POLISH + AURRAL PARITY EXTRAS
  - "Made For <profile>" header treatment, Show all rails
  - dead code: -discover.layout.ts buildLayoutRows, stale docstring
  - the 88 unstyled class names triage (map/web panels)
  - LATER/OPTIONAL: concerts via ticketmaster (aurral has it; needs api
    key + settings), per-user discovery layouts

## status
- [x] P1 play affordances (mix modal + library radio card) - aug 25
- [x] P2 daily mixes v1 (clustering + generator + TTL refresh + shelf) - aug 25, real-data verified
- [ ] P3 stations row
- [ ] P4 hydration fallback + hidden gems + shuffle seen-set
- [ ] P5 stack unification
- [ ] P6 polish pass
