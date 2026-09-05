# codex handoff: 30s preview clips getting filed as real tracks

review this against the original problem. i am handing off a fix i believe is
correct but have not fully validated. the unverified list at the bottom is the
part worth your time.

## the original report

jadux, in discord:

> soulsync is making a lot of corrupted files it's crazy
> it used to work great but about a quarter of the new files are corrupted
> just noticed it's corrupted just after the 30s cap
> probably an issue from tidal downloads

he does NOT use hifi. boulder's first read was the known hifi preview problem,
but jadux ruled that out. later:

> my tidal account subscription has ended, maybe that's the issue. Either way,
> downloading the file without verifying it is bad practice, we shouldn't have
> to use the "Preview Clip Cleanup" tool

he is right on the principle. that cleanup tool is the rescue we built AFTER the
hifi incident. needing it again means we patched a symptom at one source instead
of the gate.

## what his log proves

log is `Issue/app (1).log` (24MB, his, 2026-09-02 to 09-04). the track he named
is "Lodge" by thirdeye.

evidence, computed from the `[Integrity] ... passed (size=Xb, length=Ys)` lines:

- 246 unique flac files
- correlation between track LENGTH and file SIZE: **-0.01**
- median size ~3MB whether the track is 90s or 450s
- median implied bitrate 163 kbps. real flac is 500-1100.
- 3MB of flac at a normal lossless bitrate is almost exactly 30 seconds

so every file holds about the same slug of real audio, padded out to whatever
runtime the metadata claimed. that is jadux's "corrupted just after the 30s cap".

source confirmed tidal, not hifi:

```
[Tidal] 1/50 candidates passed validation (best: 0.99 'ThirdEYE - Breathe')
Downloading from Tidal: 555799800||ThirdEYE - Breathe
```

matched at 0.99 confidence. his expired subscription is the mechanism: tidal
serves previews to non subscribers.

note: **it is not "about a quarter", it is ~95%** of his flacs. worth telling
him. the 12 files that are fine sit at 433-816 kbps and their sizes scale with
length normally.

## why nothing caught it

`check_audio_integrity` in `core/imports/file_integrity.py` already runs on
every download from every source, and quarantines on failure. i was wrong when i
first said only hifi checked. the legs are:

- size: 3MB is not "too small". passes.
- mutagen parse: a perfectly valid flac. passes.
- duration agreement: **passes**, drift 0.0008s. the container HONESTLY declares
  the full length.
- the decode leg: only fires when the header reports length **0**.

that last one is the whole story. hifi's previews left `total_samples=0`, so we
taught the shared check to decode when it sees that zero (sella's incident, the
comment names it). tidal's header is internally consistent, so the decode never
ran.

the guard was keyed to the fingerprint of one source's lie instead of to the
property we care about: does this file hold the audio it claims.

## what i changed

`core/imports/file_integrity.py`

- `LOSSLESS_MIN_DENSITY = 0.30`, `_LOSSLESS_TYPES`, `_LOSSLESS_MP4_CODECS`
- `is_lossless_audio(audio)` - new
- `raw_pcm_bitrate(sr, bits, ch)` - new
- `is_fake_lossless_bitrate(...)` - MOVED here from hifi_client (see below)
- `_confirm_broken_audio(path)` - new, lazy imports `detect_broken_audio`
- new check inside `check_audio_integrity`, plus a fallback in the existing
  zero length branch
- renumbered the duration comment to "Check 4" (there were two "Check 3"s)

`core/hifi_client.py`

- deleted its local `is_fake_lossless_bitrate`, now re-exports the one in
  file_integrity. two copies of that predicate would drift. its 19 tests pass.

`tests/imports/test_lossless_density_guard.py` - new, 52 tests.

## the design, and why it is shaped this way

**density is a TRIGGER, not a verdict.**

```
lossless file, thin for its runtime?          <- free, just arithmetic
   -> detect_broken_audio(): ONE decode       <- authoritative
        truncated OR mostly silence -> reject
        audio is all there          -> accept, log it
```

the first version i wrote rejected on density alone. that was wrong and it is
the single most important thing for you to check i actually fixed. real quiet
music compresses just as hard as a fake. measured on real encodes:

| file | density | complete? |
|---|---|---|
| quiet ambient | 6.5% | yes |
| pure tone | 7.0% | yes |
| very soft pink noise | 24.7% | yes |
| padded 30s preview | 10.0% | **no** |

rejecting on the number alone quarantines someone's ambient album. i only found
this because i went looking for the file that breaks it. my earlier "0/30 false
positives against a healthy library" was worthless as validation: boulder's
control set is pop and rock, which never compresses that hard, so it could not
contain the counterexample.

