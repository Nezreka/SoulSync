import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { CoverFindingState } from '../-tools.api';
import type { RepairFinding } from '../-tools.types';

import { coverPlanLine } from './cover-art-finding';
import { FindingDetail } from './finding-detail';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const coverFinding = (details: Record<string, unknown> = {}): RepairFinding =>
  ({
    id: 9,
    finding_type: 'missing_cover_art',
    severity: 'info',
    status: 'pending',
    entity_type: 'album',
    entity_id: 'lib2:5',
    title: 'Artwork storage gaps: Punkrocker',
    description: '',
    details: {
      album_id: 'lib2:5',
      album_title: 'Punkrocker (radio edit)',
      artist: 'Teddybears',
      db_missing: true,
      embed_missing: false,
      sidecar_missing: true,
      sidecar_from_embedded: true,
      artwork_source: 'embedded',
      found_artwork_url: null,
      artist_thumb_url: 'https://artist/photo',
      found_artist_url: 'https://other/photo',
      ...details,
    },
  }) as unknown as RepairFinding;

const live = (over: Partial<CoverFindingState> = {}): CoverFindingState => ({
  album_id: 5,
  library: { has: false, locked: false, url: null },
  files: { with: 3, without: 0, unknown: 0, url: '/api/repair/findings/9/cover-image?place=files' },
  folder: { file: null, exists: true, url: null },
  found: { url: null, source: null },
  settings: { embed: true, sidecar: true },
  plan: [
    { place: 'library', action: 'skip', why: 'nothing_found' },
    { place: 'files', action: 'keep', why: 'has' },
    { place: 'folder', action: 'write', why: 'files' },
  ],
  ...over,
});

function serve(state: CoverFindingState | null) {
  const fetchMock = vi.fn(async () =>
    state
      ? new Response(JSON.stringify({ success: true, ...state }), { status: 200 })
      : new Response('{}', { status: 404 }),
  );
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

const renderDetail = (finding: RepairFinding) =>
  render(<FindingDetail finding={finding} onKeepDuplicate={vi.fn()} onApplyCoverArt={vi.fn()} />);

describe('a cover art finding explains itself (#2169)', () => {
  it('shows the library, the files, the folder and the found cover, empty when missing', async () => {
    const fetchMock = serve(live());
    const { container } = renderDetail(coverFinding());
    await waitFor(() =>
      expect(screen.getByText('Writes cover.jpg from the cover inside the files.')).toBeTruthy(),
    );

    expect(fetchMock).toHaveBeenCalledWith('/api/repair/findings/9/cover');
    const places = [...container.querySelectorAll('figcaption > span:first-child')].map(
      (n) => n.textContent,
    );
    expect(places).toEqual([
      'In the library',
      'In the audio files',
      'In the album folder',
      'Found by a source',
    ]);
    expect(screen.getByText('No saved cover')).toBeTruthy();
    expect(screen.getByText('No cover file')).toBeTruthy();
    expect(screen.getByText('Nothing found')).toBeTruthy();
    const files = container.querySelector('img[src*="place=files"]');
    expect(files).not.toBeNull();
    expect(screen.getByText('In 3 of 3 files')).toBeTruthy();
    expect(
      screen.getByText(
        'Stays without a saved cover: no source had one. Change cover lets you pick one.',
      ),
    ).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Change cover' })).toBeTruthy();
  });

  it('names the source the found cover came from', async () => {
    serve(
      live({
        found: { url: 'https://deezer/cover.jpg', source: 'deezer' },
        plan: [{ place: 'library', action: 'write', why: 'found' }],
      }),
    );
    renderDetail(
      coverFinding({ found_artwork_url: 'https://deezer/cover.jpg', artwork_source: 'deezer' }),
    );
    await waitFor(() => expect(screen.getByText('Found on Deezer')).toBeTruthy());
    expect(screen.getByText('Saves the Deezer cover.')).toBeTruthy();
  });

  it('never offers an artist photo for an artist that already has one', async () => {
    serve(live());
    const { container } = renderDetail(coverFinding());
    await waitFor(() => expect(screen.getByText('What Apply Art does')).toBeTruthy());
    expect(screen.queryByText('Use for artist')).toBeNull();
    expect(container.querySelector('img[src="https://artist/photo"]')).toBeNull();
  });

  it('keeps a suggested photo for an artist with none apart from the cover', async () => {
    serve(live());
    renderDetail(coverFinding({ artist_thumb_url: null }));
    await waitFor(() => expect(screen.getByText('Suggested artist photo')).toBeTruthy());
    expect(screen.getByText(/Apply Art never uses it/)).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Use for artist' })).toBeTruthy();
  });

  it('falls back to what the scan saw when the server cannot describe it', async () => {
    serve(null);
    renderDetail(coverFinding());
    await waitFor(() =>
      expect(screen.getByText(/no longer in the library as it was/)).toBeTruthy(),
    );
    expect(screen.getByText('Has a cover')).toBeTruthy();
  });
});

describe('Change cover on a finding (#2169)', () => {
  it('opens the album page picker, and a pick closes the finding', async () => {
    const calls: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url =
          typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
        const method = init?.method || (input instanceof Request ? input.method : 'GET');
        calls.push(`${method} ${url}`);
        const json = (body: unknown) =>
          new Response(JSON.stringify(body), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          });
        if (url.includes('/findings/9/cover')) return json({ success: true, ...live() });
        if (url.includes('art-options')) return json({ success: true, candidates: [] });
        if (url.includes('albums/5/art')) return json({ success: true, image_url: '/a.jpg' });
        return json({ success: true });
      }),
    );
    const changed = vi.fn();
    render(
      <QueryClientProvider client={new QueryClient()}>
        <FindingDetail
          finding={coverFinding()}
          onKeepDuplicate={vi.fn()}
          onApplyCoverArt={vi.fn()}
          onChanged={changed}
        />
      </QueryClientProvider>,
    );
    fireEvent.click(await screen.findByRole('button', { name: 'Change cover' }));
    const input = await screen.findByLabelText('Image URL for the cover');
    fireEvent.change(input, { target: { value: 'https://mine/cover.jpg' } });
    fireEvent.click(screen.getByRole('button', { name: 'Use this URL' }));

    await waitFor(() => expect(changed).toHaveBeenCalled());
    expect(calls.some((c) => c.startsWith('POST') && c.includes('library/v2/albums/5/art'))).toBe(
      true,
    );
    expect(calls).toContain('POST /api/repair/findings/9/resolve');
  });
});

describe('coverPlanLine', () => {
  it('says what each place gets, with the settings that stop it', () => {
    expect(coverPlanLine({ place: 'files', action: 'write', why: 'library', count: 1 }, '')).toBe(
      "Embeds the library's cover into the 1 file without one.",
    );
    expect(coverPlanLine({ place: 'files', action: 'skip', why: 'setting_off' }, '')).toBe(
      'Left alone: embedding art is off in Settings.',
    );
    expect(
      coverPlanLine({ place: 'folder', action: 'keep', why: 'has', file: 'folder.jpg' }, ''),
    ).toBe('Already has folder.jpg. Kept as it is.');
    expect(coverPlanLine({ place: 'folder', action: 'write', why: 'found' }, 'Deezer')).toBe(
      'Writes cover.jpg from the Deezer cover.',
    );
    expect(coverPlanLine({ place: 'library', action: 'keep', why: 'chosen' }, '')).toBe(
      'Keeps the cover you picked.',
    );
  });
});
