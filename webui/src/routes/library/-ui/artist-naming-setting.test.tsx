import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';

import type { LibraryV2ArtistNaming } from '../-library-v2.api';

import { ArtistNamingSetting, namingSentence } from './artist-naming-setting';

const deezer = { provider: 'deezer', label: 'Deezer', id: '1135797', name: 'Machine Gun Kelly' };

const automatic: LibraryV2ArtistNaming = {
  artist_id: 7,
  library_name: 'mgk',
  scanned_name: 'mgk',
  pinned: false,
  source: deezer,
  renamed: true,
  kept_source_name: false,
  folder: 'mgk/mgk - Lost Americana',
};

function serve(naming: LibraryV2ArtistNaming) {
  server.use(
    http.get('/api/library/v2/artists/7/naming', () =>
      HttpResponse.json({ success: true, ...naming }),
    ),
  );
}

function renderSetting(props: Partial<Parameters<typeof ArtistNamingSetting>[0]> = {}) {
  return render(
    <QueryClientProvider client={createTestQueryClient()}>
      <ArtistNamingSetting artistId={7} canWrite {...props} />
    </QueryClientProvider>,
  );
}

describe('#2165 Folder and tag name', () => {
  it('says plainly what the source calls them and what downloads are filed as', async () => {
    serve(automatic);
    renderSetting();
    expect(await screen.findByText('Deezer calls them')).toBeInTheDocument();
    expect(screen.getByText('Machine Gun Kelly')).toBeInTheDocument();
    expect(screen.getByText('Filed and tagged as')).toBeInTheDocument();
    expect(screen.getByText('From your library')).toBeInTheDocument();
    expect(screen.getByText('mgk/mgk - Lost Americana/')).toBeInTheDocument();
    expect(
      screen.getByText(
        'Deezer calls them Machine Gun Kelly. SoulSync files them as mgk, the name in your library.',
      ),
    ).toBeInTheDocument();
    expect(screen.getByLabelText('Name for folders and tags')).toHaveValue('mgk');
    // nothing typed, nothing to save
    expect(screen.getByRole('button', { name: 'Save name' })).toBeDisabled();
  });

  it('saves a name as the artist name override', async () => {
    serve(automatic);
    let sent: unknown;
    server.use(
      http.patch('/api/library/v2/metadata-overrides/artist/7', async ({ request }) => {
        sent = await request.json();
        return HttpResponse.json({ success: true, overrides: { name: 'MGK' } });
      }),
    );
    renderSetting();
    const input = await screen.findByLabelText('Name for folders and tags');
    fireEvent.change(input, { target: { value: '  MGK ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save name' }));
    await waitFor(() => expect(sent).toEqual({ set: { name: 'MGK' }, clear: [] }));
  });

  it('shows why a name was refused', async () => {
    serve(automatic);
    server.use(
      http.patch('/api/library/v2/metadata-overrides/artist/7', () =>
        HttpResponse.json(
          {
            success: false,
            error:
              'Kelly in your library is a different artist with that name. Pick a name that tells them apart.',
          },
          { status: 409 },
        ),
      ),
    );
    renderSetting();
    fireEvent.change(await screen.findByLabelText('Name for folders and tags'), {
      target: { value: 'Kelly' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save name' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Kelly in your library is a different artist',
    );
  });

  it('a name set by hand says so and can go back to the scanned one', async () => {
    serve({ ...automatic, library_name: 'MGK', pinned: true });
    let sent: unknown;
    server.use(
      http.patch('/api/library/v2/metadata-overrides/artist/7', async ({ request }) => {
        sent = await request.json();
        return HttpResponse.json({ success: true, overrides: {} });
      }),
    );
    renderSetting();
    expect(await screen.findByText('Set by you')).toBeInTheDocument();
    expect(
      screen.getByText(
        'Deezer calls them Machine Gun Kelly. SoulSync files them as MGK, the name you set.',
      ),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Go back to mgk' }));
    await waitFor(() => expect(sent).toEqual({ set: {}, clear: ['name'] }));
  });

  it('is read-only without library write access', async () => {
    serve(automatic);
    let writes = 0;
    server.use(
      http.patch('/api/library/v2/metadata-overrides/artist/7', () => {
        writes += 1;
        return HttpResponse.json({ success: true, overrides: {} });
      }),
    );
    renderSetting({ canWrite: false });
    const input = await screen.findByLabelText('Name for folders and tags');
    expect(input).toBeDisabled();
    fireEvent.submit(input.closest('form') as HTMLFormElement);
    expect(screen.getByRole('button', { name: 'Save name' })).toBeDisabled();
    expect(writes).toBe(0);
  });

  it('links existing files to Preview rename and Preview retag', async () => {
    serve(automatic);
    const onPreviewRename = vi.fn();
    const onPreviewRetag = vi.fn();
    renderSetting({ onPreviewRename, onPreviewRetag });
    fireEvent.click(await screen.findByRole('button', { name: 'Preview rename' }));
    fireEvent.click(screen.getByRole('button', { name: 'Preview retag' }));
    expect(onPreviewRename).toHaveBeenCalledTimes(1);
    expect(onPreviewRetag).toHaveBeenCalledTimes(1);
  });

  it('names the source only when it spells them differently', async () => {
    serve({ ...automatic, renamed: false, source: { ...deezer, name: 'mgk' } });
    renderSetting();
    expect(await screen.findByText('Filed and tagged as')).toBeInTheDocument();
    expect(screen.queryByText('Deezer calls them')).toBeNull();
  });

  it('only claims a rename the rule makes', () => {
    expect(namingSentence({ ...automatic, renamed: false, source: null })).toBeNull();
    expect(
      namingSentence({ ...automatic, renamed: false, source: { ...deezer, name: 'mgk' } }),
    ).toBeNull();
    expect(namingSentence({ ...automatic, renamed: false, kept_source_name: true })).toBe(
      'Deezer calls them Machine Gun Kelly, and so does another artist in your library. Set a name here to choose.',
    );
    expect(namingSentence({ ...automatic, source: null, renamed: false, pinned: true })).toBe(
      'New downloads are filed and tagged as mgk, the name you set.',
    );
  });
});
