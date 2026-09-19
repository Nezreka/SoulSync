# import page rebuild

## what's there now

three tabs, three mental models, one folder of files.

- auto: a feed of worker results (imported / needs review / failed) with the
  worker's settings row (confidence, interval, quality profile, save) inline
  above it. approve and dismiss are the only actions.
- albums: search a release, pick it, get a track table with a chip pool of
  unmatched files you drag or tap onto rows. "auto-detected albums" and
  "suggested from your import folder" sit above the search box.
- singles: every file as a checkbox row with an emoji identify button that
  expands an inline track search.

the header is the page icon, a refresh button, and a staging bar with the
path, "last refreshed", and a file count.

## what's wrong with it

1. the unit of work is the wrong thing. the worker thinks in folders. the
   albums tab thinks in releases. the singles tab thinks in files. the same
   staging folder shows up in all three with no shared state, so there is no
   one place that says "here is what is in your import folder and what state
   each thing is in".
2. dead ends. a worker result that says "could not identify" or "could not
   match tracks" has no action. the fix is on the albums tab, with the files
   preselected, and nothing takes you there.
3. settings in the work surface. confidence / interval / quality profile /
   save live above the results list and get scrolled past every visit.
4. no explanation. a permission problem read as "0 files" (fixed 92b9f505d).
   a failed import shows one error string. a needs-review card shows a
   confidence number and nothing about why it is not higher.
5. the matcher is functional but reads like a prototype: "drop a file here"
   in every empty row, a chip pool at the bottom, an x button, no format or
   duration on either side, no way to see the release's other candidates
   once you've picked one.
6. the singles list has no metadata beyond title / artist / extension. no
   duration, no bitrate, no size, no "this looks like it belongs to album x".
7. history and inbox are mixed. imported and failed rows from last week sit
   in the same list as this morning's pending review.
8. the processing queue is client state. reload the page and it's gone,
   though the server jobs keep running.

## the design: one inbox, one matcher

### inbox (/import)

every staging item is a row. an item is what the worker would call a
candidate: an album folder (or a loose-file group with the same album tag),
or a single loose file. one endpoint builds it by joining the staging scan
with auto_import_history on folder_path.

each row: art (once identified), album / artist or filename, N files ·
format · total duration, a status pill, confidence bar, and the actions that
status earns:

| status            | meaning                                  | actions              |
|-------------------|------------------------------------------|----------------------|
| waiting           | seen, worker has not got to it (or off)  | identify, import now |
| identifying       | worker on it                             |                      |
| needs review      | 70-90% match                             | approve, fix, dismiss|
| needs identify    | worker gave up or < 70%                  | identify, dismiss    |
| importing         | pipeline running (live track x/y)        |                      |
| imported          | done, files gone from staging            | (history)            |
| failed            | pipeline error                           | retry, identify      |

filters as pills: needs attention (default) / all / history. history is
imported + failed + dismissed, collapsed by default.

header: title, then a status strip: import folder path, N items · N files ·
size, worker on/off switch with "next scan in 40s", scan now, and a gear
that opens the settings drawer (confidence, interval, quality profile,
auto-process). unreadable folders show as a notice under the strip.

bulk: select rows, approve all / dismiss all / import selected.

### matcher (/import/match/$itemKey)

picard's layout, which is the one everyone already knows:

- left: the release. hero (art, title, artist, year · label · format ·
  country · N tracks · source badge). under it "other candidates" as a
  compact list you can click to swap, and a search box with the source
  picker for when none of them is right.
- right: the track table. one row per release track: number, title,
  duration, then the file matched to it with its own duration / format /
  bitrate and a confidence bar. mismatched duration tints the row. unmatched
  files in a pane under the table; drag or tap to assign, same as now, with
  a real drop target instead of "drop a file here" text in every row.
- footer: "import 11 of 12 tracks", with what happens to the 12th spelled
  out (stays in staging).

singles use the same page in single mode: the left side is track candidates
instead of release candidates.

### processing

the queue moves server side. jobs already exist (import/album/process
returns a job id); the inbox row shows importing with live progress from
the worker's active_imports, and the page survives a reload.

## backend

- GET /api/import/inbox: staging groups + loose files joined with
  auto_import_history by folder_path; carries status, confidence, art,
  match_data summary, per-item file list with duration / bitrate / size.
- POST /api/import/inbox/<key>/identify: seeds the matcher (returns the
  worker's identification candidates for the item, so the matcher opens
  with a real list instead of an empty search).
- existing: album/match, album/process, singles/process, auto-import
  approve / reject / settings / scan-now all stay.

## what does not change

- the core pipeline. both the worker and the manual routes already end in
  post_process_matched_download; the rebuild is the surface over it.
- re-identify (#889) hints, reassign, quarantine.
- the guided tour ids get remapped, not dropped.

## order

1. inbox endpoint + types (tests on the join and the status derivation)
2. inbox page, replacing the three tabs; old routes redirect
3. matcher page, album mode
4. matcher single mode
5. settings drawer, server-side queue, tour ids, mobile pass

## phase 2 (shipped 0548a5f08)

what a big-company importer has that the inbox did not:

- upload from the browser. drop files or folders on the inbox; folder
  names kept. POST /api/import/upload, one request per file.
- before it imports. the matcher shows destination path + tag diff per
  track from the pipeline's own builders (POST /api/import/album/preview,
  create_dirs=False), and badges tracks the library already has.
- told when it needs you. import_needs_attention automation trigger.
- bulk import waiting singles from tags. keyboard j/k/x/a/d/enter.

also shipped after: identify by fingerprint (POST /api/import/fingerprint,
a button in both matcher modes), and the upload now goes in 8 MB pieces
(POST /api/import/upload/chunk) so a proxy body cap cannot refuse it.

still on the list, not built:
- undo an import (move back to staging, drop the rows) within a window.
  destructive; wants a live-tested session, not a blind one.
- a per-item timeline (seen / identified / imported with times)
- cover art choice in the matcher (embedded vs source)
