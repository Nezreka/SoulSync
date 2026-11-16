import type { ReactElement } from 'react';

import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render as rtlRender, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createTestQueryClient } from '@/test/query-client';

import type { ArtistReview, LockState } from '../-review-lock.api';

const api = vi.hoisted(() => ({
  fetchLock: vi.fn(),
  saveLock: vi.fn(),
  removeLock: vi.fn(),
  fetchLockedIds: vi.fn(),
  fetchArtistReview: vi.fn(),
}));
const shell = vi.hoisted(() => ({ admin: true }));

vi.mock('@/platform/shell/bridge', async (original) => ({
  ...(await original<typeof import('@/platform/shell/bridge')>()),
  getShellProfileContext: () => ({ isAdmin: shell.admin }),
}));

vi.mock('../-review-lock.api', async (original) => {
  const real = await original<typeof import('../-review-lock.api')>();
  return {
    ...real,
    fetchLock: api.fetchLock,
    saveLock: api.saveLock,
    removeLock: api.removeLock,
    fetchArtistReview: api.fetchArtistReview,
    lockQueryOptions: (entity: 'album' | 'artist', id: number) => ({
      queryKey: ['t', 'lock', entity, id],
      queryFn: () => api.fetchLock(entity, id),
    }),
    lockedIdsQueryOptions: () => ({ queryKey: ['t', 'ids'], queryFn: api.fetchLockedIds }),
    artistReviewQueryOptions: (artistId: number) => ({
      queryKey: ['t', 'review', artistId],
      queryFn: () => api.fetchArtistReview(artistId),
      enabled: artistId > 0,
    }),
  };
});

import { completionOverlay } from './library-v2-page';
import { Padlock, ReviewLockButton, lockDate, lockedPresence } from './review-lock';

const OPTIONS = [
  ['tags', 'Tags in the files'],
  ['artwork', 'Artwork'],
  ['metadata', 'Library details'],
  ['files', 'File names and folders'],
  ['match', 'Release match'],
  ['upgrades', 'Quality upgrades'],
].map(([scope, label]) => ({ scope, label })) as LockState['scope_options'];

function lock(over: Partial<LockState> = {}): LockState {
  return {
    entity_type: 'album',
    entity_id: 5,
    locked: false,
    scopes: [],
    locked_at: null,
    locked_by_profile_id: null,
    changed_since_review: false,
    field_locks: { artwork: false, genres: false, edition: false },
    scope_options: OPTIONS,
    skips: [],
    can_lock: true,
    ...over,
  };
}

const REVIEW: ArtistReview = {
  albums: 3,
  reviewed: 1,
  reviewed_album_ids: [5],
  changed_album_ids: [],
  next_album_id: 9,
};

function render(ui: ReactElement) {
  return rtlRender(
    <QueryClientProvider client={createTestQueryClient()}>{ui}</QueryClientProvider>,
  );
}

const toast = vi.fn();

beforeEach(() => {
  shell.admin = true;
  Object.values(api).forEach((mock) => mock.mockReset());
  api.fetchLockedIds.mockResolvedValue({ albums: [], artists: [] });
  api.fetchArtistReview.mockResolvedValue(REVIEW);
  api.fetchLock.mockResolvedValue(lock());
  toast.mockReset();
  window.showToast = toast as never;
});

afterEach(() => vi.restoreAllMocks());

const switches = () =>
  screen
    .getAllByRole('switch')
    .map((el) => [
      within(el).getByText(/./, { selector: 'strong' }).textContent,
      el.getAttribute('aria-checked'),
    ]);

