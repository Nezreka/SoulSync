# Library v2: who writes, who reads

This is the rule every Library v2 feature follows, and the rule a new one has
to follow. It exists because the same bugs kept coming back: a tool that asked
a provider on its own got a different answer than the catalogue held, the
user's own values lost to it, and each report got its own patch.

## The rule

**The catalogue is the truth.** The `lib2_*` tables hold what the library
knows: artists, releases, tracks, files, provider ids, credits, the user's own
values. Everything else (tags on disk, a media server, a provider's live
answer) is a source that gets written INTO the catalogue, or a projection that
is written FROM it.

## Three responsibilities, one catalogue

Library Overhaul separates three operations. A job can orchestrate them, but
must delegate each operation to its existing owner:

| Responsibility | Reads | Writes | Existing examples |
|---|---|---|---|
| Fetcher / ingestion | providers, imported files, server observations | stored provider evidence, ids, source metadata and provenance | enrichment workers, `provider_credits.ensure_track_credits` |
| Healer | catalogue, stored evidence, user intent and locks | corrected catalogue identities, relationships and metadata | `native_enrich.heal_combined_artist`, Split Artists, the credit healers |
| Projection writer | committed effective catalogue | file tags, file names and folders, tag/path observations | the Re-tag job, the Reorganize job |

A healer is the intermediate stage: it may request a scoped fetch when its
stored evidence is insufficient, then read again. It applies the shared
identity decision and persists the correction. It never writes file tags or
moves files itself. A tag writer does not infer a different artist from a
live provider response or repair a catalogue identity as a side effect.

**The default healer operation changes only the catalogue.** Dry run reports a
proposed catalogue correction as a finding. Applying it commits the correction;
it need not run any projection immediately. Later tag validation and path-drift
jobs compare files with the corrected catalogue and can generate their own
findings or apply their normal automation policy.

**A healer hands its changes to the projection jobs, it doesn't run them.**
After the catalogue transaction commits, it may ask the repair worker to run
Re-tag and / or Reorganize scoped to the files of the tracks it changed
(`RepairWorker.request_projection(track_ids, retag=..., reorganize=...)`).
Those jobs then act by their own settings: by default they file findings,
they write only when the user switched their automatic mode on, and they
apply their own rules (hand-set values, hand-tagged files, locks, history).
Their on / off switch is their library-wide schedule, not a veto: whether a
heal asks is the healer's own setting (Split Artists: Request Re-tag /
Request Reorganize, both on by default). Requests for one job that arrive
before it starts add up to one run over all their files. If a projection
fails or waits, the corrected catalogue stays, and the next run of the job
finds the same files again.

For a full orchestration, dependency order is fetch -> heal/commit -> optional
Reorganize/Rename -> optional Retag. Independent jobs can run on their normal
schedule because they all derive the same intended state from the catalogue.
No healer silently renames files, and no requirement exists to run every step.
If a selected album field affects sibling tracks, validate the shared scope
before changing it; a track-level repair must not rewrite unrelated albums.

**Detection follows the same rule as the fix.** A healer finds its subjects
in the catalogue, not in the files. A job that detects from file tags or
folder names but heals the catalogue reports the same subject again until a
projection job has run (and forever while that job is in dry run). A file
that disagrees with the catalogue is the projection job's finding.

Scoped operations already available:

- `provider_credits.ensure_track_credits(db, track_id, services, artist_hints=...)`:
  fetch and store a track's provider credits from every configured credit
  source (`credit_services()`), matching it first when necessary: by ISRC at
  MusicBrainz, under the album's artist, then under each hint (the parts of a
  combined artist)
- `native_enrich.enrich_native_entity_all_services(conn, type, id, services=...)`:
  ingest provider identities for one entity
- `native_enrich.heal_combined_artist(conn, artist_id, names, lead=...)`: the
  one catalogue split of a combined-credit artist (locks, hand-set names,
  watches, aliases); returns the track and album ids it changed. The caller
  owns the transaction
