# discover page — best in class plan (#913 + full generator audit)

morning notes. did the work overnight. tl;dr at top, details below, all of it `break nothing` + tested.

## what i shipped tonight (done, tested, safe)

### 1. listening recommendations (#913) — went from BROKEN to best-in-class

the feature was silently producing **zero** recs on real data. dug in and found three stacked bugs in the generation:

- **wrong id key (the killer).** `similar_artists.source_artist_id` is a *source* id (spotify/itunes/deezer), but the scanner built its id→name map from `artists.id` (the internal row id). so every edge resolved to nothing → 0 recs. proved it on your live db: internal-id join = 0 rows, spotify-id join = 71,636 rows.
- **consensus could never fire.** it fed the ranker `get_top_similar_artists`, which does `GROUP BY similar_artist_name` + `MAX(source_artist_id)` — collapsing every similar artist down to a *single* seed. the whole point of the ranker is "artist X is similar to 3 of your seeds = strong signal," and that signal was being flattened away before it ever reached the ranker.
- **similarity strength thrown away.** each edge stores a 1-10 closeness rank; it was ignored (everything weighted equally).

the fix (all in the pure, tested core + thin scan wiring):
- build id→name from the **source-id columns**, query the **raw per-seed edges** (consensus preserved), and thread **similarity_rank** into the score so a seed's closest matches count for more.
- **recency-weighted seeds**: `weight = lifetime_plays + 1.5 × recent_30d_plays`. picks now track what you're into *now*, not just all-time totals.

result on your actual library (simulated through the real code path): **40 recommendations, 13 with multi-seed consensus, all 40 with cached art.** top picks: Arcangel (Bad Bunny + Ozuna + J Balvin), Melanie Martinez (Ariana + Billie), Maluma, De La Ghetto — all coherent, all explainable.

### 2. its own row on the discover page

new row **"Based On Your Listening"** — play-weighted, consensus-ranked artist cards with a **"Because you listen to X, Y"** line. sits right above the library-driven "Recommended For You" row. purely additive: new endpoint `/api/discover/listening-recommendations`, new loader, hides itself when empty.

**you need to run one watchlist scan** for the row to populate (the data regenerates during the scan — i did NOT touch your live db). before that scan the row just stays hidden; after it, it fills in.

> note: this is deliberately different from the existing "Recommended For You" row. that one is driven by your *whole library / watchlist*. this one is driven by your *actual listening intensity* — the ~30 artists you really play, not the thousands you happen to own.

### 3. Fresh Tape "only 5-10 tracks" — fixed

root cause: `get_discovery_recent_albums` orders `release_date DESC`, so announced-but-unreleased albums sort to the *top* and ate the 50-album budget. the scanner skipped them *after* the budget was already spent → only a handful of released albums left → 5-10 tracks. fixed by fetching a generous budget (300) **and** excluding next-year albums at the query, so released albums fill the budget. the precise same-year `is_future_release` skip stays as a second guard. downstream caps (6/artist, top 75, take 50) unchanged.

**tests:** 25 pure-core cases (consensus/similarity/recency) + 2 Fresh Tape regression tests, all green. full discovery suite (255) green. nothing else touched.

---

## best-in-class roadmap for listening recs (next phases — your call)

these are the levers to take it further. ordered by value-to-risk. none are required; tonight's work stands on its own.

