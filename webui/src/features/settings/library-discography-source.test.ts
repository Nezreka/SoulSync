import { fireEvent, waitFor } from '@testing-library/dom';
import { describe, expect, it, vi } from 'vitest';

import {
  LIBRARY_DISCOGRAPHY_SOURCE_OPTIONS,
  mountLibraryDiscographySourceSelector,
  normalizeLibraryDiscographySource,
} from './library-discography-source';

describe('Library discography source selector', () => {
  it('normalizes unknown values to the primary metadata source', () => {
    expect(normalizeLibraryDiscographySource('deezer')).toBe('deezer');
    expect(normalizeLibraryDiscographySource('not-valid')).toBe('primary');
    expect(normalizeLibraryDiscographySource(undefined)).toBe('primary');
  });

  it('renders directly below the primary metadata source', async () => {
    document.body.innerHTML = `
      <div id="metadata-settings">
        <div class="form-group" id="primary-group">
          <label for="metadata-fallback-source">Primary metadata source:</label>
          <select id="metadata-fallback-source"><option value="musicbrainz">MusicBrainz</option></select>
        </div>
      </div>
    `;

    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({
          metadata: {
            fallback_source: 'musicbrainz',
            library_discography_source: 'automatic',
          },
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    );

    const select = await mountLibraryDiscographySourceSelector({ fetchImpl });

    expect(select).not.toBeNull();
    expect(select?.value).toBe('automatic');
    expect(select?.parentElement?.previousElementSibling?.id).toBe('primary-group');
    expect(Array.from(select?.options ?? []).map((option) => option.value)).toEqual(
      LIBRARY_DISCOGRAPHY_SOURCE_OPTIONS.map((option) => option.value),
    );
  });

  it('persists the independent setting without replacing other metadata settings', async () => {
    document.body.innerHTML = `
      <div class="form-group">
        <label for="metadata-fallback-source">Primary metadata source:</label>
        <select id="metadata-fallback-source"><option value="musicbrainz">MusicBrainz</option></select>
      </div>
    `;

    const settings = {
      metadata: {
        fallback_source: 'musicbrainz',
        spotify_free: false,
        library_discography_source: 'primary',
      },
      active_media_server: 'plex',
    };
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        new Response(JSON.stringify(settings), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify(settings), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ success: true }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      );

    const select = await mountLibraryDiscographySourceSelector({ fetchImpl });
    expect(select).not.toBeNull();

    if (!select) throw new Error('Selector was not mounted');
    fireEvent.change(select, { target: { value: 'deezer' } });

    await waitFor(() => expect(fetchImpl).toHaveBeenCalledTimes(3));

    const [, request] = fetchImpl.mock.calls[2];
    const payload = JSON.parse(String(request?.body));
    expect(payload.metadata).toEqual({
      fallback_source: 'musicbrainz',
      spotify_free: false,
      library_discography_source: 'deezer',
    });
    expect(payload.active_media_server).toBe('plex');
  });
});
