import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { extractFunction } from './vanilla-extract';

/**
 * the album modal opened from a discography used to sit on "🔍 Pending" for
 * every track until Begin Analysis, and Begin Analysis goes straight on to
 * downloading. SeadogsBooty: "I can't verify individual track ownership".
 * it now asks the library on open, read-only.
 */

const JS = readFileSync(resolve(process.cwd(), 'static/downloads.js'), 'utf8');
const HELPERS = readFileSync(resolve(process.cwd(), 'static/shared-helpers.js'), 'utf8');

type Proc = { status: string; tracks: unknown[]; album?: unknown; artist?: unknown };
type Preview = (id: string) => Promise<void>;

function load(processes: Record<string, Proc>): Preview {
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  return new Function(
    'activeDownloadProcesses',
    [
      extractFunction('isAlbumContextPlaylistId', JS),
      extractFunction('updateTrackAnalysisResults', JS),
      extractFunction('previewModalOwnership', JS),
      'return previewModalOwnership;',
    ].join('\n'),
  )(processes) as Preview;
}

function modal(id: string, rows: number) {
  document.body.innerHTML = `
    <span id="analysis-progress-text-${id}">Ready to start</span>
    <div id="stat-found-${id}">-</div><div id="stat-missing-${id}">-</div>
    ${Array.from({ length: rows }, (_, i) => `<span id="match-${id}-${i}" class="track-match-status match-checking">🔍 Pending</span>`).join('')}`;
}

function respond(body: unknown) {
  const fetchMock = vi.fn().mockResolvedValue({ json: () => Promise.resolve(body) });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

const ID = 'artist_album_a1_b2';
const text = (sel: string) => document.getElementById(sel)?.textContent;

afterEach(() => {
  vi.unstubAllGlobals();
  document.body.innerHTML = '';
});

describe('the album modal ownership preview', () => {
  it('fills every row and the found/missing tiles on open', async () => {
    modal(ID, 3);
    const procs = {
      [ID]: { status: 'idle', tracks: [{}, {}, {}], album: { name: 'A' }, artist: { name: 'B' } },
    };
    respond({
      success: true,
      results: [
        { track_index: 0, found: true },
        { track_index: 1, found: false },
        { track_index: 2, found: true },
      ],
    });
    await load(procs)(ID);
    expect(text(`match-${ID}-0`)).toBe('✅ Found');
    expect(text(`match-${ID}-1`)).toBe('❌ Missing');
    expect(text(`stat-found-${ID}`)).toBe('2');
    expect(text(`stat-missing-${ID}`)).toBe('1');
    expect(text(`analysis-progress-text-${ID}`)).toBe('2 in your library, 1 missing');
  });

  it('sends the album context so the album fast path runs', async () => {
    modal(ID, 1);
    const fetchMock = respond({ success: true, results: [] });
    await load({
      [ID]: { status: 'idle', tracks: [{}], album: { name: 'A' }, artist: { name: 'B' } },
    })(ID);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`/api/playlists/${ID}/ownership-preview`);
    const body = JSON.parse(init.body);
    expect(body.is_album_download).toBe(true);
    expect(body.album_context).toEqual({ name: 'A' });
  });

  it('never overwrites a run that started while it waited', async () => {
    modal(ID, 1);
    const proc = { status: 'idle', tracks: [{}] };
    respond({ success: true, results: [{ track_index: 0, found: true }] });
    const run = load({ [ID]: proc })(ID);
    proc.status = 'running';
    await run;
    expect(text(`match-${ID}-0`)).toBe('🔍 Pending');
  });

  it('falls back to ready-to-start when the check fails', async () => {
    modal(ID, 1);
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('down')));
    await load({ [ID]: { status: 'idle', tracks: [{}] } })(ID);
    expect(text(`analysis-progress-text-${ID}`)).toBe('Ready to start');
    expect(text(`match-${ID}-0`)).toBe('🔍 Pending');
  });

  it('runs when the artist album modal opens', () => {
    const open = extractFunction('openDownloadMissingModalForArtistAlbum', HELPERS);
    expect(open).toContain('previewModalOwnership(virtualPlaylistId)');
  });
});