**why detect_broken_audio and not probe_decoded_duration.** a silence padded
preview DECODES TO ITS FULL LENGTH. measured: the fake decodes to 120.0s. so
duration based decoding cannot catch this shape at all. only silencedetect can.
`detect_broken_audio` chains astats + silencedetect in one pass and catches both
shapes a fake takes.

**fails open, except for the zero-length fallback.** on the normal thin-file
path, no ffmpeg means no authoritative confirmation, so the file is accepted.
quarantining a real album because a tool is missing is worse than missing a
fake. the older zero-length-header path is different: when the header reports no
runtime, the check has an expected duration, and decoded duration is unavailable,
it may still reject on density because there is no file runtime to confirm
against. jadux's install has ffmpeg (677 confirmations in his log), so the
authoritative decode path should protect him.

**cost.** a healthy library triggers almost nothing, so almost nothing decodes.
this is why it can be on by default where the existing opt-in audio guard could
not be.

## what i verified

- 52 tests. every guard negative checked: broke each one, confirmed the test
  fails, restored. 9 sabotages, all fail correctly.
- real encoded files end to end: padded flac + padded alac rejected. ambient,
  tone, soft, normal flac, 128k mp3, 160k aac all pass.
- both environments: 52 pass with ffmpeg on PATH, 39 pass + 8 explicit skips
  without. the skips are marked with `_GUARD_FFMPEG`, separate from the
  `_FFMPEG` used to build fixtures, so a reject test cannot silently skip and
  report green.
- replayed the shipped predicate over both logs: 234/246 of jadux's caught,
  0/30 on boulder's healthy set.
- `tests/imports/` + `test_hifi_preview_guard.py`: 965 passed (before the last
  redesign, see below).

## what i did NOT verify. attack these.

1. **i have never run this on one of jadux's actual files.** the reproduction is
   synthetic and silence-padded because that is what his numbers imply. if his
   files are truncated rather than padded, detect_broken_audio still catches it
   (it checks both) but i am inferring his shape from a log. getting one real
   file from him would settle it.
2. **my "quiet music" fixtures are synthetic.** sine waves and pink noise, not
   real ambient records. real sparse music has more entropy than a pure tone so
   it should trigger LESS, but i have not tested a real quiet album. this is the
   false positive risk and it is the one that destroys user data.
3. **the full suite has not run on this version.** i started it twice, both
   times on code i then rewrote, and killed both. needs a clean run.
4. **cost is unmeasured.** `check_audio_integrity` runs on EVERY download and
   can now decode. healthy library: near zero. jadux: 95% of files would
   trigger a decode. i have not timed that.
5. **the 0.30 threshold** is measured against two libraries, both mine to pick.
   broken topped out at 28.7%, genuine bottomed at 37.2%. that gap may not hold
   for classical, spoken word, or field recordings.
6. **`is_lossless_audio` matches on `type(audio).__name__`.** works with
   mutagen, but a name typo fails silently rather than raising. there is a test
   that every name is a real mutagen class, but the format LIST could still be
   incomplete.

## mistakes i made getting here, so you know where to look

- claimed "only hifi checks duration" after grepping only the client files. the
  shared check existed. wrong.
- shipped a version that rejected on density alone. would have quarantined real
  quiet music. caught only when boulder pushed me to verify properly.
- wrote `"Monkeys"` in the format list. the real class is `MonkeysAudio`. dead
  string, silently no coverage.
- put the density fallback BEFORE the authoritative decode in the zero length
  branch, so a cheap suspicion could override a real measurement.
- wrote a comment asserting "real flac lands at 40-75% of raw" as measured fact.
  false. only true for dense material.
- claimed "no decoder needed". wrong, that was the whole flaw.
- two guards passed my own negative check while broken (the MonkeysAudio name
  and the fail-open property) because i fixed the values by hand and never
  pinned them. both now have tests.

## specific things to check

1. does the fix actually address jadux's report, or only my reconstruction of it
2. can you find a real lossless file that is complete and gets quarantined
3. is `detect_broken_audio`'s silence threshold right for quiet music. it is
   `noise=-50dB, d=2s`, tuned for a different job
4. the zero length fallback: decode first, density only when decode returns 0.
   confirm a suspicion can never override a measurement, and decide whether the
   stricter no-decode behavior is acceptable for installs without ffmpeg
5. whether an always-on check should be able to shell out to ffmpeg at all
6. `is_fake_lossless_bitrate` moved modules. anything still importing it from
   hifi_client gets the re-export, but check nothing else broke

## how to run

```bash
python -m pytest tests/imports/test_lossless_density_guard.py -q
python -m pytest tests/imports/ tests/test_hifi_preview_guard.py -q
```

reject tests need a real `ffmpeg` on PATH. without it they skip loudly rather
than pass hollow.

## not in scope

this stops NEW bad files being filed. it does not repair what is already in
jadux's library. that is still Preview Clip Cleanup.