- `RepairWorker.request_projection(track_ids, retag=..., reorganize=...)`:
  queue the projection jobs scoped to those tracks' files
- `run_job_now(job_id, scope={'file_paths': [...]})`: a scoped run of any
  job that declares `supports_file_scope`

Split Artists is the reference: it scans library artists, decides with the
credit kernel (stored credits, the credit fetcher on request, album anchors,
identified artists, keep-whole names), heals through `heal_combined_artist`,
and requests Re-tag and Reorganize for the affected files. It has no tag
codec, no file mover and no provider search of its own.

**One job splits an artist: Split Artists.** No importer, enrichment pass
or other healer splits a combined artist. One that meets a name reading as a
list hands it over, `RepairWorker.request_split(artist_ids)` (or
`repair_worker.request_artist_split` outside a job), the way healers hand
files to Re-tag and Reorganize: the reconcile, the local credit pass, the
collaboration ghost clean-up and the step after a download all do. The import
keeps a credit as written. Every job keeps its own competence and asks the
owner of another one.

**Split Artists splits only what the providers confirm.**
`native_enrich.split_confirmed(conn, artist_id, parts)` (kernel:
`artist_credit.credits_confirm`): every part is credited on one of its tracks
by a stored provider list, so the track exists under each of them there.
Names (a ";", the album's artist inside the name, parts the library knows)
only propose a split. A split it cannot confirm is a finding the user
confirms on the finding itself; Fix All and live mode leave it alone.

**Credit sources** are the providers whose track lookup gives a real artist
list with ids: Spotify, Deezer, MusicBrainz, Tidal, Discogs, JioSaavn
(`provider_credits.CREDIT_SOURCES`). Each client reads its own format in
`track_artist_credits(track_id)` into `[{"id", "name"}]`. A source that only
has a string ("A & B") or one name is not evidence of who is on a track. A
one-artist answer is stored too: it is what keeps a band whole. The lists
come in with the data: a download keeps the list its source gave (two or more
artists with ids; a search result often names only the main artist), every
enrichment worker keeps the list of a track it matched
(`provider_credits.keep_match_credits`), and the credit fetcher asks for the
rest on request.

When a needed scoped operation is absent, extend its existing owner rather
than create another tag writer, rename planner, provider search or identity
rule inside a job. **A new bug gets a general correction to the owning layer
and a regression test, not another caller-specific exception.**

## Why

- **One truth, one answer.** When a tool asks a provider on its own, the
  library page shows one value and the tool acts on another. The reorganize
  planner did this until it moved files after a live tracklist and had to be
  patched three times (`_keep_user_casing` twice, `_keep_user_year`) to keep
  the user's own values. It now reads the catalogue and nothing else.
- **What one reader learns, every reader has.** A credit list the Split
  Artists job made the writer fetch is a stored snapshot: the artist pages,
  the healers and the next job run read it without asking again.
- **The user's values win.** Writers respect locks
  (`locks.blocks_entity`) and readers read through `project_metadata`, so a
  hand-set title or name is the one on disk and on the page.
- **Offline and fast.** A plan, a preview or a scan that only reads the
  catalogue needs no network and cannot time out on a provider.

## One question, one kernel

The same question gets one function, used everywhere. Before writing a helper,
search for the kernel. The ones that exist:

| Question | Kernel |
|---|---|
| is this artist string one act or several? | `core/matching/artist_credit.decide_credit` |
| every name a credit could hold (for comparing) | `core/matching/artist_credit.split_names` |
| what credits does the library hold for a track? | `core/matching/artist_credit.stored_track_credits` |
| where does this file belong? | `core/imports/paths.build_final_path_for_track` |
| what is the value the user sees? | `core/library2/metadata_overrides.project_metadata` |
| which provider id does this entity have? | `core/library2/worker_support.stored_provider_id` |
| are these the same recording? | `core/library2/importer.dedup_title_key` |
| what tags does this file get? | `core/tag_writer` (via `core/library2/retag.py`) |

A kernel takes what the caller knows as arguments (evidence, names the context
vouches for, lookups) and returns a decision with the step that made it. Each
caller passes its knowledge in; it does not re-implement the rules.

## Evidence before names

When a decision can be made from data or from a name, data wins. Such a
decision is one chain of steps, most trusted first, and the first step that
knows decides:

1. what a provider recorded for this entity (a stored credit list, an id)
2. what the writer stores for it on request (scoped, as above)
3. what this context vouches for (the album's artist, the artist a download
   was for)
4. what the library knows (an identified artist, a known act)
5. a weaker name check, only where the caller opts in
6. a lossless default: when nothing knows, nothing is changed

Step 6 matters: an unknown "A & B" stays one credit. A later match can still
split it; a wrong split leaves artists behind that nothing can merge reliably.

Separators describe syntax, not identities. Before cutting, preserve complete
artist spans backed by stored provider credits or contextual names. For
example, a partial credit list naming "Boy Meets Girl" protects that entire
name inside "Boy Meets Girl feat. X", even though `meets` is a separator.
The whole string and each smaller piece run through the same recursive
decision function. There is no separate pre-cut decision engine. After a
cut, every remaining piece goes through the same evidence checks, including
the same optional weaker name checks. A stored artist span remains intact
when embedded in a longer credit; tests cover that property across separators.
The names in a proposed split must have actual separators between them:
"Nova" and "Vale" do not explain "NovaVale".

A catalogue row's existence is weaker than a contextual anchor: an old
"Drake feat. Rihanna" row must not hide Rihanna when the album context names
Drake. A provider credit explicitly naming the whole string as one artist
still takes precedence. Keep both cases covered by regression tests.

## Existing migration boundary

This document defines the required architecture, not a claim that all older
jobs already comply. These still detect from files, call providers from the
scan, or write tags / move files themselves; each needs its detection moved
to the catalogue as well as its fix, or it reports the same subject again:

- Album Release Year: detects from folder names and date tags, writes date
  tags with its own codec, renames the album folder itself. The original-date
  tags it writes (#2053) have no catalogue field yet, so Re-tag cannot project
  them
- Track Number Repair: detects from file tags, fetches tracklists in the scan,
  writes the number tag and renames files itself
- AcoustID mismatch "retag", MBID mismatch, Unknown Artist Recovery, Metadata
  Gap: correct the catalogue and then write tags themselves
- Untagged Downloads: right for files outside the library (staging); a
  library file belongs to Re-tag

Path Mismatch is Reorganize's own finding and stays its writer.

## What a writer stores

- Everything it learns, including answers that change nothing ("this track is
  credited to one artist", "nothing found"), so a reader never makes it ask
  twice. `lib2_credit_checks` holds such answers with an age after which they
  are asked again.
- With its provenance: which provider, which id, when.
- Never over a user's value or a lock.

## Checklist for a change (people and agents)

- [ ] Does the code read lib2, or does it call a provider? A catalogue reader that calls
      a provider directly must delegate to the fetching layer.
- [ ] Is there a kernel for this question? Use it; extend it if a case is
      missing, with a test.
- [ ] Is a missing piece of data fetched by a scoped writer, stored, and read
      again?
- [ ] Is a negative answer stored, so the next run doesn't ask again?
- [ ] Does the user's value (override, lock) still win?
- [ ] Does a healer commit catalogue changes before delegating file work?
- [ ] Is the projection optional, scoped, lock-aware and retryable?
- [ ] Is the root cause fixed in its owning layer rather than patched per caller?
- [ ] Is a reported case added to that kernel's test corpus
      (`tests/matching/test_artist_credit_corpus.py` for credits)?
