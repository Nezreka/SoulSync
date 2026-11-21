/** the download order in words: the order read aloud, a row's role, its status, the toast */

import { describe, expect, it } from 'vitest';

import {
  changeText,
  dropText,
  orderSentence,
  orderText,
  roleText,
  statusOf,
  usedIn,
} from './words';

describe('the order read aloud', () => {
  it('reads the music order aloud', () => {
    expect(orderText('music', ['soulseek', 'youtube', 'qobuz'])).toBe(
      'Music tries Soulseek first, then YouTube, then Qobuz.',
    );
    expect(orderText('music', ['soulseek'])).toBe('Music downloads from Soulseek only.');
  });
  it('says which sources are skipped until they are set up', () => {
    expect(orderText('music', ['soulseek', 'youtube', 'qobuz'], { qobuz: true })).toBe(
      "Music tries Soulseek first, then YouTube, then Qobuz. Qobuz isn't set up yet, so it is skipped for now.",
    );
    expect(orderText('music', ['tidal', 'qobuz', 'soulseek'], { qobuz: true, tidal: true })).toBe(
      "Music tries Tidal first, then Qobuz, then Soulseek. Tidal and Qobuz aren't set up yet, so they are skipped for now.",
    );
    const parts = orderSentence('music', ['soulseek', 'qobuz'], { qobuz: true });
    expect(parts.filter((p) => p.kind === 'name').map((p) => p.text)).toEqual([
      'Soulseek',
      'Qobuz',
    ]);
    expect(parts.find((p) => p.kind === 'warn')?.text).toBe("Qobuz isn't set up yet");
  });
  it('has its own words for video and for audiobooks', () => {
    expect(orderText('video', ['torrent', 'usenet'])).toBe(
      'Movies and TV try Torrent first, then Usenet.',
    );
    expect(orderText('video', ['extto'])).toBe('Movies and TV download from External only.');
    expect(orderText('audiobooks', ['usenet', 'torrent'])).toBe(
      'Books ask Usenet first, then Torrent.',
    );
    expect(orderText('music', [])).toBe('No source is picked yet. Add one below.');
  });
});

describe('a row', () => {
  it('says what its place means', () => {
    const order = ['soulseek', 'youtube', 'qobuz'];
    expect(roleText('music', 'soulseek', 0, order)).toBe('Tried first');
    expect(roleText('music', 'youtube', 1, order)).toBe('Tried when Soulseek finds nothing');
    expect(roleText('music', 'qobuz', 2, order, { qobuz: true })).toBe(
      'Skipped until it is set up',
    );
    expect(roleText('video', 'torrent', 0, ['torrent'])).toBe('The only source for video');
    expect(roleText('audiobooks', 'usenet', 0, ['usenet', 'torrent'])).toBe('Asked first');
    expect(roleText('audiobooks', 'torrent', 1, ['usenet', 'torrent'])).toBe('Asked after Usenet');
  });
  it('says what a drop will mean', () => {
    expect(dropText('music', 'youtube', 0)).toBe('Drop here: YouTube is tried first');
    expect(dropText('audiobooks', 'soulseek', 2)).toBe('Drop here: Soulseek is asked third');
  });
  it('gives its health in words, never a dot alone', () => {
    const base = { status: {}, said: {}, unready: {} };
    expect(statusOf('soulseek', base)).toEqual({ tone: 'idle', text: 'Not tested yet' });
    expect(statusOf('soulseek', { ...base, status: { soulseek: 'ok' } })).toEqual({
      tone: 'ok',
      text: 'Connected',
    });
    expect(statusOf('soulseek', { ...base, status: { soulseek: 'testing' } }).text).toBe(
      'Testing…',
    );
    // what the server said, without the sign it leads a warning with
    expect(
      statusOf('youtube', {
        ...base,
        status: { youtube: 'warn' },
        said: { youtube: '⚠ Cookies are stale' },
      }),
    ).toEqual({ tone: 'warn', text: 'Cookies are stale' });
    expect(
      statusOf('lidarr', { ...base, status: { lidarr: 'fail' }, said: { lidarr: 'Bad API key' } }),
    ).toEqual({ tone: 'bad', text: 'Bad API key' });
    expect(statusOf('lidarr', { ...base, status: { lidarr: 'fail' } }).text).toBe(
      'Connection failed',
    );
  });
  it('needs setup when it is in the order and the server will skip it', () => {
    const state = { status: { qobuz: 'ok' as const }, said: {}, unready: { qobuz: true } };
    expect(statusOf('qobuz', state, { inOrder: true })).toEqual({
      tone: 'warn',
      text: 'Needs setup',
    });
    // not in the order: only what its test said
    expect(statusOf('qobuz', state).text).toBe('Connected');
  });
  it('lists where else a source is used', () => {
    expect(
      usedIn([
        { label: 'Video', pos: 1 },
        { label: 'Audiobooks', pos: 2 },
      ]),
    ).toBe('Video #1, Audiobooks #2');
  });
});

describe('what a change did, and how it saves', () => {
  it('music saves with the page, video at once', () => {
    expect(
      changeText('music', { type: 'move', id: 'youtube', from: 1, to: 0, by: 'drag' }, [
        'youtube',
        'soulseek',
      ]),
    ).toBe('YouTube is now tried first for music. It saves with your other changes.');
    expect(
      changeText('video', { type: 'move', id: 'usenet', from: 0, to: 1, by: 'menu' }, [
        'torrent',
        'usenet',
      ]),
    ).toBe('Usenet is now tried second for video. The video order saves at once.');
  });
  it('adding, removing, and the last one left', () => {
    expect(
      changeText('music', { type: 'add', id: 'tidal', from: -1, to: 2, by: 'button' }, [
        'soulseek',
        'youtube',
        'tidal',
      ]),
    ).toBe('Tidal is now used for music, tried third. It saves with your other changes.');
    expect(
      changeText('audiobooks', { type: 'remove', id: 'soulseek', from: 2, to: -1, by: 'menu' }, [
        'torrent',
        'usenet',
      ]),
    ).toBe('Soulseek is no longer used for audiobooks. It saves with your other changes.');
    expect(
      changeText('video', { type: 'remove', id: 'usenet', from: 1, to: -1, by: 'drag' }, [
        'torrent',
      ]),
    ).toBe('Usenet is no longer used for video. The video order saves at once.');
  });
});
