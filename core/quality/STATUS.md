# Quality-Profile Pipeline Modularization — Status

## What this is

Before this work, exactly ONE global setting (`preferences.quality_profile`)
plus several separate global toggles (`acoustid.require_verified`,
`lossy_copy.downsample_hires`, `post_processing.audio_completeness_check`,
`import.replace_lower_quality`, `lossy_copy.*`, `import.folder_artist_override`)
governed every download/import in the app, for every item, all the time.

Quality profiles turn that into the single, app-wide, **named**,
**per-item-assignable** unit of configuration: a wishlist item (or a
per-context override like Auto-Import) points at a specific `quality_profiles`
row instead of the pipeline consulting a global setting.

## Roadmap: where this fits

**End goal.** A Lidarr-style library manager where an artist, album, or track
can be assigned its own quality profile — "keep everything by this artist at
FLAC-or-better", "this compilation just needs 320kbps MP3s", "auto-upgrade
this album's tracks until lossless" — all enforced by the *same* download/
import pipeline every other acquisition path already uses, with no special
casing per entity type.

**Starting point.** Before this work, none of that was possible even in
principle: ONE global setting decided quality for literally everything, for
every user, all the time. There was no unit of configuration to *assign* to
an entity in the first place.

**What this PR delivers, standalone.** The unit of configuration itself:
named, user-manageable `quality_profiles` rows, a live-resolution pipeline
architecture (every stage asks "what does this item's assigned profile say
*right now*", nothing is a stale snapshot), and — as the first concrete proof
this generalizes beyond "one item, one profile" — Auto-Import can already be
pointed at its own profile, independent of the app-wide default used by
normal downloads/Wishlist items. That's real, usable value today even if
nothing else ever gets built on top: users who want stricter rules for what
Auto-Import accepts vs. what they manually download already get that, right
now, with zero other changes.

**Why it doesn't block or presume the future work.** `quality_profiles` is
just rows with an id. A future entity-assignment layer (per artist/album/
track) only ever needs to store a `quality_profile_id` somewhere and call
`load_profile_by_id()` — exactly what Auto-Import already does. Nothing here
hard-codes an assumption about what that future feature looks like, so it can
be designed, reviewed, and merged entirely on its own timeline without this
work needing to change.

## What's in a profile

Every field the Settings → Quality page exposes lives on the `quality_profiles`
row (see `core/quality/schema.py::QUALITY_PROFILES_DDL`):

- `ranked_targets` — the priority ladder of acceptable qualities.
- `fallback_enabled` — accept a file matching none of the ranked targets
  rather than rejecting it (walking the ladder itself is always-on; this only
  decides what happens when nothing on it matches).
- `search_mode` / `rank_candidates_by_quality` — how download candidates are
  ordered.
- `upgrade_policy` (`acceptable` | `until_cutoff`) plus
  `upgrade_cutoff_index` — whether the Quality Upgrade jobs treat "meets any
  ranked target" as done, or keep proposing upgrades until the selected ranked
  target is reached.
- `acoustid_required` — AcoustID verification STRICTNESS for this profile:
  when on, a track AcoustID runs on but cannot confirm is quarantined instead
  of imported with the "unverified" badge. It is not an on/off switch for
  running the check — whether AcoustID is enabled/configured at all stays a
  true global capability (like a Connections credential), and skipping the
  check entirely remains an explicit per-download user action. (An earlier
  iteration wrongly treated `acoustid_required=False` as "skip AcoustID
  entirely", which would have silently disabled FAIL-quarantine protection
  for every wishlist download after migration — caught and fixed in review.)
- `downsample_enabled`, `deep_audio_verify`, `replace_lower_quality`,
  `lossy_copy_enabled`/`codec`/`bitrate`/`delete_original`.
- `folder_artist_override` — use the top Staging folder as the artist.
  Auto-Import-only (it never affected normal downloads/Wishlist imports).

There is deliberately no "run the quality check at all" master toggle: a
profile with an empty `ranked_targets` list (or `fallback_enabled=True`)
already means "accept anything" — a standalone toggle would just be a second
way to say the same thing. An earlier pass added exactly that toggle
(`quality_filter_enabled`) before this was noticed; it's since been removed
via a real `ALTER TABLE ... DROP COLUMN` migration (`core/quality/schema.py`'s
`_DROPPED_COLUMNS`), not just left dormant.

## How the pipeline actually uses it (the important part)

A wishlist row carries exactly **one** quality-related column:
`quality_profile_id` — a pointer, resolved once at insert time
(`MusicDatabase.add_to_wishlist`, falling back to the app-wide default
profile when unset). It is **not** a snapshot of the profile's settings.

Every pipeline stage that needs to know "what does this item's profile
actually say right now" calls `core/quality/selection.py::load_profile_by_id(profile_id)`
**live**, at the moment it needs the answer:

1. **Search / candidate ranking** — `core/downloads/task_worker.py::_candidate_ordering()`
   reads `track_info['quality_profile_id']`, resolves the profile live, and
   ranks candidates by its `ranked_targets`/`search_mode`.
2. **Import quality gate** — `core/imports/guards.py::check_quality_target()`
   resolves the profile live and judges the file against its
   `ranked_targets`/`fallback_enabled`/`downsample_enabled`.
3. **Every post-processing step** — `core/imports/pipeline.py::_resolve_context_quality_profile()`
   resolves the profile ONCE per file (cached on the context) and each step
   reads its own setting from it: the deep-audio ffmpeg verify
   (`deep_audio_verify`), AcoustID strictness (`acoustid_required` —
   unverified → quarantine when on), replace-lower-quality on collision
   (`replace_lower_quality`), hi-res downsampling (`downsample_enabled` →
   `file_ops.downsample_hires_flac`), and the lossy copy
   (`lossy_copy_*` → `file_ops.create_lossy_copy`). The legacy global config
   key is only a fallback for the rare case profile resolution itself fails.
4. **Quality Upgrade repair job** — `core/repair_jobs/quality_upgrade.py`
   judges the whole scan against the app-wide default profile (no per-track
   override on this branch — see "Known gaps" below).
5. **Auto-Import** — `core/auto_import_worker.py::_process_matches()`
   resolves its assigned profile (`auto_import.quality_profile_id`, falling
   back to the app-wide default) once per batch for
   `folder_artist_override`, and injects `quality_profile_id` into each
   file's context so stages 1–3 above enforce the same profile.

Because every stage resolves live instead of trusting a frozen copy, editing
a profile takes effect immediately for every item assigned to it — there is
no separate cache/snapshot to go stale. (An earlier version of this work also
denormalized `acoustid_required`/`fallback_allowed`/`downsample_enabled` onto
the wishlist row at insert time; two of those three turned out to be dead
code — never read by the gate, which already resolved live — and the third
was the one place a stale snapshot could actually drift from a later-edited
profile. Removed in favor of the single-pointer design above.)

**Both sync directions with the Settings page are covered.** The Settings →
Quality toggles are stored as global config keys (like every other setting on
the page), but the pipeline enforces the profile row. Applying a profile
pushes its values into the config (`apply_quality_profile_to_settings`), and
every settings save that touches a quality-owned section pushes the config
values back into the active default profile row
(`sync_default_quality_profile_from_config`, called from the settings-save
endpoint) — so "the page edits the active profile" holds in both directions
and neither store can drift from the other.

## Per-context precedent: Auto-Import

Auto-Import (Settings → Import) can be assigned its own profile, independent
of the app-wide default used by normal downloads — the first concrete proof
that different *contexts*, not just different wishlist items, can run under
different rules. The mechanism is generic: inject `quality_profile_id` into
whatever `track_info`/`context` dict already flows through
`check_quality_target`, same as a wishlist row does. Adding another
per-context override later (e.g. a different automation) needs nothing new —
just its own config key and the same injection.

## Managing profiles

Every profile, including the two starter ones (seeded as "Balanced" /
"Upgrade until top quality" — the one-time migration renames the default row
to "Default" so it reads as "your carried-over settings", not a factory
preset nobody chose), is fully user-manageable — renamed, edited-in-place, or
deleted. Nothing is permanently protected. Two guards keep the app always in
a valid state instead of relying on hard-coded protected ids:

- Deleting the current app-wide default auto-promotes another remaining
  profile to default first, so the app is never left without one.
- Deleting the *last* remaining profile is refused — there must always be at
  least one to fall back to.

Deleting a profile also cleans up its references (Lidarr-style, but with
fallback semantics instead of refusing): wishlist rows pointing at it are
re-pointed to NULL ("use the default") in the same transaction, and a
matching Auto-Import override is cleared. Even a reference missed by that
would safely fall back to the default via `load_profile_by_id()`.

Settings UI: a detached, sticky, always-visible side panel next to the 4
Quality tiles (mirrors the Downloads page's filter-pills-left /
batches-panel-right layout), not a strip sitting above them — every tile on
that tab visibly belongs to whichever profile is active in the panel. On
wide screens the panel protrudes to the right WITHOUT taking any width away
from the tiles; the tiles stay on the same centered Settings column as every
other tab.
All actions are inline (rename/create swap a label for a text input in
place) or the app's own themed confirm dialog; no native
`prompt()`/`confirm()` popups.

## Known gaps / deliberately deferred here

- **No per-artist/album/track profile assignment UI yet.** The `tracks` table
  now carries its own `quality_profile_id` pointer (same nullable, NULL =
  "use the app-wide default" design as `wishlist_tracks`; existing library
  tracks are backfilled to the migrated default profile so nothing is
  silently reset to factory targets on upgrade), and both the Quality Check
  scanner and the Quality Upgrade Finder resolve it per track (cached per
  distinct profile id, falling back to the default). What's still missing is
  purely the UI to actually set a *different* profile on a specific artist/
  album/track — until that exists every track's pointer stays whatever the
  migration backfilled or NULL for anything added since, so in practice every
  track is judged against the same default profile today. A full Library
  Manager assignment surface is the natural next step once this branch lands.
- **This branch was extracted from a larger internal effort** (an
  experimental, not-yet-proposed Library Manager v2) specifically so it could
  be reviewed and merged on its own merits without that larger, riskier
  feature riding along. `quality_profiles` is designed so a future
  entity-assignment layer (per artist/album/track, or anything else) can
  point at these same rows without any change to the pipeline described
  above — the pipeline only ever needs an id.

## Verification

Full `pytest tests/` suite green. `oxlint --type-check` on the React sources:
0 errors. Verified via a real Docker build + boot against live data:
the schema cleanup migrations (`DROP COLUMN quality_filter_enabled`, the
three dropped wishlist snapshot columns) ran cleanly on a database that had
been through every intermediate version, every existing wishlist row kept its
profile pointer, the profile row stayed consistent with the live config
(no drift), and the Settings UI / Auto-Import picker /
`/api/quality-profile/custom` endpoints all work — with no leftovers from
the earlier (rejected) above-the-tiles banner design in the rendered HTML/JS.

The upgrade migration specifically has its own end-to-end test
(`tests/quality/test_migrate_to_profiles.py::test_migration_output_is_actually_consumed_end_to_end`)
that goes further than checking the migration's own output in isolation: it
simulates a real pre-existing install (custom ranked targets + AcoustID/
downsample/deep-verify settings via the legacy preferences singleton and
config), runs the real migration, then calls the REAL (unmocked)
`load_profile_by_id()` — the exact function every pipeline stage calls — and
confirms it returns the migrated settings, that a pre-existing Wishlist row
gets backfilled to point at that same profile, and that Auto-Import (with
nothing configured, i.e. every existing install right after upgrading)
resolves to the *identical* profile through the *identical* code path. In
other words: on upgrade, a user's real prior settings become the one default
profile, and both the Wishlist and Auto-Import automatically inherit it with
zero extra configuration required.
