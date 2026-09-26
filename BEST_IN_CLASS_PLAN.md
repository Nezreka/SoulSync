# best in class plan

source: discovery-search-research.pdf (sept 26), checked against dev.

the short version: soulsync already has more features than aurral or the arrs.
what it's missing is legibility. it makes good decisions and then hides why. so
this plan is mostly "show the work", then "give discovery a memory". no new
sources, no new shelves, no matcher rewrite.

---

## progress

(rebuilt from scratch on `claude/best-in-class` off dev 3.4.7; the first
attempt was reverted, so nothing below is inherited from it.)

- [x] phase 1: decision reasons. `core/downloads/decisions.py`,
  `evaluate_candidates` beside `get_valid_candidates` in validation.py. one
  `_select` path for both; the automatic path records nothing (pinned by a
  test that makes building a Decision throw). parity guard across 7 configs x
  2 orders, negative-checked 4 ways. codes added beyond the v1 table:
  `quarantined`, `too_large` (the soulseek quality filter drops those too),
  `unexplained` (never expected; a test fails if a fixture produces one),
  `below_cutoff` (phase 4). commit 8ad9001e.
- [x] phase 2: candidate inspector. redownload modal streams rejected rows
  (capped 50/source, true counts), reason pills, wanted-vs-found evidence,
  "grab anyway" behind a confirm (identity overrides warn harder). found and
  fixed on the way: a below-profile override used to download and then get
  quarantined by the import quality guard; it now skips that guard for the one
  file. preview / wrong-length / blacklisted rows can't be overridden (the
  file check or the worker would throw them away). reused from the wishlist
  ("search manually" now opens it for that exact track, pick = pinned
  one-task batch) and from failed downloads ("see what every source has").
  commits 56079789, 66f00868. mockup step: replaced by screenshots of the real
  component at desktop + phone widths.
- [x] phase 3: automatic grabs explain themselves. `download_decisions`
  (one row per task, newest 5000, cleared with download history, linked to
  track_downloads on import). the worker keeps only a summary (winner, 10
  alternatives, counts per code), never the pool. quarantine retries merge
  instead of replacing. track-detail modal: "why this file" / "why nothing was
  downloaded". commits 2c55e958, 85b6bbc3 (clear-history fix).
- [x] phase 4: quality upgrades surfaced. library "could be better" filter +
  per-artist counts; artist page per-track upgrade button and per-album
  "Upgrade N"; the inspector's upgrade mode rejects hits under the profile's
  cutoff as `below_cutoff`; "Apply Quality Upgrades" automation action
  (until_cutoff profiles only, capped per run, only exists if added).
  commit b1551261.
- [x] phase 5a: blocked artists never render. worse than suspected: discovery
  never read the profile blocklist at all, and the two surfaces that filtered
  (hero, recent releases) plus the discovery-pool sql used a global union of
  every profile's blocks. now one definition (`core/discovery/blocked.py`),
  applied to every discover GET that renders artists/albums/tracks, outside
  the shelf cache. a guard test fails for a new discover route that is neither
  filtered nor listed as "not a surface". daily mixes drop blocked seeds at
  generation (their subtitle is text) and rebuild when the blocks change. the
  discover page's blocked-artists modal now edits the profile blocklist; its
  old global table was being re-migrated on every start, so an unblock came
  back after a restart. commit a1d25e2a. also fixed on the way: three
  wishlist source guards still pinned the vanilla jump phase 2 removed
  (1dd94fd9).
- [x] phase 5b: one explanation shape. `core/discovery/explain.py`, under
  `explanation` (not `why`: artist cards already use `why` for chips and BYLT
  rows for a note). written by the producers: the listening-recs scan, BYLT
  generation, daily mixes, stations, and the similar-artist and hero cards.
  stored data from before (listening recs, BYLT generations) gets the plain
  version from its seeds. the ui words it in one place
  (`-discover.explanation.ts`), wording pinned to the old copy by the
  vanilla differential. stations write it but don't show it (the line would
  repeat the station's name); it's there for 5c's seed context.
- [ ] phase 5c
- [ ] phase 6
- [ ] phase 7
- [ ] phase 0 checklists

open question 4 (keep decisions forever?): answered. pruned with download
history ("Clear Completed" clears them) plus a 5000-row cap.

---

## where we actually stand (checked, not from the pdf)

- `core/search/basic.py` returns one flat list sorted by `quality_score`. no
  target track, so nothing is ever rejected there. it's a raw file browser.
- `core/downloads/validation.py::get_valid_candidates` is the real decision
  engine. it rejects for previews, duration, version (live/remix), artist gate,
  title words, youtube/prowlarr quality. every rejection is a `logger` line or a
  silent `continue`. the caller only ever gets the survivors.
