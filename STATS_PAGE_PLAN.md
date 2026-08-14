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

## P1 — Comparison layer (the cheapest thing that changes everything)

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

## P2 — Split the page in two

Library Health, Format Breakdown, Database Storage, Library Disk Usage are
operator facts. Top Artists / Albums / Tracks / Genres / Recently Played are
personal facts. They want different visual languages and different visit
frequencies.

- Personal stays at `/stats`. Ops moves to a "Library" tab on the same route
  (or folds into Tools, which is where operators already live — decide during
  the phase, don't pre-commit).
- Pure re-arrangement. No new data.

## P3 — The two charts that are actually personal

1. **When you listen** — hour-of-day × day-of-week heatmap. One `GROUP BY` over
   `played_at`. The most personal chart available per unit of effort.
2. **Sessions and streaks** — gaps in `played_at` give sessions; consecutive
   days give streaks. Yields "longest session", "biggest listening day",
   "current streak".

New DB helpers next to the existing `get_listening_timeline`. Both are cacheable
in the same worker pass.

## P4 — Own vs play (the thing only SoulSync can say)

Join `listening_history` to `tracks`: you *own* 40% metal and *play* 12% of it.
Genre, decade, and format gaps between the library and actual listening.

Nobody else can show this — Spotify has no library, Plex has no acquisition
history. This is the page's strongest claim to being worth visiting.

Also unlocks: **unplayed but owned** (already have `unplayed_count`, but it is a
dead number today — make it a *list* you can act on) and **rediscovered**
(not played in 6 months, then played again).

## P5 — Year in Listening (the big one)

A separate surface — `/stats/year` or a modal takeover — NOT another section.

What makes Wrapped work is three things, in order of importance:
1. a **fixed period**, not a filter
2. a **sequence of single-idea moments**, not a grid
3. a **shareable card** at the end

Spine: top artist per month for twelve months, month-over-month shifts, your #1
of the year, minutes as "that's N days", new artists discovered, the one track
you played most and when.

All of it is SQL over `listening_history`. The work is sequencing and design,
not data.

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

- Where do the ops stats live after P2 — a tab here, or Tools?
- Is Wrapped year-bound (Jan–Dec) or rolling twelve months? Rolling is more
  useful year-round; fixed is more shareable in December.
- Does the shareable card render server-side (Pillow, like the overlay
  compositor already does) or client-side canvas?
