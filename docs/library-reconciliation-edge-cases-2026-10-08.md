# Library reconciliation edge-case catalogue (2026-10-08)

This catalogue records the real-library cases used while designing and tuning the folder-first reconciliation tool and review queue. It is supporting evidence for the eventual pull request and a regression checklist for future matcher changes. The expected behavior is part of the specification; an individual provider title or identifier is evidence, not automatically the desired local metadata.

## Audio identity and one-to-one assignment

| Case | Failure mode | Expected behavior |
| --- | --- | --- |
| Brand New — *Deja Entendu* | Similar durations let an earlier file consume the “Play Crack the Sky” slot, producing a duplicate-looking assignment and no useful missing-track signal. | Rank viable edges using title, recording ID, duration, and AcoustID evidence, then solve a one-to-one album assignment. Never let input order decide a tie. |
| Lamb of God — *Ashes of the Wake* | Greedy duration matching can associate the right duration with the wrong title and strand the correct file. | Prefer strong title/audio identity before duration; use duration as corroboration and reject impossible pairings. |
| Radiohead — *OK Computer*, “Lucky” | A nearby-duration song could consume track 11 while “Lucky,” whose duration differs by only 0.63 seconds, remained unmatched. | Compare titles before resolving similar durations and preserve global one-to-one assignment. |
| ABBA — *Gold: Greatest Hits*, “Angeleyes” / “Angel Eyes” | AcoustID linked several MusicBrainz recording rows whose spelling differed but all supported the same release slot. | Collapse catalogue aliases that support one exact slot. Different MBIDs alone do not create a human decision. |
| ABBA — *Gold: Greatest Hits*, “Take a Chance on Me” | Equal top-score AcoustID evidence also identifies a genuinely different recording. | Keep one explicit audio-assignment question and show the competing identity. |
| Chelsea Wolfe — *The Dark* | Pre-album single copies and album copies fit the same release slots. | Present one duplicate-copy decision per recording, including playlist use and recoverable keeper actions. |
| Stone Sour — *Come What(ever) May* | One uncertain assignment, four absent bonus/acoustic tracks, numbering changes, title conventions, and safe ID repairs create a dense case. | Lead with the five album-level questions, keep missing tracks within album completeness, and show compact row details without repeating the same recommendation. |

## AcoustID catalogue behavior

- One fingerprint may link many MusicBrainz recording IDs. Deduplicate identical normalized title-and-artist identities in the UI while retaining every ID internally.
- Equal AcoustID scores are unordered catalogue links, not a ranking. Album slot, title, embedded ID, filename, recording disambiguation, and duration decide whether alternatives conflict.
- A top-score alternative that fits another release slot remains material. Alternatives that all identify the assigned slot are aliases.
- AcoustID can identify a base recording while the release supplies the useful version text. Preserve live, acoustic, instrumental, remix, radio edit, and combined-track qualifiers when the rest of the evidence supports them.
- A failed decoder or fingerprint lookup affects one file. A sustained service failure may stop new remote lookups, but cached evidence and unrelated folders remain usable.

## Release and edition identity

