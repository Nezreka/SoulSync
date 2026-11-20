/**
 * The Library tab's hero sentences (settings-bic phase 4), from sample
 * settings: what each pill and kind reads back.
 */

import { describe, expect, it } from 'vitest';

import {
  audiobookFoldersSentence,
  audiobookImportSentence,
  audiobookNamingSentence,
  audiobookTagsSentence,
  type HeroPart,
  listWords,
  musicFoldersSentence,
  musicImportSentence,
  musicNamingSentence,
  musicTagsSentence,
  pathWords,
  podcastFoldersSentence,
  podcastImportSentence,
  podcastNamingSentence,
  subtitlesSentence,
  templateWords,
  videoFoldersSentence,
  videoImportSentence,
  videoNamingSentence,
  videoTagsSentence,
} from './words';

const read = (parts: HeroPart[]) => parts.map((p) => p.text).join('');
const warned = (parts: HeroPart[]) => parts.filter((p) => p.kind === 'warn').map((p) => p.text);
const named = (parts: HeroPart[]) => parts.filter((p) => p.kind === 'name').map((p) => p.text);

describe('the Library heroes', () => {
  it('Folders: where downloads land and where things go, the install default in amber', () => {
    const music = musicFoldersSentence({
      download: './downloads',
      transfer: './Transfer',
      installDefault: true,
    });
    expect(read(music)).toBe(
      'Downloads land in ./downloads and finished music moves to ./Transfer. Your music library is still the install folder default.',
    );
    expect(warned(music)).toEqual(['Your music library is still the install folder default.']);
    expect(
      warned(musicFoldersSentence({ download: '/dl', transfer: '/music', installDefault: false })),
    ).toEqual([]);
    expect(read(audiobookFoldersSentence({ path: '/books', extra: 2, routed: 1 }))).toBe(
      'Finished books go to /books. SoulSync also reads 2 more folders. Downloads by 1 spoken-word artist are filed with the books.',
    );
    expect(read(audiobookFoldersSentence({ path: '', extra: 0, routed: null }))).toBe(
      'Finished books go to ./audiobooks.',
    );
    expect(read(podcastFoldersSentence('/pods'))).toBe('Episodes are saved in /pods.');
    expect(read(videoFoldersSentence({ download_path: '/dl', movies_path: '/m' }, 'ready'))).toBe(
      'Downloads land in /dl. Movies go to /m. No TV folder is set yet.',
    );
    expect(warned(videoFoldersSentence({}, 'ready'))).toEqual([
      'No movies or TV folder is set yet.',
    ]);
    expect(read(videoFoldersSentence(null, 'loading'))).toBe('Loading the video folders.');
    expect(warned(videoFoldersSentence(null, 'failed'))).toEqual([
      'The video folders did not load.',
    ]);
  });

  it('Naming: what a file is saved as, in folders', () => {
    expect(
      read(
        musicNamingSentence({
          on: true,
          album: 'Daft Punk/Discovery (2001)/01 - One More Time.flac',
          template: '',
        }),
      ),
    ).toBe('An album track is saved as Daft Punk / Discovery (2001) / 01 - One More Time.flac');
    // before the server answered: the template in words
    expect(
      named(musicNamingSentence({ on: true, album: null, template: '$albumartist/$album/$title' })),
    ).toEqual(['albumartist / album / Title']);
    expect(read(musicNamingSentence({ on: false, album: null, template: '' }))).toBe(
      "Custom templates are off, so files keep SoulSync's built-in layout.",
    );
    expect(read(audiobookNamingSentence('$author/$series/$seriespos - $title'))).toBe(
      'A book is saved as Author / Series / Series position - Title.',
    );
    expect(read(podcastNamingSentence('$show/Season $season/$title'))).toBe(
      'An episode is saved as Show / Season Season number / Title.',
    );
    expect(read(videoNamingSentence('A (1999)/A.mkv', 'S/Season 01/S - S01E01.mkv'))).toBe(
      'A movie is saved as A (1999) / A.mkv and an episode as S / Season 01 / S - S01E01.mkv.',
    );
    expect(templateWords('${author}x/$narrator')).toBe('Authorx / Narrator');
    expect(pathWords('/a//b/')).toBe('a / b');
  });

  it('Tags and artwork: who writes IDs, where cover art comes from, what goes with a file', () => {
    expect(
      read(
        musicTagsSentence({
          enabled: true,
          services: ['Spotify', 'MusicBrainz', 'Deezer'],
          art: ['Cover Art Archive'],
          artwork: true,
        }),
      ),
    ).toBe(
      'Downloads are tagged with IDs from Spotify, MusicBrainz and Deezer. Cover art comes from Cover Art Archive first.',
    );
    expect(read(musicTagsSentence({ enabled: true, services: [], art: [], artwork: true }))).toBe(
      'Downloads are tagged, with no service IDs written. Cover art comes from the metadata source.',
    );
    expect(
      warned(musicTagsSentence({ enabled: false, services: ['Spotify'], art: [], artwork: true })),
    ).toEqual(['Post-processing is off,']);
    expect(
      read(audiobookTagsSentence({ tags: true, embed: false, cover: true, sidecars: true })),
    ).toBe('Books get tags, a cover.jpg and metadata sidecars.');
    expect(
      read(audiobookTagsSentence({ tags: false, embed: false, cover: false, sidecars: false })),
    ).toBe('Books are kept as they arrive: nothing is written into them.');
    expect(read(videoTagsSentence({ subs: true, artwork: false, nfo: false }))).toBe(
      'Finished files are filed with matching subtitles. Artwork and NFO files are left out.',
    );
    expect(read(videoTagsSentence({ subs: true, artwork: true, nfo: false }))).toBe(
      'Finished files are filed with matching subtitles and artwork. NFO files are left out.',
    );
    expect(listWords(['a'])).toBe('a');
  });

  it('Import and files: what happens when a file arrives', () => {
    expect(read(musicImportSentence({ source: 'any source', permanent: true }))).toBe(
      'The Import page searches any source. Your music library folder is permanent, so a deep scan never moves files out.',
    );
    expect(read(audiobookImportSentence({ percent: 92, days: 1, merge: true }))).toBe(
      'A book is imported once 92% of its runtime is in, and given up on after 1 day. Books in many files are merged into one M4B.',
    );
    expect(read(podcastImportSentence('both'))).toBe('Podcasts are kept as audio and video.');
    expect(read(podcastImportSentence(''))).toBe('Podcasts are kept as audio only.');
    expect(
      read(
        videoImportSentence({ move: false, verify: true, replace: true, recycle: true, days: 7 }),
      ),
    ).toBe(
      'A finished file is copied in and checked with ffprobe. A better copy replaces the one you have. Deleted files go to the recycle bin for 7 days.',
    );
    expect(read(videoImportSentence(null))).toBe('Loading what happens to video files.');
  });

  it('Subtitles: the languages, the providers in order, Whisper last', () => {
    expect(
      read(
        subtitlesSentence({
          on: true,
          langs: 'en;es',
          providers: ['SubDL', 'OpenSubtitles', 'Embedded'],
          whisper: false,
        }),
      ),
    ).toBe('Subtitles in en, es come from SubDL, then OpenSubtitles, then Embedded.');
    expect(
      warned(subtitlesSentence({ on: false, langs: '', providers: [], whisper: false })),
    ).toEqual(['not downloaded']);
  });
});
