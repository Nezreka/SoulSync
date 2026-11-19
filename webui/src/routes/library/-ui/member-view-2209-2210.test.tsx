/**
 * #2209: a member without the Watchlist part got Add to / Remove from
 * Watchlist on the Library and a success. #2210: a member got the track
 * editor's inputs and quality profile picker, which only answered "library
 * changes require the admin profile". both now show only what they can do.
 */
import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';
import { ADULT, clearPerms, stubPerms } from '@/test/perms';
import { createTestQueryClient } from '@/test/query-client';

import type { LibraryV2AlbumDetail, LibraryV2ArtistSummary } from '../-library-v2.types';

import { ArtistBulkBar, dismissBulkRun, type ArtistSelection } from './library-bulk';
import {
  AlbumTrackTable,
  LibraryV2CanWatchContext,
  LibraryV2CanWishContext,
  LibraryV2CanWriteContext,
  MonitorToggle,
} from './library-v2-page';
import { QualityProfilePicker } from './quality-profile-modal';

afterEach(() => {
  clearPerms();
  dismissBulkRun();
  Reflect.deleteProperty(window, 'showToast');
});

function asMember(node: React.ReactNode, { canWatch }: { canWatch: boolean }) {
  return render(
    <QueryClientProvider client={createTestQueryClient()}>
      <LibraryV2CanWriteContext.Provider value={false}>
        <LibraryV2CanWishContext.Provider value>
          <LibraryV2CanWatchContext.Provider value={canWatch}>
            {node}
          </LibraryV2CanWatchContext.Provider>
        </LibraryV2CanWishContext.Provider>
      </LibraryV2CanWriteContext.Provider>
    </QueryClientProvider>,
  );
}

function selection(ids: number[]): ArtistSelection {
  return { selected: new Set(ids), toggle: vi.fn(), setMany: vi.fn(), clear: vi.fn() };
}

const artistRow = { id: 5, name: 'Amtrac', monitored: false } as LibraryV2ArtistSummary;

function bulkBar(canWatch: boolean) {
  return (
    <ArtistBulkBar
      selection={selection([5])}
      pageArtists={[artistRow]}
      totalMatching={1}
      filters={{ q: '', sort: 'name', monitored: 'all' } as never}
      canWrite={false}
      canWish
      canWatch={canWatch}
      canSearch={false}
    />
  );
}

