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
  choices: ['files'],
  pick: 'files',
  plans: {},
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

const renderDetail = (finding: RepairFinding, onApplyCoverArt = vi.fn()) =>
  render(
    <FindingDetail finding={finding} onKeepDuplicate={vi.fn()} onApplyCoverArt={onApplyCoverArt} />,
  );

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

// the reporter's Stadium Arcadium: a library cover, none in the files, a
// cover.jpg in the folder, and (say) a found one
const everything = (over: Partial<CoverFindingState> = {}) =>
  live({
    library: { has: true, locked: false, url: '/api/repair/findings/9/cover-image?place=library' },
    files: { with: 0, without: 2, unknown: 0, url: null },
    folder: {
      file: 'cover.jpg',
      exists: true,
      url: '/api/repair/findings/9/cover-image?place=folder',
    },
    found: { url: 'https://deezer/cover.jpg', source: 'deezer' },
    choices: ['library', 'folder', 'found'],
    pick: 'library',
    plan: [{ place: 'library', action: 'keep', why: 'has' }],
    plans: {
      library: [{ place: 'library', action: 'keep', why: 'has' }],
      folder: [{ place: 'files', action: 'write', why: 'folder', count: 2 }],
      found: [{ place: 'library', action: 'write', why: 'found', replace: true }],
    },
    ...over,
  });

describe('every cover on a finding can be picked (#2202)', () => {
  it('picks the library cover to start with and applies it', async () => {
    serve(everything());
    const apply = vi.fn();
    renderDetail(coverFinding({ found_artwork_url: 'https://deezer/cover.jpg' }), apply);
    const library = await screen.findByRole('radio', { name: 'Use the cover in the library' });
    expect(library.getAttribute('aria-checked')).toBe('true');
    expect(screen.getByRole('radiogroup', { name: 'Cover Apply Art uses' })).toBeTruthy();
    expect(screen.getAllByText('Using this')).toHaveLength(1);
    expect(screen.getByText('Already has a cover. Kept as it is.')).toBeTruthy();
    // a tile with no picture is no pick
    expect(screen.getAllByRole('radio')).toHaveLength(3);
    fireEvent.click(screen.getByRole('button', { name: 'Apply Art' }));
    expect(apply).toHaveBeenCalledWith(9, 'album:library');
  });

  it('a click picks another cover, shows its plan and applies it', async () => {
    serve(everything());
    const apply = vi.fn();
    renderDetail(coverFinding({ found_artwork_url: 'https://deezer/cover.jpg' }), apply);
    const found = await screen.findByRole('radio', { name: 'Use the cover found on Deezer' });
    fireEvent.click(found);
    expect(found.getAttribute('aria-checked')).toBe('true');
    expect(
      screen
        .getByRole('radio', { name: 'Use the cover in the library' })
        .getAttribute('aria-checked'),
    ).toBe('false');
    expect(
      screen.getByText('Replaces its cover with the Deezer cover, the same as Change cover.'),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole('radio', { name: 'Use the cover in the album folder' }));
    expect(
      screen.getByText("Embeds the album folder's cover into the 2 files without one."),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Apply Art' }));
    expect(apply).toHaveBeenCalledWith(9, 'album:folder');
    expect(screen.getByRole('button', { name: 'Change cover' })).toBeTruthy();
  });

  it('a cover that does not load is no pick, the next best is', async () => {
    serve(everything());
    const apply = vi.fn();
    const { container } = renderDetail(coverFinding(), apply);
    await screen.findByRole('radio', { name: 'Use the cover in the library' });
    fireEvent.error(container.querySelector('img[src*="place=library"]') as Element);
    await waitFor(() =>
      expect(
        screen
          .getByRole('radio', { name: 'Use the cover in the album folder' })
          .getAttribute('aria-checked'),
      ).toBe('true'),
    );
    expect(screen.queryByRole('radio', { name: 'Use the cover in the library' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Apply Art' }));
    expect(apply).toHaveBeenCalledWith(9, 'album:folder');
  });

  it('with no cover anywhere, Apply Art waits and Change cover is still there', async () => {
    serve(
      live({
        library: { has: false, locked: false, broken: true, url: null },
        files: { with: 0, without: 1, unknown: 0, url: null },
        choices: [],
        pick: null,
        plan: [{ place: 'library', action: 'skip', why: 'broken' }],
      }),
    );
    renderDetail(coverFinding());
    await waitFor(() => expect(screen.getByText('Saved cover no longer loads')).toBeTruthy());
    expect(
      screen.getByText('Its saved cover no longer loads. Change cover lets you pick a new one.'),
    ).toBeTruthy();
    expect(screen.queryByRole('radio')).toBeNull();
    expect((screen.getByRole('button', { name: 'Apply Art' }) as HTMLButtonElement).disabled).toBe(
      true,
    );
    expect(screen.getByText(/There is no cover here to apply/)).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Change cover' })).toBeTruthy();
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