| Case | Failure mode | Expected behavior |
| --- | --- | --- |
| Queens of the Stone Age — *Songs for the Deaf* | Files from materially different editions were combined. “The Real Song for the Deaf” and the specific “Song for the Deaf” recording distinguish candidate releases. | Require an edition choice when tracklists or recordings differ, then recompute dependent track actions. |
| Queens of the Stone Age — *Rated R* | The folder was named “R,” and a deluxe tracklist had replaced the standard split-track edition. | Use album context and exact tracklist evidence to select or ask for the intended edition; do not treat an album-title correction as sufficient. |
| The Ocean — *Pelagial* | Instrumental and vocal discs begin with the same instrumental recording; a physical two-disc edition conflicts with a continuous digital tracklist. | Do not infer the disc from vocals alone. Prefer a fitting digital release in digital-numbering mode while retaining a manual numbering choice where the local convention differs. |
| Lamb of God — *Sacrament* | Several providers returned differently shaped releases, though one MusicBrainz release completely fit all 11 files. | Do not create a release decision when only one candidate fully fits. |
| Joep Beving — *Trilogy* | A multidisc physical release numbers each disc separately, while the desired digital convention numbers all 46 tracks continuously. | Proactively search for a fitting digital edition and present one album-wide numbering choice. |
| Katy Kirby — *Katy Kirby on Audiotree Live* | Apple reports “Katy Kirby on Audiotree Live (Audiotree Live) - EP”; Deezer repeats the same parenthetical. Removing only “- EP” proposed awkward redundant metadata. | Treat a trailing qualifier that merely repeats words already in the album title as source flavor and preserve the cleaner local title. Keep the exact provider release ID. |
| Duran Duran — *Rio* | Apple release `693584837` fits all nine tracks. The library/cache retained “Rio (Columbia House),” the current provider title is “Rio (Remastered),” and every file tag and the folder use “Rio.” | Keep the exact release identity for track matching while preserving the unanimous clean display title. Clean obsolete retailer, record-club, and pressing-plant qualifiers from the library album without pushing them into file tags. |
| The Spiritual Sound | Multiple MusicBrainz releases assign the same ten files equally. | Show the edition ambiguity and disambiguation fields; avoid pretending audio can distinguish catalogue-equivalent editions. |

## Numbering conventions and discs

- SoulSync stores disc and track numbers on files and library rows. It does not model a disc as a separate library entity.
- `digital` mode prefers one continuous sequence and disc 1 when a fitting digital release exists.
- `release` mode preserves the selected release’s per-disc positions.
- Numbering changes are one album-wide choice, never dozens of independent metadata decisions.
- A numbering action cannot hide a material title/version change on the same track.
- Conventional `Disc N`, `CD N`, and similar child folders may form one logical album when their release identities do not conflict.
- A track number such as `2.12` must not be silently rewritten to `12`, `1.12`, or `1` without the chosen convention being explicit.

## Titles, versions, and metadata flavor

| Case | Failure mode | Expected behavior |
| --- | --- | --- |
| `Song (Serenade From the Stars)` versus `Song` | Bracketed text can be harmless provider flavor or part of a distinct song identity. | Preserve existing permissive wishlist behavior when other qualifiers establish identity; treat known version markers and genuinely distinct titles as material. |
| Nine Inch Nails title examples | Bracketed/version text can distinguish recordings on the same release. | Never strip all bracketed text globally. Use recording disambiguation, duration, IDs, and release slots. |
| TOOL — *Lateralus* | Every track used `Tool`, while the selected source used `TOOL`. | Group as one ready **Artist update**, `13 tracks: Tool → TOOL`; do not label it a title convention. |
| Zero 7 — *When It Falls* | Nine tracks lacked complete featured performer credits in file and library metadata. | Group as **Featured artist credits**: “Adding missing featured artists to metadata on 9 tracks.” |
| Minor punctuation and provider wording | Typographic changes, subtitle flavor, and filename-safe punctuation can flood the queue. | Separate harmless conventions from title identity, make safe changes bulk-applicable, and name filename-only work “Rename file.” |
| Album-wide title or artist update | Identical changes appeared as one card per track. | Summarize the field and old/new value once while retaining per-track evidence below. |

## Combined tracks and split tracks

- Steve Miller Band’s “Threshold/Fly Like an Eagle” file represented two intended release slots and should be replaced with split tracks.
- Tool’s “Opiate” combined presentation may be acceptable when no split release is available; duration and release structure decide.
- Queens of the Stone Age and Deftones examples show that a combined file can be valid on one edition and wrong on another. Edition selection precedes split/combined repair.
- A combined title must not be mistaken for duplicate songs merely because one component resembles another slot.

## Compilations, soundtracks, singles, and folder grouping

