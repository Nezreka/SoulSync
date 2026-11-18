/**
 * #2188: audiobooks and podcast episodes have Ask first. a member on it keeps
 * the heart and the episode button, and they ask an admin instead of
 * downloading; the asks land on the Requests page beside music.
 */

import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { AudiobookItem } from '@/routes/audiobooks/-audiobooks.types';
import type { MediaRequest, MusicRequestList } from '@/routes/requests/-requests.types';

import { _resetAdminName } from '@/platform/shell/my-permissions';
import { AudiobookProvider } from '@/routes/audiobooks/-ui/audiobook-context';
import { AudiobookWishlistButton } from '@/routes/audiobooks/-ui/audiobook-wishlist-button';
import * as podcastApi from '@/routes/podcasts/-podcasts.api';
import { PodcastProvider, usePodcastContext } from '@/routes/podcasts/-ui/podcast-context';
import {
  buildRequestItems,
  canAnswer,
  itemKind,
  kindLabel,
  subLine,
  whoAsked,
} from '@/routes/requests/-requests.helpers';

import { server } from './msw';
import { ADULT, clearPerms, stubPerms } from './perms';

const book = {
  asin: 'B1',
  title: 'Wind and Truth',
  source: 'audible',
  author_names: ['Brandon Sanderson'],
  narrator_names: [],
} as unknown as AudiobookItem;

let toast: ReturnType<typeof vi.fn>;
let posted: unknown[];

beforeEach(() => {
  toast = vi.fn();
  window.showToast = toast as unknown as typeof window.showToast;
  posted = [];
  _resetAdminName();
  server.use(
    http.get('/api/profiles', () =>
      HttpResponse.json({ success: true, profiles: [{ id: 1, name: 'Boulder', is_admin: true }] }),
    ),
    http.get('/api/audiobooks/wishlist', () =>
      HttpResponse.json({ success: true, items: [], counts: {}, requested: [] }),
    ),
    http.post('/api/audiobooks/wishlist', async ({ request }) => {
      posted.push(await request.json());
      return HttpResponse.json({ success: true, requested: true, wishlisted: false });
    }),
  );
});

afterEach(() => {
  cleanup();
  clearPerms();
  delete window.showToast;
  vi.restoreAllMocks();
});

describe('a book on Ask first', () => {
  it('the heart says Request, asks the admin, then says Requested', async () => {
    stubPerms({ ...ADULT, 'books.get': 'ask' });
    await act(async () => {
      render(
        <AudiobookProvider>
          <AudiobookWishlistButton book={book} variant="full" />
        </AudiobookProvider>,
      );
    });
    const button = screen.getByRole('button', { name: /Request/ });
    // the css keeps it for an ask first profile, unlike a grab button
    expect(button).toHaveAttribute('data-books-asks');
    fireEvent.click(button);
    await waitFor(() => expect(screen.getByRole('button', { name: /Requested/ })).toBeDisabled());
    expect(posted).toEqual([{ asin: 'B1', narrator_mode: 'exact' }]);
    expect(toast).toHaveBeenCalledWith(
      'Asked Boulder for "Wind and Truth". You\'ll hear when it\'s approved',
      'success',
    );
  });

  it('a profile that downloads still wishlists', async () => {
    stubPerms(ADULT);
    await act(async () => {
      render(
        <AudiobookProvider>
          <AudiobookWishlistButton book={book} variant="full" />
        </AudiobookProvider>,
      );
    });
    expect(screen.getByRole('button', { name: /Add to wishlist/ })).toBeInTheDocument();
  });
});

function EpisodeProbe() {
  const { handleDownloadEpisode } = usePodcastContext();
  return (
    <button
      onClick={() =>
        handleDownloadEpisode(
          {
            guid: 'ep-12',
            title: 'Episode 12',
            enclosure_url: 'https://cdn.example/ep12.mp3',
            enclosure_type: 'audio/mpeg',
            description: '',
            show_notes: '',
            episode_type: 'full',
            duration_seconds: 120,
          },
          'Some Show',
          null,
          'https://feeds.example/show.xml',
        )
      }
    >
      Get it
    </button>
  );
}

describe('an episode on Ask first', () => {
  it('asks the admin, with the feed so the request knows when it lands', async () => {
    stubPerms({ ...ADULT, 'podcasts.get': 'ask' });
    vi.spyOn(podcastApi, 'fetchPodcastDownloads').mockResolvedValue([]);
    vi.spyOn(podcastApi, 'fetchWatchlistPodcasts').mockResolvedValue([]);
    const download = vi
      .spyOn(podcastApi, 'downloadPodcastEpisode')
      .mockResolvedValue({ success: true, requested: true });
    await act(async () => {
      render(
        <PodcastProvider>
          <EpisodeProbe />
        </PodcastProvider>,
      );
    });
    fireEvent.click(screen.getByRole('button', { name: 'Get it' }));
    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith(
        'Asked Boulder for "Episode 12". You\'ll hear when it\'s approved',
        'success',
      ),
    );
    expect(download.mock.calls[0][2]).toBe('https://feeds.example/show.xml');
  });
});

describe('book and episode asks on the Requests page', () => {
  const ask = (over: Partial<MediaRequest> = {}): MediaRequest => ({
    id: 5,
    profile_id: 7,
    requester_name: 'Maya',
    kind: 'audiobook',
    item_key: 'B1',
    title: 'Wind and Truth',
    subtitle: 'Brandon Sanderson',
    status: 'pending',
    created_at: '2026-11-18 10:00:00',
    ...over,
  });
  const list = (media: MediaRequest[]): MusicRequestList => ({
    pending: [],
    history: [],
    pendingVideos: [],
    videoHistory: [],
    pendingMedia: media.filter((m) => m.status === 'pending'),
    mediaHistory: media.filter((m) => m.status !== 'pending'),
    counts: { pending: 0, approved: 0, available: 0, declined: 0, removed: 0 },
    asksFirst: true,
    quota: null,
  });

  it('wait with the rest, say what they are, and are answered like music', () => {
    const items = buildRequestItems(
      list([
        ask(),
        ask({ id: 6, kind: 'podcast', title: 'Episode 12', subtitle: 'Some Show' }),
        ask({ id: 7, status: 'declined', resolved_at: '2026-11-18 11:00:00' }),
      ]),
    );
    expect(items.map((i) => i.source)).toEqual(['media-pending', 'media-pending', 'media-history']);
    expect(items.map((i) => kindLabel(itemKind(i)))).toEqual([
      'Audiobook',
      'Podcast episode',
      'Audiobook',
    ]);
    expect(subLine(items[0])).toBe('Brandon Sanderson');
    const now = Date.parse('2026-11-18T12:00:00Z');
    expect(whoAsked(items[0], true, now, 1)).toBe('Maya asked · 2 hours ago');
    expect(canAnswer(items[0], { isAdmin: true, moderate: false }, 1)).toBe(true);
    // a moderator never answers their own
    expect(canAnswer(items[0], { isAdmin: false, moderate: true }, 7)).toBe(false);
    expect(canAnswer(items[2], { isAdmin: true, moderate: false }, 1)).toBe(false);
  });
});