describe('#2209 the Watchlist on the Library', () => {
  it('a member without the Watchlist gets no watchlist toggle and no bulk watchlist actions', () => {
    stubPerms({ ...ADULT, 'music.watchlist': false });
    asMember(
      <>
        <MonitorToggle entity="artists" id={5} monitored={false} />
        <MonitorToggle entity="albums" id={6} monitored={false} />
        {bulkBar(false)}
      </>,
      { canWatch: false },
    );
    expect(screen.queryByRole('button', { name: /Watchlist/ })).toBeNull();
    // the Wishlist is still theirs
    expect(screen.getByRole('button', { name: 'Add to Wishlist' })).toBeEnabled();
    // and refreshing a discography is the admin's
    expect(screen.queryByRole('button', { name: 'Refresh discography' })).toBeNull();
  });

  it('follows the live map: the server said yes, the admin turned it off since', () => {
    stubPerms({ ...ADULT, 'music.watchlist': false });
    asMember(<MonitorToggle entity="artists" id={5} monitored={false} />, { canWatch: true });
    expect(screen.queryByRole('button', { name: /Watchlist/ })).toBeNull();
  });

  it('a member with the Watchlist reads "your Watchlist" and sees their own state', () => {
    stubPerms({ ...ADULT, 'music.watchlist': true });
    asMember(
      <>
        <MonitorToggle entity="artists" id={5} monitored />
        {bulkBar(true)}
      </>,
      { canWatch: true },
    );
    expect(screen.getByRole('button', { name: 'Remove from Watchlist' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(screen.getByRole('button', { name: 'Add to your Watchlist' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Remove from your Watchlist' })).toBeEnabled();
  });

  it('a refused add shows as failed with the reason, never as done', async () => {
    stubPerms({ ...ADULT, 'music.watchlist': true });
    const toast = vi.fn();
    window.showToast = toast;
    server.use(
      http.post('/api/library/v2/artists/5/monitor', () =>
        HttpResponse.json(
          {
            success: false,
            error: 'not_allowed',
            permission: 'music.watchlist',
            message: "Watchlist isn't part of your profile. Ask an admin if you'd like it.",
          },
          { status: 403 },
        ),
      ),
    );
    asMember(bulkBar(true), { canWatch: true });
    fireEvent.click(screen.getByRole('button', { name: 'Add to your Watchlist' }));
    const status = await screen.findByRole('status');
    await waitFor(() => expect(status).toHaveTextContent(/1 failed, finished/));
    expect(status).toHaveTextContent(/Watchlist isn't part of your profile/);
    expect(toast).toHaveBeenCalledWith(
      expect.stringMatching(/0 artists done, 1 failed/),
      'warning',
    );
  });
});

describe('#2210 the track editor for a member', () => {
  function album(): LibraryV2AlbumDetail {
    return {
      id: 42,
      title: 'Season of Surrender',
      album_type: 'album',
      release_date: null,
      year: null,
      image_url: null,
      genres: [],
      explicit: null,
      label: null,
      style: null,
      mood: null,
      monitored: false,
      origin: 'library',
      quality_profile: null,
      primary_artist: null,
      tracks: [
        {
          id: 7,
          title: 'Legions',
          track_number: 1,
          disc_number: null,
          duration: null,
          bpm: null,
          explicit: null,
          style: null,
          mood: 'heavy',
          isrc: null,
          monitored: false,
          quality_profile_id: 1,
          quality_profile_source: 'artist',
          canonical_track_id: null,
          artists: [],
          metadata_gaps: [],
          file_status: 'present',
          file: {
            file_id: 70,
            path: '/music/legions.mp3',
            format: 'mp3',
            bitrate: 320_000,
            sample_rate: 44_100,
            bit_depth: null,
            size: 1024,
            quality_tier: 'lossy',
            import_status: 'imported',
            verification_status: null,
            acoustid_status: null,
            pipeline_result: {},
            source: null,
            file_state: 'active',
          },
        },
      ],
      track_count: 1,
      tracks_present: 1,
      tracks_missing: 0,
      total_size_bytes: 0,
      user_overrides: {},
    } as LibraryV2AlbumDetail;
  }

  function serve(writes: string[]) {
    server.use(
      http.get('/api/library/v2/albums/42', () =>
        HttpResponse.json({ success: true, album: album() }),
      ),
      http.get('/api/library/v2/albums/42/match-status', () =>
        HttpResponse.json({ success: true, album: [], tracks: {} }),
      ),
      http.get('/api/library/v2/quality-profiles', () =>
        HttpResponse.json({
          success: true,
          profiles: [
            { id: 1, name: 'Default', upgrade_policy: 'until_top', upgrade_cutoff_index: 0 },
            { id: 2, name: 'Upgrade until top quality', upgrade_policy: 'until_top' },
          ],
        }),
      ),
      http.get('/api/library/v2/ui-preferences', () =>
        HttpResponse.json({
          success: true,
          preferences: { track_table: { edit_via_title: true } },
        }),
      ),
      http.get('/api/library/v2/albums/42/queue-status', () =>
        HttpResponse.json({ tracks: {}, albums: {} }),
      ),
      http.get('/api/library/v2/tracks/7/source-info', () =>
        HttpResponse.json({ success: true, downloads: [], manual_skips: [] }),
      ),
      http.get('/api/library/v2/tracks/7/history', () =>
        HttpResponse.json({ success: true, history: [] }),
      ),
      http.get('/api/library/v2/tracks/7/file-tags', () =>
        HttpResponse.json({ success: true, available: true, tags: { title: 'Legions' } }),
      ),
      http.post('/api/library/v2/*', ({ request }) => {
        writes.push(request.url);
        return HttpResponse.json({ success: false }, { status: 403 });
      }),
      http.patch('/api/library/v2/*', ({ request }) => {
        writes.push(request.url);
        return HttpResponse.json({ success: false }, { status: 403 });
      }),
    );
  }

  it('shows the track as text: no profile to pick, no inputs, nothing to save', async () => {
    const writes: string[] = [];
    serve(writes);
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <AlbumTrackTable albumId={42} onAction={vi.fn()} />
      </QueryClientProvider>,
    );
    const title = await screen.findByRole('button', { name: 'Legions' });
    expect(title).toHaveAttribute('title', expect.stringMatching(/^Track details/));
    fireEvent.click(title);
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByRole('heading', { name: /^Track — Legions/ })).toBeInTheDocument();
    expect(
      await within(dialog).findByText(/Effective: Default \(Inherited from artist\)/),
    ).toBeInTheDocument();
    expect(within(dialog).queryByText('Upgrade until top quality')).toBeNull();
    expect(within(dialog).queryByRole('button', { name: /Use inherited profile/ })).toBeNull();
    expect(within(dialog).queryByRole('button', { name: /Delete File/ })).toBeNull();

    fireEvent.click(within(dialog).getByRole('button', { name: 'Metadata & Tags' }));
    expect(await within(dialog).findByTestId('read-only-metadata')).toHaveTextContent(/Legions/);
    expect(within(dialog).getByTestId('read-only-metadata')).toHaveTextContent(/heavy/);
    expect(within(dialog).queryByRole('textbox')).toBeNull();
    expect(within(dialog).queryByRole('spinbutton')).toBeNull();
    expect(within(dialog).queryByRole('button', { name: 'Save' })).toBeNull();
    expect(within(dialog).queryByRole('button', { name: /Write Tags to File/ })).toBeNull();
    expect(writes).toEqual([]);
  });

  it('the admin keeps the editor', async () => {
    serve([]);
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <LibraryV2CanWriteContext.Provider value>
          <AlbumTrackTable albumId={42} onAction={vi.fn()} />
        </LibraryV2CanWriteContext.Provider>
      </QueryClientProvider>,
    );
    fireEvent.click(await screen.findByRole('button', { name: 'Legions' }));
    const dialog = await screen.findByRole('dialog');
    expect(
      within(dialog).getByRole('heading', { name: /^Edit Track — Legions/ }),
    ).toBeInTheDocument();
    expect(await within(dialog).findByText('Upgrade until top quality')).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Metadata & Tags' }));
    expect(await within(dialog).findByLabelText('Title')).toHaveValue('Legions');
    expect(within(dialog).getByRole('button', { name: 'Save' })).toBeInTheDocument();
  });
});

describe('#2210 the quality profile picker read-only', () => {
  it('names the profile in use and offers nothing', async () => {
    server.use(
      http.get('/api/library/v2/quality-profiles', () =>
        HttpResponse.json({
          success: true,
          profiles: [
            { id: 1, name: 'Default', upgrade_policy: 'none' },
            { id: 2, name: 'Hi-Res', upgrade_policy: 'none' },
          ],
        }),
      ),
    );
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <QualityProfilePicker entity="albums" id={3} currentProfileId={1} readOnly />
      </QueryClientProvider>,
    );
    expect(await screen.findByText('Upgrades disabled')).toBeInTheDocument();
    expect(screen.getAllByText(/Default/).length).toBeGreaterThan(0);
    expect(screen.queryByText('Hi-Res')).toBeNull();
    expect(screen.queryByRole('button')).toBeNull();
  });
});
