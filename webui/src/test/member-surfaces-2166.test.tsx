/**
 * what a member sees, per permission (#2166, piece F). no dead buttons: a
 * control is there when the profile can use it, says Request when it asks
 * first, and is simply absent when it can't. every case runs on a mocked
 * GET /api/profiles/me/permissions map (src/test/perms.ts).
 */

import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { RequestItem } from '@/routes/requests/-requests.types';
import type { SearchAlbum, SearchTrack } from '@/routes/search/-search.types';

import { NotPartOfProfile } from '@/components/not-part-of-profile/not-part-of-profile';
import { useMusicGet } from '@/platform/shell/download-rights';
import { _resetAdminName } from '@/platform/shell/my-permissions';
import { OwnedTopTracksDownloadAll } from '@/routes/library/-ui/owned-artist-top-tracks';
import { canAnswer, whoAsked } from '@/routes/requests/-requests.helpers';
import { EMPTY_OWNERSHIP, SearchResults } from '@/routes/search/-ui/search-results';
import { VideoGrid } from '@/routes/search/-ui/video-grid';

import { server } from './msw';
import {
  ADULT,
  BOOKS_OFF,
  LISTEN_ONLY,
  MODERATOR,
  SEE_ALL,
  TEEN,
  clearPerms,
  stubPerms,
} from './perms';

const album = (over: Partial<SearchAlbum> = {}): SearchAlbum => ({
  id: 'a1',
  name: 'Drukqs',
  artist: 'Aphex Twin',
  album_type: 'album',
  source: 'spotify',
  ...over,
});

const track = (over: Partial<SearchTrack> = {}): SearchTrack =>
  ({
    id: 't1',
    name: 'Avril 14th',
    artist: 'Aphex Twin',
    album: 'Drukqs',
    duration_ms: 125000,
    source: 'spotify',
    ...over,
  }) as SearchTrack;

function renderResults(props: Partial<Parameters<typeof SearchResults>[0]> = {}) {
  return render(
    <SearchResults
      activeSource="spotify"
      dbArtists={[]}
      artists={[]}
      albums={[]}
      tracks={[]}
      labels={[]}
      videos={[]}
      videoProgress={{}}
      ownership={EMPTY_OWNERSHIP}
      artistImages={{}}
      onArtistHref={() => '/a'}
      onLabelHref={() => '/l'}
      onAlbumClick={vi.fn()}
      onTrackClick={vi.fn()}
      onTrackPlay={vi.fn()}
      onVideoDownload={vi.fn()}
      {...props}
    />,
  );
}

beforeEach(() => {
  _resetAdminName();
  server.use(
    http.get('/api/profiles', () =>
      HttpResponse.json({ success: true, profiles: [{ id: 1, name: 'Boulder', is_admin: true }] }),
    ),
  );
});

afterEach(() => {
  cleanup();
  clearPerms();
});

describe('search: getting music, three ways', () => {
  const albums = [album({ id: '1', name: 'Full Length' }), album({ id: '2', name: 'Second' })];

  it('download right away: Download on the cover and the top result', () => {
    stubPerms(ADULT);
    renderResults({ albums });
    expect(screen.getByRole('button', { name: 'Download Full Length' })).toBeInTheDocument();
  });

  it('ask first: the same controls say Request', () => {
    stubPerms(TEEN);
    renderResults({ albums });
    expect(screen.getByRole('button', { name: 'Request Full Length' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Download / })).toBeNull();
  });

  it('can’t request: no cover button, the top album only opens', () => {
    // the same page with asks has one cover button per album more
    stubPerms(TEEN);
    const asking = renderResults({ albums });
    const withButtons = document.querySelectorAll('#enh-albums-section button').length;
    asking.unmount();
    stubPerms(LISTEN_ONLY);
    renderResults({ albums });
    expect(document.querySelectorAll('#enh-albums-section button').length).toBe(
      withButtons - albums.length,
    );
    expect(screen.queryByRole('button', { name: /^(Download|Request) / })).toBeNull();
    const topResult = document.getElementById('enh-top-result') as HTMLElement;
    expect(topResult.textContent).toContain('Open album');
    expect(topResult.textContent).not.toMatch(/Download|Request/);
  });

  it('can’t request: the top track keeps Play, loses the download', () => {
    stubPerms(LISTEN_ONLY);
    renderResults({ tracks: [track()] });
    const topResult = document.getElementById('enh-top-result') as HTMLElement;
    expect(topResult.textContent).toContain('Play');
    expect(topResult.textContent).not.toMatch(/Download|Request/);
    // the track row has no download button either, the row still opens
    expect(screen.queryByRole('button', { name: /^(Download|Request) Avril/ })).toBeNull();
  });

  it('an admin changing the map re-renders the page in place', () => {
    stubPerms(TEEN);
    renderResults({ albums });
    expect(screen.getByRole('button', { name: 'Request Full Length' })).toBeInTheDocument();
    act(() => {
      stubPerms(ADULT);
    });
    expect(screen.getByRole('button', { name: 'Download Full Length' })).toBeInTheDocument();
  });
});