- the redownload flow (`/api/library/track/<id>/redownload/search-sources`,
  web_server.py ~11185, react `redownload-modal.tsx`) ALREADY fans out across
  every configured download client, runs `get_valid_candidates` per source and
  streams candidates with confidence, quality, peers, blacklisted. that's 80% of
  the arr "interactive search" already built. it just drops the rejected ones.
- automatic grabs (`task_worker.py`) keep `cached_candidates` in memory for retry
  and write the winner to `track_downloads`. the alternatives and the reason it
  won are gone once the task ends.
- quality upgrades: music already has `upgrade_policy` (acceptable / until_cutoff
  / until_top) and the Quality Upgrade Finder repair job. the pdf's "quality-unmet
  queue" mostly exists. it's just buried in maintenance findings.
- recs: BYLT shelves are per seed, listening recs carry `seed_count`, daily mixes
  are profile-scoped and stored whole. partial explanations exist, no shared shape.
- feedback: `blocklist` (profile-scoped, id-keyed) is enforced at acquisition
  (`master.py`, add_to_wishlist). i couldn't find discovery surfaces filtering it,
  so a blocked artist can probably still show up in the pool / mixes. verify
  first thing in phase 5.
- there's no "less like this" or "not now" anywhere. #1284 fixed browsing leaking
  into taste, but there's still no explicit way to say no.

---

## phase 0: prove what's shipped (runs alongside everything)

best in class means it works on a real install. these shipped and were never
live-smoked:

