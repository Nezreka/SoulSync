import { describe, expect, it } from 'vitest';

import type { AudioTrack, SubtitleTrack } from './tracks';

import { audioLabel, pickAudio, pickSubtitle, sameLanguage } from './tracks';
import { cuesAt, parseVtt } from './vtt';

const a = (index: number, language: string, extra: Partial<AudioTrack> = {}): AudioTrack => ({
  index,
  language,
  title: null,
  codec: 'ac3',
  channels: 6,
  layout: null,
  default: false,
  plays: false,
  ...extra,
});
const sub = (
  id: string,
  language: string | null,
  extra: Partial<SubtitleTrack> = {},
): SubtitleTrack => ({
  id,
  language,
  title: null,
  codec: 'subrip',
  kind: 'text',
  forced: false,
  default: false,
  external: false,
  ...extra,
});

describe('tracks', () => {
  it('knows eng and en are the same', () => {
    expect(sameLanguage('eng', 'en')).toBe(true);
    expect(sameLanguage('pt-BR', 'por')).toBe(true);
    expect(sameLanguage('jpn', 'en')).toBe(false);
  });

  it('picks audio by the saved language, then the default', () => {
    const tracks = [a(0, 'eng', { default: true }), a(1, 'jpn')];
    expect(pickAudio(tracks, { audio: 'ja', subtitle: null })).toBe(1);
    expect(pickAudio(tracks, { audio: null, subtitle: null })).toBe(0);
    expect(pickAudio([], { audio: 'ja', subtitle: null })).toBe(0);
  });

  it('subtitles: the saved language, off, or forced ones in the audio language', () => {
    const subs = [
      sub('s0', 'eng', { forced: true }),
      sub('s1', 'eng'),
      sub('s2', 'spa', { kind: 'image' }),
    ];
    expect(pickSubtitle(subs, { audio: null, subtitle: 'en' }, 'eng')).toBe('s1');
    expect(pickSubtitle(subs, { audio: null, subtitle: 'es' }, 'eng')).toBe('s2');
    expect(pickSubtitle(subs, { audio: null, subtitle: null }, 'eng')).toBe('s0');
    expect(pickSubtitle(subs, { audio: null, subtitle: 'off' }, 'eng')).toBeNull();
    expect(pickSubtitle(subs, { audio: null, subtitle: null }, 'jpn')).toBeNull();
  });

  it('labels a track the way a person reads it', () => {
    expect(audioLabel(a(0, 'eng', { codec: 'eac3' }))).toEqual({
      label: 'English',
      detail: '5.1 · EAC3',
    });
  });
});

describe('webvtt', () => {
  const text = `WEBVTT

1
00:00:01.000 --> 00:00:03.500 line:90%
<i>Hello</i> there

00:04.000 --> 00:05.000
Second &amp; line
{\\an8}top`;

  it('reads cues and drops styling', () => {
    expect(parseVtt(text)).toEqual([
      { start: 1, end: 3.5, text: 'Hello there' },
      { start: 4, end: 5, text: 'Second & line\ntop' },
    ]);
  });

  it('finds what shows at a time', () => {
    const cues = parseVtt(text);
    expect(cuesAt(cues, 2).map((c) => c.text)).toEqual(['Hello there']);
    expect(cuesAt(cues, 3.6)).toEqual([]);
    expect(cuesAt(cues, 4.5)[0].start).toBe(4);
  });
});