describe('music videos in search', () => {
  const video = { video_id: 'abc', title: 'Windowlicker', url: 'https://youtu.be/abc' };

  it('a download card when they can get music', () => {
    render(<VideoGrid videos={[video]} progress={{}} onDownload={vi.fn()} />);
    expect(screen.getByRole('button', { name: 'Download Windowlicker' })).toBeInTheDocument();
  });

  it('a watch card, not a dead download, when they can’t', () => {
    const open = vi.spyOn(window, 'open').mockImplementation(() => null);
    render(<VideoGrid videos={[video]} progress={{}} />);
    const card = screen.getByRole('button', { name: 'Watch Windowlicker on YouTube' });
    card.click();
    expect(open).toHaveBeenCalledWith('https://youtu.be/abc', '_blank', 'noopener,noreferrer');
    open.mockRestore();
  });
});

describe('library artist top tracks', () => {
  const tracks = [{ name: 'Xtal' }];

  it('Download All / Request All / nothing', () => {
    stubPerms(ADULT);
    const { unmount } = render(
      <OwnedTopTracksDownloadAll tracks={tracks} source="spotify" artistName="A" artistId={1} />,
    );
    expect(screen.getByRole('button', { name: 'Download All' })).toBeInTheDocument();
    unmount();
    stubPerms(TEEN);
    const second = render(
      <OwnedTopTracksDownloadAll tracks={tracks} source="spotify" artistName="A" artistId={1} />,
    );
    expect(screen.getByRole('button', { name: 'Request All' })).toBeInTheDocument();
    second.unmount();
    stubPerms(LISTEN_ONLY);
    render(
      <OwnedTopTracksDownloadAll tracks={tracks} source="spotify" artistName="A" artistId={1} />,
    );
    expect(screen.queryByRole('button')).toBeNull();
  });
});

describe('the calm card', () => {
  it('books off: names the admin and what is still theirs', async () => {
    stubPerms(BOOKS_OFF);
    render(<NotPartOfProfile media="books" />);
    expect(screen.getByRole('heading').textContent).toBe('Audiobooks aren’t part of your profile');
    await waitFor(() =>
      expect(screen.getByTestId('not-part-of-profile').textContent).toContain(
        'Ask Boulder if you’d like them.',
      ),
    );
    expect(screen.getByTestId('not-part-of-profile').textContent).toContain(
      'Music, movies, TV shows and podcasts are all still yours.',
    );
  });

  it('a server refusal shows its own words', () => {
    stubPerms(TEEN);
    render(<NotPartOfProfile media="books" message="Getting books isn't part of your profile." />);
    expect(screen.getByRole('heading').textContent).toBe(
      "Getting books isn't part of your profile.",
    );
  });
});

describe('the hook', () => {
  function Probe() {
    const get = useMusicGet();
    return <span data-testid="probe">{`${get.mode}:${get.verb}:${get.cantRequest}`}</span>;
  }

  it('follows the store and the profile columns before it', () => {
    clearPerms();
    vi.stubGlobal('canDownload', () => false);
    const { rerender } = render(<Probe />);
    expect(screen.getByTestId('probe').textContent).toBe('ask:Request:false');
    vi.unstubAllGlobals();
    act(() => {
      stubPerms(LISTEN_ONLY);
    });
    rerender(<Probe />);
    expect(screen.getByTestId('probe').textContent).toBe('none:Request:true');
  });
});

describe('requests: who answers, who sees', () => {
  const pending = (profileId: number, name: string): RequestItem =>
    ({
      source: 'pending',
      status: 'pending',
      group: {
        key: 'k',
        profile_id: profileId,
        requester_name: name,
        kind: 'album',
        title: 'Drukqs',
        track_ids: [],
        tracks: [],
        track_count: 0,
        status: 'pending',
      },
    }) as RequestItem;

  it('admins answer anyone', () => {
    expect(canAnswer(pending(7, 'Maya'), { isAdmin: true, moderate: true }, 1)).toBe(true);
  });

  it('a moderator answers other people, never their own', () => {
    expect(canAnswer(pending(9, 'Liam'), { isAdmin: false, moderate: true }, 7)).toBe(true);
    expect(canAnswer(pending(7, 'Maya'), { isAdmin: false, moderate: true }, 7)).toBe(false);
  });

  it('a member answers nothing', () => {
    expect(canAnswer(pending(9, 'Liam'), { isAdmin: false, moderate: false }, 7)).toBe(false);
  });

  it('see everyone shows names, and your own as You', () => {
    expect(whoAsked(pending(9, 'Liam'), true, Date.now(), 7)).toMatch(/^Liam asked/);
    expect(whoAsked(pending(7, 'Maya'), true, Date.now(), 7)).toMatch(/^You asked/);
    expect(whoAsked(pending(9, 'Liam'), false, Date.now(), 7)).toMatch(/^You asked/);
  });

  it('the fixtures agree with the roles', () => {
    expect(MODERATOR['requests.moderate']).toBe(true);
    expect(SEE_ALL['requests.moderate']).toBe(false);
  });
});