- own library per profile (#1199)
- import page inbox + picard matcher
- downloads page redesign (batch groups)
- #1299 same-named releases
- basic search: as-is + tag-it-yourself paths (enriched was verified)
- chat evolution p1-p4, server activity, video metadata edit+lock

one short checklist per feature, boulder runs it, bugs get fixed before new
work lands on top. i'll write the checklists.

---

## phase 1: decision reasons (backend only, no ui)

the foundation. everything after this reads it.

**new module `core/downloads/decisions.py`:**

```python
@dataclass(frozen=True)
class Decision:
    accepted: bool
    code: str              # stable, never renamed once shipped
    detail: str = ''       # human line, e.g. "4:12 vs expected 3:58"
    stage: str = ''        # preview | identity | version | duration | quality | policy
    score: float | None = None
```

**reason codes (as shipped; add, never rename):**

| code | stage | when |
|---|---|---|
| `accepted` | decision | passed everything |
| `preview` | preview | soundcloud snippet / short preview |
| `duration_mismatch` | duration | strict-duration source outside integrity tolerance |
| `artist_mismatch` | identity | artist gate failed, or artist not in the soulseek path |
| `artist_unverified` | identity | youtube with no artist evidence and foreign title words |
| `version_conflict` | version | live/remix/acoustic marker vs what was asked for |
| `match_weak` | identity | score under threshold |
| `outranked` | identity | youtube outside the confidence band of the top match |
| `below_profile` | quality | fails the quality profile (soulseek, youtube, prowlarr) |
| `peer_queue` | availability | soulseek peer queue over the limit |
| `blacklisted` | policy | in `download_blacklist` (set by callers) |
| `duplicate` | decision | same file as a higher-ranked row (set by callers) |
| `quarantined` | policy | this exact file failed an earlier import (soulseek filter) |
| `too_large` | policy | over the MB/min size cap (soulseek filter) |
| `below_cutoff` | quality | upgrade mode only: doesn't reach the profile's cutoff |
| `unexplained` | decision | safety net; tests fail if any fixture produces it |

**change:** `get_valid_candidates` gets a sibling
`evaluate_candidates(results, track, query, profile_id) -> list[(result, Decision)]`
that returns EVERY candidate. `get_valid_candidates` becomes a thin filter over
it, so the automatic path keeps its exact behaviour and signature.

each `continue` / early `return []` in validation.py becomes a recorded
decision. the log lines stay.

**tests:**
- one per code, fed a real-shaped `SearchResult`
- parity guard: for a fixture pool, `get_valid_candidates` output is identical
  before and after (order and membership). negative-check it by breaking one
  filter.
- no code is ever emitted that isn't in the table (enum guard)

**risk:** validation.py is the hottest path in the app. parity test is the gate,
full downloads suite before commit.

---

## phase 2: candidate inspector (the headline)

build it by growing the redownload modal, not a new page.

**backend:** `redownload/search-sources` switches to `evaluate_candidates` and
streams rejected rows too, each with `decision: {accepted, code, detail, stage}`.
add identity fields the ui needs: parsed artist/title, match confidence,
bit depth / sample rate where known.

**ui (react, `redownload-modal.tsx` step 2):**
- default view: accepted only, ranked like today. nothing changes for someone
  who doesn't care.
- a quiet toggle "show 14 rejected". rejected rows dim, reason as a small pill
  ("version: live", "too short", "below your profile").
- row expands to evidence: expected vs found for title, artist, duration,
  quality, and which profile rule applied.
- "grab anyway" on a rejected row goes through `showConfirmDialog` that states
  what's being overridden. identity overrides (wrong artist/version) get a
  stronger warning than quality ones.
- overrides never touch the profile. they affect this grab only.

**then reuse it in two more places:**
- wishlist item "search manually" (today there isn't a real one)
- failed download row on the downloads page: "see what it found"

**fan-out manners:** the inspector is the ONLY place that searches every source
at once, and only when the user opens it. automatic stays on source priority.
soulseek searches stay one at a time per the existing client rules.

mockup first (design bar), then build.

---

## phase 3: automatic grabs explain themselves

**schema:** new table `download_decisions`

```
id, track_download_id (nullable), task_key, track_title, track_artist,
profile_id, chosen_json, alternatives_json (top 10 + counts per code),
created_at
```

`_COLUMN_MIGRATIONS` style, never touched by existing tables.

**write:** `task_worker` after `attempt_download_with_candidates` succeeds or
gives up: winner decision + top alternatives + a count of rejections by code.
capped size, pruned with the same retention as download history.

**read:** downloads page row, "why this file" opens a small panel: what won,
why, what came second, what got rejected and how many. failed rows show "nothing
passed: 9 too short, 4 wrong version, 2 below profile" which is the answer to
most support threads.

---

## phase 4: surface quality upgrades

no new engine. the Quality Upgrade Finder already does the work.

- promote its findings out of the tools page into a "could be better" filter on
  the library / downloads page
- per-album "upgrade" action that runs the phase 2 inspector with the profile's
  cutoff pre-applied
- optional automation: "apply upgrade findings nightly" for users on
  until_cutoff. off by default.

---

## phase 5: discovery explains itself + takes feedback

**5a. verify + fix blocklist in discovery.** check pool, BYLT, daily mixes,
stations, hero, listening recs. blocked artists must never render. guard test
per surface.

**5b. one explanation shape.** every rec record carries, at creation time:

```json
{"why": {"kind": "similar_to|listened|genre|new_release|trending",
         "seeds": [{"name": "Tool", "id": "...", "source": "deezer"}],
         "confidence": 0.82}}
```

BYLT, listening recs, daily mixes and stations write it. the ui renders one
line: "because you play tool and deftones". no reconstructing it in the ui.

**5c. feedback.** new table `discovery_feedback`:

```
profile_id, entity_type (artist|album|track), entity key (name + source ids),
kind (more|less|not_now), seed_context_json, created_at, expires_at
```

- more like this: boosts the seed in scoring
- less like this: dampens the artist and the seed edge that brought it
- not now: hides it for 30 days
- block: writes the existing `blocklist`. reset taste never clears blocks.

scoring reads feedback in `core/discovery/scoring.py` and the bylt/mix
generators. each surface gets a ⋯ menu with the four actions (same pattern as
the library tab redesign).

---

## phase 6: discovery inbox

one place for things worth coming back to.

- sources: watchlist new releases (`recent_releases`), saved recs, upcoming
  releases, concerts if enabled
- states per profile: unread, saved, dismissed, added
- refresh runs in the background, partial failure shows "deezer didn't answer,
  showing the rest", never a blank inbox
- badge count in the nav

new table `discovery_inbox` keyed by profile + entity. this overlaps discover
elevation p5 (unification), so fold it into that rather than a new page.

---

## phase 7: renewable mixes

daily mixes become recipes you can subscribe to.

- recipe: seeds or genres, year range, source mix (library / discovery /
  trending), schedule, length
- rules: one song per artist, source shortfall redistributes, keep a reserve,
  replace tracks that fail to download
- "keep this one" freezes a generation into a normal playlist

last because it leans on 5b and 5c. the generator already exists
(`core/personalized/generators/daily_mix.py`), this is mostly config + storage.

---

## what we're not doing

- dense arr-style tables as the only ui
- another metadata source or shelf this cycle
- rewriting the matcher. only after phase 3 data shows it picks badly
- aurral's exact scoring constants or its lidarr dependency

## order and size

| phase | size | depends on |
|---|---|---|
| 0 live-smoke | ongoing | none |
| 1 reasons | medium | none |
| 2 inspector | large | 1 |
| 3 auto explain | medium | 1 |
| 4 upgrades surfaced | small | 2 |
| 5 discovery feedback | large | none (5a first) |
| 6 inbox | medium | 5 |
| 7 renewable mixes | medium | 5 |

1 → 2 → 3 is the search track. 5a can start any time, it's a bug.

## open questions for boulder

1. inspector in the redownload modal first, or straight to wishlist "search
   manually"?
2. `not_now` 30 days ok, or configurable?
3. inbox inside discover, or its own nav item?
4. should phase 3 keep decisions forever or prune with download history?