| Case | Failure mode | Expected behavior |
| --- | --- | --- |
| *The Truman Show* soundtrack | Compilation tracks credited to their performers could be pulled away from the soundtrack album. | Album identity and soundtrack folder context outrank per-track performer ownership. Keep track artists separately from the album artist. |
| Matchbox Twenty, Sixpence None the Richer, Smash Mouth, Vanessa Carlton collections | Single-artist compilations were grouped or displayed as though they were various-artist releases, sometimes producing duplicate queue entries. | Keep a single-artist compilation with its artist and one physical album case. |
| Chelsea Wolfe pre-release single/EP tracks | The same recordings existed in a short pre-release and the full album. | Detect duplicate copies across the single and album context, preserving playlist evidence before a keeper decision. |
| Physical disc subfolders | Separate library album rows or damaged positions made one multidisc release appear as several albums. | Merge only when physical ancestry, normalized title, and release identity agree. Preserve conflicts for review. |
| Watchlist recovery snapshots | `.watchlist-removal-*` files looked like active duplicate library files. | Exclude recovery snapshots from active reconciliation inventory. |
| Alternate container paths | The same file appeared under stored `/music/...` and scanned `/app/Music/...` paths. | Join aliases only when the operating system proves they are the same physical file. |

## Album completeness and acquisition intent

- Missing release slots do not prove that the user wanted a complete album.
- A lone track currently referenced by a playlist is positive evidence of a partial acquisition and should not request the rest of the release.
- Without persisted intent, ask one **Album completeness** question and list the absent slots inside it.
- Once the user chooses complete, absent slots become actionable missing-track requests. Once partial is chosen, they become intentionally uncollected slots.
- Defer completeness questions while release identity, extra files, or ambiguous assignments remain unresolved.
- “Album intent” and “Missing tracks” are phases of the same concern and share one queue filter.

Examples encountered: Snow Patrol’s *Up to Now* should remain partial; the single Alicia Keys playlist track should not imply ownership of the full *Girl on Fire* release; *Amélie* needs an explicit complete-versus-partial choice when acquisition history is absent.

## Duplicates, wrong audio, and destructive actions

- Mistagged wrong audio can create a false duplicate after retagging. AcoustID identity must be checked before metadata is rewritten.
- Exact duplicates must show every local copy, quality/path evidence, and playlist membership before a keeper is selected.
- An extra file paired with a missing release slot may be a different version, not disposable junk. Show the pair as one version conflict.
- Deletion/quarantine remains recoverable and manual. Safe metadata bulk actions never include destructive work.
- Previously encountered wrong-audio examples, composer/performance attribution errors, and the Southbound mismatch motivated keeping file identity above tags and folder names in the evidence hierarchy.

## Queue and apply invariants

1. The album is the navigation and reconciliation unit; raw findings are implementation details.
2. Release/edition choice comes before numbering, track assignment, metadata, missing-track, and file actions.
3. Successful assignments remain persisted and visible so an exceptional row has album context.
4. Fully ready albums show one concise album action, with detailed evidence available on demand.
5. Each proposal names its actual field: artist, featured artist credits, album title, filename, recording ID, numbering, or title identity.
6. Provider IDs link to MusicBrainz, Apple Music, or Deezer when a stable public URL exists.
7. Applying a group revalidates evidence, file state, release pin, locks, and destination collisions. Partial failure must be reported as partial failure.
8. Accepted exceptions reopen when their audio, release, folder, or evidence signature changes.
9. A scan interrupted before completion preserves previous plans and findings; a successful scan retires stale ones.
10. The review queue must distinguish algorithmic uncertainty from presentation state. A selected row should reveal its detail without changing the underlying confidence or risk classification.
11. Headings describe the proposed outcome (“Use continuous numbering,” “Link MusicBrainz recording IDs”), not the planner's internal category or finding count.
12. The default album view is the actionable plan. The complete track audit and technical evidence remain available as separate views so dense albums do not bury the controlling decision.
13. Queue modes separate albums that need input, supported changes ready to apply, and recoverable file actions. Type filtering refines the selected mode instead of duplicating it.
