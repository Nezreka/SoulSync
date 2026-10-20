import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { runInNewContext } from 'node:vm';
import { describe, expect, it } from 'vitest';

import { extractFunction } from './vanilla-extract';

const downloads = readFileSync(resolve(process.cwd(), 'static/downloads.js'), 'utf8');
const helpers = readFileSync(resolve(process.cwd(), 'static/shared-helpers.js'), 'utf8');
const render = runInNewContext(
  [
    ...[
      'escapeHtml',
      'escapeForInlineJs',
      'formatArtists',
      'renderModalTrackPlayButton',
      'renderModalTrackPlaylistButton',
      'renderDownloadModalBody',
    ].map((name) => extractFunction(name, downloads)),
    extractFunction('formatDuration', helpers),
    extractFunction('cleanArtistName', helpers),
    'renderDownloadModalBody;',
  ].join('\n'),
  { document },
) as (
  id: string,
  tracks: { name: string; artists: { name: string }[]; duration_ms: number }[],
  labels?: string[],
) => string;

const tracks = [
  { name: '<img src=x onerror=alert(1)>', artists: [{ name: 'A & B' }], duration_ms: 61000 },
];

describe('the shared download-modal body', () => {
  it.each([
    ['Track', 'Artist', 'Library Match'],
    ['Track Name', 'Artist(s)', 'Library Status'],
  ])('preserves %s / %s / %s headings and engine selectors', (title, artist, library) => {
    const host = document.createElement('div');
    host.innerHTML = render('playlist-1', tracks, [title, artist, library]);
    expect([...host.querySelectorAll('th')].map((el) => el.textContent?.trim())).toEqual([
      '',
      '#',
      title,
      artist,
      'Duration',
      library,
      'Download Status',
      'Actions',
    ]);
    expect(host.querySelector('#track-selection-count-playlist-1')?.textContent).toBe(
      '1 / 1 tracks selected',
    );
    expect(host.querySelector('#analysis-progress-text-playlist-1')?.textContent).toBe(
      'Ready to start',
    );
    expect(host.querySelector('#download-progress-text-playlist-1')?.textContent).toBe(
      'Waiting for analysis',
    );
    expect(host.querySelector('#match-playlist-1-0')?.textContent).toBe('🔍 Pending');
    expect(host.querySelector('#download-playlist-1-0')?.textContent).toBe('-');
    expect(host.querySelector('#actions-playlist-1-0')?.textContent).toBe('-');
    expect(host.querySelector('.track-duration')?.textContent).toBe('1:01');
    expect(host.querySelector('img')).toBeNull();
    expect(host.querySelector('.track-name')?.getAttribute('title')).toBe(tracks[0].name);
    expect(host.querySelector('.track-artist')?.textContent).toBe('A & B');
    expect(host.querySelector('[title="Play track"]')).not.toBeNull();
    expect(host.querySelector('[aria-label="Add to playlist"]')).not.toBeNull();
    expect(host.querySelector<HTMLInputElement>('#select-all-playlist-1')?.checked).toBe(true);
    expect(host.querySelector('.track-select-cb')?.getAttribute('onchange')).toBe(
      "updateTrackSelectionCount('playlist-1')",
    );
  });

  it('renders an empty table with default playlist headings', () => {
    const host = document.createElement('div');
    host.innerHTML = render('empty', []);
    expect(host.querySelector('#track-selection-count-empty')?.textContent).toBe(
      '0 / 0 tracks selected',
    );
    expect(host.querySelector('#download-tracks-tbody-empty')?.children).toHaveLength(0);
    expect(host.querySelector('th:nth-child(3)')?.textContent).toBe('Track');
  });
});