describe('the padlock (#2046)', () => {
  it('an open album offers to lock it, with every part switched on', async () => {
    api.saveLock.mockResolvedValue(lock({ locked: true, scopes: OPTIONS.map((o) => o.scope) }));
    render(<ReviewLockButton entity="album" id={5} name="Views" variant="hero" />);

    const button = await screen.findByRole('button', { name: 'Lock Views as reviewed' });
    expect(button.getAttribute('aria-pressed')).toBe('false');
    fireEvent.click(button);

    await screen.findByRole('dialog', { name: 'Lock Views' });
    await waitFor(() => expect(screen.getAllByRole('switch')).toHaveLength(6));
    expect(switches().every(([, on]) => on === 'true')).toBe(true);
    // what a lock does not stop is said on the sheet
    expect(screen.getByText(/Missing tracks still download/)).toBeTruthy();

    fireEvent.click(screen.getByRole('switch', { name: /Quality upgrades/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Lock' }));

    await waitFor(() => expect(api.saveLock).toHaveBeenCalledTimes(1));
    expect(api.saveLock).toHaveBeenCalledWith('album', 5, {
      scopes: ['tags', 'artwork', 'metadata', 'files', 'match'],
    });
    await waitFor(() => expect(toast).toHaveBeenCalledWith('Views is locked', 'success'));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('a locked album shows its switches, what was kept away, and unlocks', async () => {
    api.fetchLockedIds.mockResolvedValue({ albums: [5], artists: [] });
    api.fetchLock.mockResolvedValue(
      lock({
        locked: true,
        scopes: ['tags', 'files'],
        locked_at: '2026-11-10 08:00:00',
        field_locks: { artwork: true, genres: false, edition: false },
        skips: [
          {
            scope: 'tags',
            what: 'Genre cleanup',
            first_at: null,
            last_at: '2026-11-12 09:00:00',
            times: 3,
          },
        ],
      }),
    );
    api.removeLock.mockResolvedValue(lock());
    render(<ReviewLockButton entity="album" id={5} name="Views" />);

    fireEvent.click(await screen.findByRole('button', { name: 'Views is reviewed and locked' }));
    await screen.findByRole('dialog', { name: 'Views is locked' });
    await waitFor(() => expect(screen.getAllByRole('switch')).toHaveLength(6));
    expect(
      switches()
        .filter(([, on]) => on === 'true')
        .map(([name]) => name),
    ).toEqual(['Tags in the files', 'File names and folders']);
    expect(screen.getByText('Genre cleanup')).toBeTruthy();
    expect(screen.getByText(/3 times/)).toBeTruthy();
    expect(screen.getByText('Artwork you picked')).toBeTruthy();
    // nothing changed yet, so there is nothing to save
    expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(true);

    fireEvent.click(screen.getByRole('switch', { name: /Artwork/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() =>
      expect(api.saveLock).toHaveBeenCalledWith('album', 5, {
        scopes: ['tags', 'artwork', 'files'],
        review: false,
      }),
    );

    fireEvent.click(screen.getByRole('button', { name: 'Unlock' }));
    await waitFor(() => expect(api.removeLock).toHaveBeenCalledWith('album', 5));
  });

  it('says when the files changed after the review and can mark it reviewed again', async () => {
    api.fetchLockedIds.mockResolvedValue({ albums: [5], artists: [] });
    api.fetchArtistReview.mockResolvedValue({ ...REVIEW, changed_album_ids: [5] });
    const held = lock({ locked: true, scopes: ['tags'], changed_since_review: true });
    api.fetchLock.mockResolvedValue(held);
    api.saveLock.mockResolvedValue({ ...held, changed_since_review: false });
    render(<ReviewLockButton entity="album" id={5} name="Views" artistId={2} variant="hero" />);

    fireEvent.click(
      await screen.findByRole('button', { name: 'Views changed since it was reviewed' }),
    );
    expect(await screen.findByText('Changed since your review')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Mark reviewed again' }));
    // a new review: no review:false, so the date and snapshot move
    await waitFor(() =>
      expect(api.saveLock).toHaveBeenCalledWith('album', 5, { scopes: ['tags'] }),
    );
  });

  it('lock and next opens the next album that is not reviewed', async () => {
    api.saveLock.mockResolvedValue(lock({ locked: true, scopes: ['tags'] }));
    const openAlbum = vi.fn();
    render(
      <ReviewLockButton
        entity="album"
        id={5}
        name="Views"
        artistId={2}
        variant="hero"
        onOpenAlbum={openAlbum}
      />,
    );
    fireEvent.click(await screen.findByRole('button', { name: 'Lock Views as reviewed' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Lock and next' }));
    await waitFor(() => expect(openAlbum).toHaveBeenCalledWith(9));
    expect(api.fetchArtistReview).toHaveBeenCalledWith(2, 5);
  });

  it('#2143 locking from an album row leaves the row alone and stays on the page', async () => {
    // the album row toggles on a click and opens the album page on two. the
    // sheet is portaled, but react still bubbles its clicks to the row.
    api.saveLock.mockResolvedValue(lock({ locked: true, scopes: ['tags'] }));
    const rowClick = vi.fn();
    render(
      <div onClick={rowClick}>
        <ReviewLockButton entity="album" id={5} name="Views" artistId={2} />
      </div>,
    );
    fireEvent.click(await screen.findByRole('button', { name: 'Lock Views as reviewed' }));
    const sheet = await screen.findByRole('dialog');
    fireEvent.click((await within(sheet).findAllByRole('switch'))[0]);
    fireEvent.click(within(sheet).getAllByRole('switch')[0]);
    // no "Lock and next" off the album page: it opened the next album
    expect(within(sheet).queryByRole('button', { name: 'Lock and next' })).toBeNull();
    fireEvent.click(within(sheet).getByRole('button', { name: 'Lock' }));
    await waitFor(() => expect(toast).toHaveBeenCalledWith('Views is locked', 'success'));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(rowClick).not.toHaveBeenCalled();
  });

  it('an artist has its own switches, a review count, and takes its albums along', async () => {
    api.fetchLock.mockResolvedValue(
      lock({ entity_type: 'artist', entity_id: 2, review: { albums: 3, reviewed: 1 } }),
    );
    api.saveLock.mockResolvedValue(lock({ entity_type: 'artist', entity_id: 2, locked: true }));
    render(<ReviewLockButton entity="artist" id={2} name="Drake" variant="toolbar" />);

    expect(await screen.findByText('1 of 3 reviewed')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Lock Drake as reviewed' }));
    await waitFor(() => expect(screen.getAllByRole('switch')).toHaveLength(5));
    expect(switches().map(([name]) => name)).toEqual([
      'Artwork',
      'Library details',
      'Artist folder',
      'Artist match',
      'And all current albums',
    ]);
    fireEvent.click(screen.getByRole('button', { name: 'Lock' }));
    await waitFor(() =>
      expect(api.saveLock).toHaveBeenCalledWith('artist', 2, {
        scopes: ['artwork', 'metadata', 'files', 'match'],
        albums: true,
      }),
    );
  });

  it('someone who cannot lock sees a padlock only where there is one, read only', async () => {
    shell.admin = false;
    const { unmount } = render(<ReviewLockButton entity="album" id={5} name="Views" />);
    await waitFor(() => expect(api.fetchLockedIds).toHaveBeenCalled());
    expect(screen.queryByRole('button')).toBeNull();
    unmount();

    api.fetchLockedIds.mockResolvedValue({ albums: [5], artists: [] });
    api.fetchLock.mockResolvedValue(lock({ locked: true, scopes: ['tags'], can_lock: false }));
    render(<ReviewLockButton entity="album" id={5} name="Views" />);
    fireEvent.click(await screen.findByRole('button', { name: 'Views is reviewed and locked' }));
    await waitFor(() => expect(screen.getAllByRole('switch')).toHaveLength(6));
    expect(screen.getAllByRole('switch').every((el) => (el as HTMLButtonElement).disabled)).toBe(
      true,
    );
    expect(screen.queryByRole('button', { name: 'Unlock' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Save' })).toBeNull();
  });

  it('a failed lock says so and stays open', async () => {
    api.saveLock.mockRejectedValue(new Error('Locking needs the admin profile'));
    render(<ReviewLockButton entity="album" id={5} name="Views" variant="hero" />);
    fireEvent.click(await screen.findByRole('button', { name: 'Lock Views as reviewed' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Lock' }));
    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith('Locking needs the admin profile', 'error'),
    );
    expect(screen.getByRole('dialog')).toBeTruthy();
  });
});

describe('lockDate', () => {
  it("reads sqlite's utc timestamp", () => {
    expect(lockDate('2026-11-10 08:00:00')).toMatch(/2026/);
    expect(lockDate('')).toBe('');
    expect(lockDate('nonsense')).toBe('');
  });
});

describe('#2119 a locked album is obvious and reads as finished', () => {
  it('draws a closed, filled padlock when locked and an open empty one when not', () => {
    const { container, rerender } = rtlRender(<Padlock locked={false} />);
    const open = container.querySelector('svg')!;
    const openShackle = open.querySelector('path')!.getAttribute('d');
    expect(open.querySelector('rect')!.getAttribute('fill')).toBe('none');
    expect(open.hasAttribute('data-locked')).toBe(false);
    rerender(<Padlock locked />);
    const closed = container.querySelector('svg')!;
    expect(closed.querySelector('rect')!.getAttribute('fill')).toBe('currentColor');
    expect(closed.hasAttribute('data-locked')).toBe(true);
    // a different shape, not only a css turn that can go missing
    expect(closed.querySelector('path')!.getAttribute('d')).not.toBe(openShackle);
  });

  it('shows what is on disk as the whole album once locked', () => {
    expect(lockedPresence(2, 12, true)).toEqual({ present: 2, total: 2, locked: true });
    expect(lockedPresence(2, 12, false)).toEqual({ present: 2, total: 12, locked: false });
    // nothing on disk is not a finished album, lock or not
    expect(lockedPresence(0, 12, true)).toEqual({ present: 0, total: 12, locked: false });
  });

  it('turns a locked release card green with its own count', () => {
    const card = { owned: true, ownedTracks: 9, totalTracks: 10 };
    expect(completionOverlay(card)).toEqual({ cls: 'partial', label: '9/10' });
    expect(completionOverlay(card, true)).toEqual({ cls: 'completed', label: '9/9' });
  });
});
