registerDocsSection({
    id: 'sample-studio',
    title: 'Sample Studio',
    icon: '🎛️',
    pages: [
        {
            id: 'studio-overview',
            title: 'Overview',
            lede: 'Sample Studio turns your library into a sample pack: chop any track on a waveform, pitch it, tempo-match it, split it into stems, and save the keepers to your stash.',
            body: `
## What it is

Sample Studio lives at **Sample Studio** in the sidebar. It has three columns:

- **Library** — search your tracks, filter by quality, tempo, and length.
- **Editor** — the waveform: set in/out points, loop, zoom, pitch-shift, tempo-match, and separate stems.
- **Stash** — every chop you save, with playback, delete, and one-click ZIP export.

Pick a track on the left and it opens in the editor. The first time a track is opened, SoulSync analyzes it in the background — BPM, beat grid, and chop-friendly onset points appear on the waveform when ready.

## The workflow

1. **Pick** a track from your library.
2. **Chop** — drag the amber in/out handles, or press \`I\` and \`O\` at the playhead. Loop the region to audition it.
3. **Shape** — shift pitch ±12 semitones, or type a target BPM to tempo-match the chop. The preview renders in seconds.
4. **Save** — name it, tag it, pick WAV 16/24 or FLAC 24. The chop is stored as both a rendered audio file *and* a lightweight bookmark, so the stash survives even if files move.
5. **Export** — download the whole stash as a ZIP whenever you want to move chops into your DAW.

## Keyboard shortcuts

Press \`?\` in the editor for the full list. The essentials: \`Space\` play/pause, \`I\`/\`O\` set in/out, \`+\`/\`-\` zoom.
`,
        },
        {
            id: 'studio-stems',
            title: 'Stem Separation',
            lede: 'Split any track into drums, vocals, bass, and everything else with Demucs, then chop from a single stem.',
            body: `
## How it works

Open a track, scroll to the **Stems** panel under the editor, and hit **Separate stems**. SoulSync runs Demucs on your server — about a minute for a full song — and caches the four stems permanently. You only ever separate a track once.

## What you can do with stems

- **Play all** with live **solo** (S) and **mute** (M) per stem, mixed in your browser.
- **Chop from a stem** — tap a stem name and the whole editor switches to it: the waveform, the audio, the pitch/tempo preview, and the saved chop all come from that stem. The stash bookmark records which stem a chop was cut from.
- Tap the selected stem again (or **Back to full mix**) to return to the original track.

## About the model

Demucs weights are **not bundled** with SoulSync — on first use your server downloads the model (about 80 MB) once and keeps it. The weights are free for non-commercial use, which covers SoulSync. Separation runs on CPU by default; it is slower than GPU but needs no special hardware.

If separation fails, the panel shows the error with a **Try again** button — your track and stash are never touched.
`,
        },
        {
            id: 'studio-stash',
            title: 'The Stash',
            lede: 'Every saved chop keeps its rendered file and a bookmark of how it was made.',
            body: `
## What's stored

Each stash entry has a **name**, **tags**, the source track, the in/out times, pitch and tempo settings, the output format, and — if you chopped from a stem — which stem. The rendered WAV/FLAC file sits next to it on disk.

## Managing the stash

- **Play** any entry right in the panel to audition it.
- **Delete** removes both the file and the bookmark.
- **Export ZIP** downloads every chop at once — the fastest way to get chops into your DAW or sampler.

Temporary audition previews are cleaned up automatically after an hour; saved chops are kept until you delete them.
`,
        },
        {
            id: 'studio-folders',
            title: 'Sample Folders',
            lede: 'Choose where saved chops live — one folder or many, organized with your track metadata.',
            body: `
## Where chops go

Saved chops are rendered into your **sample folders**, configured under Settings → Library → Folders → Sample Studio Folders. You can have as many as you like — the first is the default, and the save dialog lets you pick a different one per chop (drums here, melodies there, a folder per project).

## Organization

Inside the destination folder, chops are filed with the **sample path template** (Settings → File Organization), using the source track's artist, title, and album plus your chop name — for example \`Artist/Track - My chop.wav\`. Every rendered file also carries its own tags: the chop name as title, the source artist, a comment with the source track and settings, and the cover art when the source track has it. The files are self-describing in any DAW or file browser.

Sample folders are separate from your music library on purpose — library scans never pick chops up as albums. And files never move on their own: removing a folder from settings doesn't touch existing chops.
`,
        },
    ],
});
