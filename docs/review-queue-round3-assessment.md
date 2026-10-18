# Review queue: third feedback round

Scope: screenshot findings 1–23. The Operations redesign (24) is deferred.

## Decisions and application

Numbering and metadata choices are drafts. Quarantine, replacement and duplicate-keeper choices are also saved in the album plan. Choosing them does not edit audio files. The user can revise a choice, apply the album, or save a resolved plan for a later explicit batch. Saved approvals are bound to the current evidence and selected file action; changed evidence invalidates them.

The global ready-metadata action excludes physical file decisions. Applying a saved plan with physical decisions has a separate confirmation. Existing file identity, release, fingerprint and playlist guards still run when applying it.

Release pins, completeness intent and assignments update the plan and trigger reanalysis. Explicit wishlist requests still start acquisition. Reopening a historical decision is reconsideration, not reversal of an already completed file edit.

## Findings confirmed from the supplied examples

- Queen's extra file tagged “Who Wants to Live Forever” has fingerprint evidence for “Teo Torriatte (HD mix)”. The conflict is not simply its ten-second duration difference. An obsolete finding had incorrectly been presented as a reviewed decision; obsolete results now remain reviewable and current evidence is retained.
- Continuous numbering was being interpreted as an outlying local position on multi-disc releases. This explains the false review reason on “Living on My Own”, “Girl”, and “You've Got to Hide Your Love Away”. The matcher now recognizes continuous release-slot positions without weakening version checks.
- New Light's single local track belonged to a playlist. That silently implied a partial album. It now prompts for completeness, using partial ownership only as a suggestion.
- Shaman's “The Game of Love” already had `Santana, Michelle Branch` in both its local track-artist tag and database credit. The evidence view showed the narrower artist field. It now displays the track credit when available and preserves richer credits.
- Faith's reviewed duration discrepancy remains inspectable and can be reopened. Matching a recording identifier does not mean all duration discrepancies should be silently accepted.

## Other-tools overlap audit

Read-only baseline captured 2026-10-17. Joined current findings to queue cases using physical path aliases, local album IDs and file membership. These are overlaps, not proof that every proposed value conflicts.

| Other finding type | Pending | Findings overlapping queue cases | Concern |
| --- | ---: | ---: | --- |
| Recording MBID mismatch | 824 | 731 | May overwrite the queue's recording decision |
| Album MBID mismatch | 3 | 3 | May replace the selected release identity |
| AcoustID mismatch | 133 | 93 | Same audio-identity decision, different presentation |
| Track numbering | 35 | 35 | Can contradict saved numbering intent |
| Canonical version | 266 | 128 | Edition/provider selection can differ |
| Incomplete album | 127 | 71 | May duplicate or contradict completeness and wishlist intent |
| Duplicate tracks | 89 | 66 | May discard intentional repeated release slots |
| Album tag inconsistency | 9 | 8 | May compete with planned album metadata |
| BPM | 500 | 197 | Independent field, subject to normal stale-file guards |
| Genre | 159 | 39 | Independent field |
| Cover art | 11 | 6 | Usually independent; artwork edition still matters |
| Lyrics | 63 | 23 | Independent if recording identity remains correct |
| ReplayGain | 9 | 7 | Independent analysis, subject to file identity |
| Dead file / orphan / empty folder | 8 / 16 / 1 | 0 / 0 / 0 | No active-case overlap in this snapshot |

Of 128 overlapping canonical-version proposals, 97 specify a different provider/release key. Cross-provider differences alone do not prove different editions. Of 66 overlapping duplicate findings, 49 contain files mapped to distinct release slots: repeated recordings on compilations cannot be treated as disposable copies solely because their audio matches.

This round centralizes missing-track decisions in the queue while reusing the wishlist/download machinery. It does not remove the legacy tools or claim that their findings have been reconciled with queue decisions. Shared ownership of edition, acquisition and file decisions is work for the deferred Operations discussion.

## Runtime and acquisition

Applying changes also waits for reconciliation access, verifies fingerprints and release/file identity, checks playlist references, writes tags/database state, may rename files, persists progress, and reanalyzes the album. A read-only five-file FLAC fingerprint benchmark took 0.642–0.968 seconds per file, median 0.878 seconds. That alone is roughly 23 seconds for 26 files; it is not a measured total apply time. Progress reports now expose waiting, fingerprinting and application timing.

Wishlist publication is recorded before removing the wishlist entry. Publication schedules a coalesced album discovery refresh. A request remains pending until the correct release slot is actually assigned; a completed transfer alone does not establish a match. Existing media-server library-scan automation remains in place.

## Verification

File-changing workflows are exercised against temporary test libraries, not by applying the user's pending library decisions. Deployed browser checks inspect real queue data and block mutation requests. Final deployment and reconciliation results are recorded below after both cycles complete.
