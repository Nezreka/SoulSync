# Global Quality System — Source Binding, Ranking & UI

**Branch:** `feature/global-quality-system`
**Date:** 2026-06-14
**Status:** Approved design — ready for implementation plan

## Problem

The quality model (`core/quality/model.py`) is complete and Soulseek already
uses it, but the system has no cross-cutting quality behaviour:

1. **Streaming sources don't populate real quality.** `SearchResult.audio_quality`
   is a derived property over `format/bitrate/sample_rate/bit_depth`, but Tidal,
   HiFi, Deezer, Qobuz, Amazon, YouTube never fill `sample_rate`/`bit_depth` (and
   sometimes not even the right `format`). Their `audio_quality` therefore falls
   back to crude kbps heuristics.
2. **Ranking is inconsistent.** Only the Soulseek path runs
   `filter_results_by_quality_preference`. Streaming results are ordered by
   match-confidence only — quality is ignored.
3. **No quality-aware source fall-through.** `search_with_fallback` is
   "first non-empty source wins": it never escalates to the next source when the
   current source can't deliver the wanted quality.
4. **No UI for the v3 ranked-target list.** The profile model is v3 (ordered
   target list) but there is no editor for it.

## Decisions (locked)

- **Per-Source Population, not cross-source pooling.** Source priority (the
  hybrid chain order) stays king. Each source populates an accurate
  `audio_quality`; the chain fall-through becomes quality-aware so a source that
  cannot meet *any* target is skipped in favour of the next source.
- **Full scope:** source mappers (A) + ranking wiring + streaming-path unify,
  UI ranked-target editor (B), quarantine-reason surfacing (C), tests (D).
- **Bitrate is a settable minimum threshold (a range "≥ X"), never an exact
  match.** Lossless (FLAC/WAV) is matched on `bit_depth`/`sample_rate`; bitrate is
  only a fallback heuristic when those are absent. This is already how
  `AudioQuality.matches_target` behaves — the work is to expose it correctly in
  the UI and add a small VBR tolerance for lossy presets.

## Hard constraints (must not regress)

These are already correct in the codebase and the new work must preserve them:

- **Retry harmony.** A quality reject already flows through the same retry path as
  AcoustID: `check_quality_target` → `move_to_quarantine(trigger='quality')` →
  `requeue_quarantined_task_for_retry(..., 'quality')` (pipeline.py:584-620). The
  worker walks the next-best candidate using the per-source retry budget. New code
  must keep `check_quality_target` returning a reason string that feeds this path.
- **force_import isolation.** `force_imported` status is set ONLY by the AcoustID
  version-mismatch fallback (`core/imports/version_mismatch_fallback.py`). The
  quality guard sets NO verification status — it quarantines with
  `trigger='quality'` and the bypass flag `_skip_quarantine_check='quality'`. A
  quality mismatch must NEVER become `force_imported`. force_import stays reserved
  for AcoustID mismatches.

## A — Source mappers

New module **`core/quality/source_map.py`** centralises each source's tier
knowledge (kept out of `model.py` to avoid bloat). Each download client populates
the four quality fields (`quality`, `bitrate`, `sample_rate`, `bit_depth`) of its
`TrackResult` via these helpers; `audio_quality` then derives automatically.

Each tier value is a **claim**, verified post-download by `check_quality_target`
reading the real file. Ranking uses the claim to pick; the guard catches lies.

| Source | Source of values | Mapping |
|--------|------------------|---------|
| **Soulseek** | slskd attrs type 4 (sample_rate) / 5 (bit_depth) — real | already done (`AudioQuality.from_slskd_file`) |
| **Qobuz** | API `maximum_sampling_rate` (kHz) + `maximum_bit_depth` — real | `sample_rate = rate*1000`, `bit_depth`, `format='flac'` |
| **Deezer** | config code `flac`/`mp3_320`/`mp3_128` | flac→16-bit/44.1kHz · mp3_320→320kbps · mp3_128→128kbps |
| **Tidal** | track `audioQuality` tier | HI_RES_LOSSLESS/HI_RES→flac 24/96 · LOSSLESS→flac 16/44.1 · HIGH→aac 320 · LOW→aac 96 |
| **HiFi / Monochrome** | Tidal-backed; config quality key | same map as Tidal |
| **Amazon** | real `sampleRate` when present, else tier | UHD→24/96 · HD→16/44.1; prefer real sampleRate |
| **YouTube / SoundCloud** | yt-dlp / stream `format` + `abr` | lossy: format + bitrate, no bit_depth |