| phase | what | value | risk | notes |
|---|---|---|---|---|
| **3** | **playable track row** ✅ DONE | high | low-med | shipped: "🎧 Your Listening Mix" row — a track playlist (play/queue/download/sync) right under the artist row. stored as full render-ready dicts (not pool-hydrated, so it can't shrink on pool rotation like Fresh Tape does). |
| **4** | **direct top-tracks fetch** ✅ DONE | high | med | shipped: scan fetches each recommended artist's top tracks (Spotify/Deezer), resolving the artist id by name-search when the similar-artist row lacks one — guarded by a strict name-match so it never pulls the wrong artist. bounded (top 20 recs), per-call guarded, fail-soft to the pool. iTunes has no top-tracks API → pool-only there. needs a live scan to populate. |
| **5** | **genre-affinity boost** | med | low | we already compute your genre breakdown. boost recs whose genres match your top genres → tighter taste alignment. pure scoring add. |
| **6** | **adventurousness dial** | med | low | the ranker already supports `min_seed_count` (consensus floor). expose it as a "Safe ↔ Adventurous" slider on the row. |
| **7** | **diversity pass** | low-med | low | avoid 40 recs all orbiting your single heaviest seed — cap picks-per-seed so the row spans your taste. |

the core is built to absorb all of these without re-plumbing — `similarity_from_rank`, `build_recency_weighted_seeds`, and the scoring formula are all pure + tested.

---

## full discover-page generator audit (every soulsync-built row, excluding last.fm + listenbrainz)

how each one is generated today, and whether it can be elevated. "clear win" = safe + additive. "product call" = needs your decision (changes the row's character).

### curated (built during the scan, then hydrated)
- **Fresh Tape / Release Radar** — new releases from watchlist+similar artists. **FIXED tonight** (see above). one more *clear win* available: hydration silently drops any curated id no longer in the discovery pool — could fall back to the stored `track_data_json` blob so the row can't shrink at read time.
- **The Archives / Discovery Weekly** — strong already. nice 3-tier popularity split + serendipity scoring (boost never-played artists, penalize overplayed). same hydration-drop caveat as Fresh Tape; same cheap fallback fix.
- **Seasonal Mix** — cleanest of the bunch. hydrates from a dedicated `seasonal_tracks` table (carries its own data), so it doesn't suffer the pool-drop problem. no bug.

### discovery-pool generators (live queries)
- **Popular Picks** — ranks by popularity DESC. solid. only nit: on iTunes (no popularity scale) it silently degrades to random — indistinguishable from Shuffle there. UI-label thing at most.
- **Hidden Gems** — *clear win*. currently `ORDER BY RANDOM()` over low-popularity tracks — so it's "random obscure," not "*best* obscure." a light ranking (popularity just under the threshold, or genre-affinity to you) would make it feel curated instead of arbitrary. (a deeper *product call*: add personalization like Archives has — bigger lift, changes its "pure underground" character.)
- **Genre Playlists** — good. pushes the genre match into SQL. `RANDOM()` ordering is fine for a browse; a popularity/affinity tiebreak (*clear win*) would make thin genres feel less arbitrary.
- **Discovery Shuffle** — random by design, correct. only possible add: exclude tracks already shown in other rows this refresh (needs a cross-section seen-set — medium plumbing).
- **Time Machine (by decade)** — *clear win, low risk*: decades are hardcoded, so a modern-only library shows 7 decade tabs, 5 empty. filter the tabs to decades that actually have pool data.
- **Daily Mix** — the weakest row. the "50% your library" half permanently returns nothing (library tracks have no source ids to play), so each Daily Mix is really just a relabeled Genre Playlist. real fix = backfill source ids into library rows (*schema-level, higher risk*) — worth a dedicated pass, not a quick tweak. also silently falls back to "top artists as pseudo-genres" when genre data is missing → "Daily Mix 1" becomes mislabeled artist-radio. gate/label that (*clear win*).

### cross-cutting
- **hydration fragility** (Fresh Tape + Archives): both depend on curated ids still living in the pool at read time; misses are dropped silently. Seasonal already solved this with a dedicated table. giving the two spotify-style rows the same data-blob fallback is the single most robust cross-cutting fix. low risk, clear win.
- **RANDOM-ordering pattern** (Hidden Gems, Shuffle, Genre, Decade): intentional for variety, but leaves quality signal on the table for the non-shuffle rows. adding a light ranking pass to Hidden Gems + Genre is the biggest "best-in-class" lever after tonight's work.

want me to take any of these? the Hidden Gems ranking + Time Machine empty-decade filter + the Fresh Tape/Archives hydration fallback are all safe, additive, same-shape-as-tonight wins i can knock out next.
