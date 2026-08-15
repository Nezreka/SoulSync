# Stats page — elevation plan

Agreed direction after the Aug 14 2026 review. The page today is honest but
inert: ten sections of totals, no comparisons, four `onClick` handlers (all of
them the range picker), and library-ops facts sitting in the same visual
language as personal listening facts.

**The asset we already have and barely use:** `listening_history` is a per-play
event log — `played_at` (indexed), artist, album, title, `duration_ms`,
`server_source`, `db_track_id`, deduped by unique index. Every Wrapped-style
feature comes from exactly this shape. We currently reduce it to five totals and
three top-25 lists.

Phases are ordered by *feel-change per unit of work*, not by ambition.

---

## P1 — Comparison layer ✅ SHIPPED (cc23bdad7)

Every overview tile gains "vs previous period": 1,247 plays **↑31% vs last
month**. Same for time, artists, albums, tracks.

- Backend: `_build_stats_cache` already loops `('7d','30d','12m','all')`. Add a
  second query per range over the *preceding* window of equal length and store
  `previous` alongside `overview`. No new endpoint, no new table.
- Frontend: one `<Delta>` component; tiles gain a sparkline-free arrow + pct.
- Edge cases that must be right: no previous data (hide, don't show ↑∞), a
  previous value of 0, and `all` (no meaningful previous — omit).

**Why first:** turns ten trivia numbers into ten signals for one extra query per
range. Nothing else on the list has this ratio.

## P2 — Split the page in two ✅ SHIPPED

Library Health, Format Breakdown, Database Storage, Library Disk Usage are
operator facts. Top Artists / Albums / Tracks / Genres / Recently Played are
personal facts. They want different visual languages and different visit
frequencies.

**Decided: a tab on `/stats`, not a move into Tools.** Tools is where you go to
RUN things; these are reference facts, and burying them one page deeper would
have made findable things less findable — the opposite of the wider problem.
The tab lives in the URL (`?tab=library`) so it is linkable and survives a
reload, and the range picker hides there because disk usage is not
range-scoped — an inert control is worse than no control.

## P3 — The two charts that are actually personal ✅ SHIPPED

1. **When you listen** — hour-of-day × day-of-week heatmap. One `GROUP BY` over
   `played_at`. The most personal chart available per unit of effort.
2. **Sessions and streaks** — gaps in `played_at` give sessions; consecutive
   days give streaks. Yields "longest session", "biggest listening day",
   "current streak".

New DB helpers next to `get_listening_timeline`, both folded into the same
worker cache pass. Sessions (gap detection) deliberately deferred — the gap
threshold is a judgement call and it is the least legible of the numbers.

**TIMEZONE, discovered during this phase and worth knowing:** `played_at` is
stored as LOCAL naive wall-clock — the web player writes
`datetime.now().isoformat()` and `plex_client` writes `item.viewedAt`, both
local. That makes the heatmap correct for free (`strftime('%H')` IS the hour
you listened) and must not be "fixed" to UTC.

The same fact means the RANGE FILTERS are skewed: they compare local
timestamps against SQLite's UTC `datetime('now')`, so every range-scoped stat
is off by the server's UTC offset (a few hours on a 7-day window). Pre-existing
and untouched here — changing it moves every existing number, so it wants its
own change with its own before/after.

## P4 — Own vs play (the thing only SoulSync can say) ✅ SHIPPED

Join `listening_history` to `tracks`: you *own* 40% metal and *play* 12% of it.
Genre, decade, and format gaps between the library and actual listening.

Nobody else can show this — Spotify has no library, Plex has no acquisition
history. This is the page's strongest claim to being worth visiting.

Shipped as genre share-of-library vs share-of-plays, sorted by the size of the
DISAGREEMENT (the biggest genre is something you already know), plus a
"never played" album shelf — `unplayed_count` was a dead number, an album you
can act on is worth more.

Both sides are percentages of the GENRE-KNOWN population and share one parser
(`_accumulate_genres`), so an untagged artist is absent from both rather than
counted as a zero on one, and a genre cannot be spelled differently on the two
halves — which would render as a real gap.

Decade and format gaps NOT built: `tracks.year` is sparse and format lives only
in `file_path`, so both would be gaps in the metadata dressed up as gaps in
taste. **Rediscovered** still to do.

## P5 — Year in Listening ✅ SHIPPED

A separate surface, opened as `?story=year` from a single accented button on
the Listening tab. Takeover rather than a route: the open state still lives in
the URL (linkable, survives reload, Back closes it), without a second page
shell or router surgery for one screen.

All three things that make the format work are in:

1. **A fixed period, not a filter** — `get_year_in_listening()` decides the
   window and the endpoint takes no `range` argument at all. Letting a caller
   narrow it would turn the story back into the picker the page already has.
2. **A sequence of single-idea moments** — nine slide kinds, keyboard and
   button driven, with progress pips.
3. **Something you keep** — a client-side canvas card with Save as image.

**Decided: rolling twelve calendar months, not Jan–Dec.** A self-hosted app
gets opened in August, and a fixed calendar year would hand a five-month-old
install an empty story for seven of its twelve slots. The period label prints
the real range so nothing is implied that the data does not cover.

**Decided: client-side canvas, not server-side Pillow.** The browser already
holds every number on the card; the server alternative meant re-querying the
year and adding an image endpoint to redraw what was on screen.

The slide list is DERIVED, not fixed (`buildYearSlides`). An empty slide is
worse than a shorter story — it breaks the promise that each screen was worth
advancing to. So a year with one artist gets no top-five countdown, a year
where nothing was discovered gets no discoveries slide, and a year with no
plays is a single honest screen that says so.

Two things worth knowing about the data:

- **Discoveries compare first-play against ALL of history, not the window.**
  Scoping that subquery to the window would call every returning artist a
  discovery — the most obviously wrong number this surface could print. There
  is a test that fails if anyone narrows it.
- **This surface is FREE OF THE UTC SKEW** the range filters carry. The window
  is built from the local clock and compared with `date(played_at)`, which
  matches the local wall-clock the column stores — and parses both stored
  shapes (the web player writes an ISO 'T' separator, plex_client a space,
  and a lexicographic compare orders those differently at a boundary).

Cached once as `stats_cache_year`, not per range — it is a period, not a
filter, and four copies under four keys would be four chances to disagree.
The endpoint computes it live on a cache miss, which is the path that matters:
a fresh or just-restarted install would otherwise show an empty year that
looks identical to "you have never listened to anything".

### P5b — artwork pass (second round)

First version shipped name-only, because the year cache never went through
the enrichment the per-range caches get. That was a wiring gap, not a data
gap: the images were sitting in `artists.thumb_url` / `albums.thumb_url` the
whole time.

- `_enrich_stats_items` LIFTED out of `ListeningStatsWorker` into
  `core/stats/enrich.py` so the cached path and the live-compute path share
  one enricher. A version that only existed on the worker is exactly how the
  live path ended up returning bare names.
- `discoveries` now enriches as an artist list (`ARTIST_LIST_KEYS`), so the
  slide has both artwork and the `id` it needs to link through.
- Every artist row in the story links to artist detail. Rows we could not
  resolve an id for render as plain text rather than a link to
  `/artist-detail/library/undefined`.
- A new **album wall** slide, and artwork on the opening, countdown and
  on-repeat slides. Missing art renders as an initial-letter tile, never a
  hole — a patchy library must not produce a ragged grid.
- The share card was rebuilt: `-year.card.ts` holds a pure
  `buildYearCardModel` (content + palette + which art) and a `drawYearCard`
  geometry pass, so what the card SAYS is testable without a canvas. A live
  preview is drawn by the same call that exports — the preview cannot drift
  from the file.
- **The canvas taint trap:** artwork proxied from the media server is
  same-origin, but anything from an external metadata provider is not.
  Drawing one of those without CORS taints the canvas and `toBlob` throws.
  Images load with `crossOrigin='anonymous'`, and a failed export retries
  text-only rather than handing back nothing.

`shareCardLines` / `shareCardFilename` were DELETED from `-year.helpers.ts`
when the card model landed — two sources of truth for the same card content
is the drift that bites six months later.

### P5c — motion, playback, and the card STUDIO

**Motion** (`-year.animation.ts`): headline numbers count up, month bars grow
from zero in sequence, list items reveal behind one another. The easing,
duration scaling and stagger cap are pure and tested, because animation bugs
otherwise only ever surface as "it looked wrong for a second".

Duration scales with magnitude — 12 and 40,000 ticking over the same span
looks broken, because the eye reads the individual digits. Stagger is capped
so the twelfth month does not arrive after the reader has moved on. All of it
degrades to the final state under `prefers-reduced-motion`.

**A real bug the motion tests caught:** the count-up seeded its clock from
`performance.now()` but measured elapsed against the rAF callback timestamp.
Those are not guaranteed to share a time origin; where they do not, `elapsed`
goes negative and the number sits at zero forever. The clock now comes from
the first rAF timestamp.

**Playback:** album cards on the wall play the album — `/api/stats/album-tracks/<id>`
returns rows shaped exactly like `/api/library/radio`, because that is what
`npMapRadioTrack` (media-player.js) reads and anything else silently drops out
of the queue. Tracks with no `file_path` are excluded server-side: a row the
player would skip is not a track you own, and counting it makes "play album"
look like it lost songs.

**The card is now a STUDIO, not a template** — 4 layouts × 3 aspects ×
4 themes, plus which numbers appear.

This is the deliberate divergence from Spotify. They ship one fixed
composition because they render it for half a billion people; we render it for
one person who owns their music, so the card can offer real choices AND use
the actual covers off their disk. A single fixed template throws that away.

- Layouts are different COMPOSITIONS, not restyles: `poster` (one cover, full
  bleed, bottom-anchored type), `mosaic` (a 3-wide wall of covers with a text
  panel), `stack` (the balanced default), `minimal` (type only). A layout
  picker whose options all look alike is worse than no picker.
- Aspects are Post 4:5 / Square 1:1 / Story 9:16, and the type SCALES with the
  card — a story card is not a post card with more empty space.
- `paper` was added as a light theme because every dark card looks the same in
  a feed.
- Stats are user-selected, capped at 5, and always render in DEFINITION order
  — a card whose rows reshuffle as you tick boxes feels broken rather than
  configurable. `toggleCardStat` refuses to leave the card with none.
- **Copy to clipboard** alongside save: on desktop the real share gesture is
  paste, not "find the downloaded file and drag it somewhere".

## P6 — three.js, if it earns it

Lowest value, highest cost. Bundle is already 2.2 MB; three.js is ~600 KB.

If it goes in it is **one** hero moment inside the P5 sequence — not decoration
on the stats page. Revisit only after P5 ships and is worth landing on.

---

## Not doing

- More top-N lists. Twenty-five artists is already more than anyone reads.
- A second range picker. The ranges are fine; what was missing is comparison.
- Real-time stats. The precomputed worker cache is the right call — this data
  does not need to be live.

## Open questions

Both P5 questions are now answered above (rolling twelve months; client-side
canvas). What is left:

- The pre-existing UTC skew on the RANGE filters. P5 sidesteps it; the older
  range-scoped stats still carry it. Fixing it moves every existing number on
  the page, so it wants its own change with its own before/after — not a
  drive-by.
- **Rediscovered** tracks (P4 leftover): something you played heavily, stopped,
  and came back to. Needs a "gap" definition, which is a judgement call.
- Sessions / gap detection (P3 leftover), for the same reason.