Helper shape:

```python
# core/quality/source_map.py
TIDAL_TIER_MAP: dict[str, AudioQuality]
AMAZON_TIER_MAP: dict[str, AudioQuality]

def quality_from_tidal_tier(tier: str) -> AudioQuality: ...
def quality_from_qobuz(sampling_rate_khz: float, bit_depth: int) -> AudioQuality: ...
def quality_from_deezer(code: str) -> AudioQuality: ...
def quality_from_amazon(stream_info: dict) -> AudioQuality: ...
```

Clients copy the result onto the `TrackResult` fields (small helper
`TrackResult.set_quality(aq)` to avoid four-line repetition at each call site).

## Wiring — quality-aware fall-through

New helper **`core/quality/selection.py`**:

```python
def rank_for_profile(candidates) -> tuple[list, bool]:
    """Return (ranked_candidates, satisfied_a_target).

    satisfied = filter_and_rank(fallback_enabled=False) produced anything.
    """
```

`search_with_fallback` (`core/download_engine/engine.py`) changes from
"first non-empty wins" to:

```
best_fallback = []
for source in chain:
    tracks, albums = source.search(query)
    if not tracks: continue
    ranked, satisfied = rank_for_profile(tracks)
    if satisfied:
        return ranked, albums           # this source meets a target → done
    best_fallback = best_fallback or (ranked, albums)
# chain exhausted, nothing satisfied a target
return best_fallback if fallback_enabled else ([], [])
```

**Key behavioural note:** with source-priority-king plus a long target list
(down to MP3 192), fall-through only triggers when a source meets *no* target at
all (below the floor) — e.g. user wants only FLAC and a source has only MP3.
Otherwise the first source that returns acceptable results wins, exactly as today
but now quality-ranked within.

## Streaming-path unification

In `download_orchestrator.search_and_download_best`, both paths converge on:
**match-filter first (right track), then quality-rank (best version).** The
streaming branch keeps its confidence filter (≥0.55) and then applies
`rank_for_profile` to the survivors; the Soulseek branch keeps its quality ranking
and is unchanged in spirit. No path is left quality-blind.

## B — Ranked-targets UI

A draggable, ordered list in the quality settings panel:

- Each row: drag handle, label, format, the relevant constraint
  (bit_depth + min_sample_rate for lossless; **min_bitrate as a settable "≥ X
  kbps" field** for lossy), delete button.
- "Add target" control with format/bit_depth/sample_rate/bitrate inputs.
- `fallback_enabled` toggle.
- Persists via the existing `GET/POST /api/quality-profile` (already v3-shaped).

Bitrate field is explicitly a **minimum threshold**, defaulting to small VBR
headroom for the common presets (e.g. a "320" preset stores `min_bitrate≈315` so
VBR/mono files near 320 still match). Lossless rows hide the bitrate field since
they match on bit_depth/sample_rate.

## C — Quarantine reason

Mostly wiring — the UI already carries `quarantineReason` dataset and an approve
flow (`webui/static/downloads.js`), and `check_quality_target` already returns a
"file is X, wanted Y" string.

- Ensure the quality rejection reason is threaded into the quarantine record's
  reason field and rendered in the track-detail modal (what was wanted vs what the
  file actually is).
- "Approve anyway" sets `_skip_quarantine_check='quality'` (the bypass already
  honoured at pipeline.py:584-585).

## D — Tests

- `tests/quality/test_source_map.py` — each mapper produces the expected
  `AudioQuality` (Tidal tiers, Qobuz kHz→Hz, Deezer codes, Amazon real-vs-tier,
  lossy no bit_depth).
- `tests/quality/test_selection.py` — `rank_for_profile` satisfied/unsatisfied;
  fall-through: source A below floor → engine escalates to source B; nothing
  matches with `fallback_enabled` on (best returned) vs off (empty).
- Extend `tests/quality/test_model.py` — `matches_target` bitrate-as-minimum,
  FLAC matched on bit_depth/sample_rate not bitrate, v2→v3 migration.
- `tests/imports/test_quality_guard.py` — `check_quality_target` quarantine
  reason scenarios **plus a regression test asserting a quality mismatch uses
  `trigger='quality'` and never sets `force_imported`.**

## Out of scope

- Cross-source candidate pooling (rejected in favour of per-source population).
- The Monochrome/HiFi 30-second-silence bug (tracked separately in `PLAN.md`;
  needs ffmpeg `silencedetect`, not tier mapping).
