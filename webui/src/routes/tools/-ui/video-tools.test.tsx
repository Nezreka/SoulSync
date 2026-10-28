/**
 * the video tools page (Oct 27 2026): the music Tools page on ?media=video,
 * and the findings changes that landed on both sides with it.
 *
 * what's pinned:
 * - every request goes to the side the page is showing, from the very first
 *   fetch (the base is set during render, before any child effect)
 * - the video tools tab has the video cards, and non-admins don't get the
 *   admin-only ones
 * - video findings never meet music's per-type questions (quality_upgrade is
 *   a slug both sides use), a file-changing video fix asks once, the ghost's
 *   Forget posts its action
 * - keyboard triage, the albums-view gap note, and the triage buckets
 */

import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { FindingGroup, FindingTypeInfo } from '../-tools.groups';
import type { RepairFinding, RepairJob } from '../-tools.types';

import { isSafeType } from '../-tools.groups';
import { repairUrl, setToolsMedia, ToolsMediaContext } from '../-tools.media';
import { FindingsSurface } from './findings-surface';
import { triageBuckets } from './operations-studio';
import { ToolsPage } from './tools-page';
import { VideoFindingDetail } from './video-finding-detail';
import { videoScanProgressText } from './video-tool-cards';

const fetchMock = vi.fn();
const toastSpy = vi.fn();
const confirmSpy = vi.fn();

function routes(map: Record<string, unknown>, fallback: unknown = {}) {
  fetchMock.mockImplementation((url: string) => {
    const hit = Object.keys(map)
      .filter((key) => String(url).includes(key))
      .sort((a, b) => b.length - a.length)[0];
    return Promise.resolve({
      ok: true,
      status: 200,
      json: async () => (hit ? map[hit] : fallback),
    } as never);
  });
}

async function flush() {
  await act(async () => {});
}

const urls = () => fetchMock.mock.calls.map(([url]) => String(url));

const vfinding = (over: Partial<RepairFinding> = {}): RepairFinding => ({
  id: 7,
  job_id: 'quality_upgrade',
  finding_type: 'quality_upgrade',
  severity: 'info',
  status: 'pending',
  title: 'Heat — 720p, cutoff is 1080p',
  description: 'Best file: 720p',
  entity_type: 'movie',
  entity_id: '12:3',
  created_at: new Date().toISOString(),
  details: { kind: 'movie', movie_id: 12, title: 'Heat', cutoff: '1080p' },
  ...over,
});

const VTYPES: FindingTypeInfo[] = [
  {
    type: 'quality_upgrade',
    label: 'Quality Upgrades',
    verb: 'Grab Upgrade',
    fixable: true,
    destructive: true,
    acquires: true,
    confirm: 'Each one searches for a better release.',
  },
  {
    type: 'naming_mismatch',
    label: 'Naming',
    verb: 'Rename',
    fixable: true,
    destructive: true,
    confirm: 'Each file is renamed to its template name.',
  },
  {
    type: 'youtube_ghost',
    label: 'YouTube Ghosts',
    verb: 'Mark Deleted',
    fixable: true,
    destructive: false,
  },
  {
    type: 'stale_wishlist',
    label: 'Stale Wishlist',
    verb: 'Remove',
    fixable: true,
    destructive: false,
  },
  {
    type: 'duplicate_movie',
    label: 'Duplicate Movies',
    verb: null,
    fixable: false,
    destructive: false,
  },
];

const VJOBS: RepairJob[] = [
  {
    job_id: 'quality_upgrade',
    display_name: 'Quality Upgrades',
    enabled: true,
    is_running: false,
    interval_hours: 24,
  } as RepairJob,
];

beforeEach(() => {
  fetchMock.mockReset();
  toastSpy.mockReset();
  confirmSpy.mockReset().mockResolvedValue(true);
  routes({});
  vi.stubGlobal('fetch', fetchMock);
  Object.assign(window, { showToast: toastSpy, showConfirmDialog: confirmSpy });
  try {
    localStorage.clear();
  } catch {}
});

afterEach(() => {
  cleanup();
  setToolsMedia('music');
  vi.unstubAllGlobals();
});

function renderVideoSurface(items: RepairFinding[]) {
  setToolsMedia('video');
  routes({
    '/api/video/repair/finding-types': { types: VTYPES },
    '/api/video/repair/findings/groups': { groups: [] },
    '/api/video/repair/findings/counts': { pending: items.length, total: items.length },
    '/api/video/repair/findings?': { items, total: items.length, page: 0 },
    '/api/video/repair/findings/7/fix': { success: true, message: 'ok' },
    '/api/video/repair/findings/8/fix': { success: true, message: 'ok' },
    '/api/video/repair/findings/9/fix': { success: true, message: 'ok' },
    '/api/video/repair/findings/7/dismiss': { success: true },
    '/api/video/repair/findings/7/reopen': { success: true },
  });
  return render(
    <ToolsMediaContext.Provider value="video">
      <FindingsSurface jobs={VJOBS} runs={[]} trackCount={100000} onStatusChanged={vi.fn()} />
    </ToolsMediaContext.Provider>,
  );
}

/** search escapes the grouping, so the list renders without a groups payload */
async function showList() {
  await flush();
  fireEvent.change(document.getElementById('repair-findings-search') as HTMLElement, {
    target: { value: 'e' },
  });
  await flush();
  await flush();
}

// ── the api base ─────────────────────────────────────────────────────────────

describe('which side the requests go to', () => {
  it('repairUrl follows the media', () => {
    setToolsMedia('music');
    expect(repairUrl('/jobs')).toBe('/api/repair/jobs');
    setToolsMedia('video');
    expect(repairUrl('/jobs')).toBe('/api/video/repair/jobs');
  });

  it('a video tools page never asks the music repair api, even on its first fetch', async () => {
    routes({ '/api/video/repair/jobs': { jobs: [] } });
    render(<ToolsPage media="video" />);
    await flush();
    await flush();
    const repairCalls = urls().filter((url) => url.includes('/repair/'));
    expect(repairCalls.length).toBeGreaterThan(0);
    expect(repairCalls.filter((url) => url.startsWith('/api/repair'))).toEqual([]);
    expect(repairCalls.every((url) => url.startsWith('/api/video/repair'))).toBe(true);
    // and the library size comes from the video dashboard, not the music db
    expect(urls()).toContain('/api/video/dashboard');
    expect(urls()).not.toContain('/api/database/stats');
  });

  it('the music page still asks the music api', async () => {
    routes({ '/api/repair/jobs': { jobs: [] } });
    render(<ToolsPage />);
    await flush();
    const repairCalls = urls().filter((url) => url.includes('/repair/'));
    expect(repairCalls.length).toBeGreaterThan(0);
    expect(repairCalls.some((url) => url.startsWith('/api/video'))).toBe(false);
  });
});

// ── the tools tab ────────────────────────────────────────────────────────────

describe('the video tools tab', () => {
  it('has the video cards and none of the music ones', () => {
    const { container } = render(<ToolsPage media="video" />);
    const ids = [...container.querySelectorAll('.tool-card')].map((card) => card.id);
    expect(ids).toEqual([
      'video-library-scan-card',
      'video-server-scan-card',
      'video-overlay-studio-card',
      'video-collection-studio-card',
      'video-backups-card',
      'video-rename-card',
    ]);
    expect(container.querySelector('#db-updater-card')).toBeNull();
    expect(container.querySelector('[data-tools-media="video"]')).not.toBeNull();
    // the studios open through the dashboard's one delegated handler
    expect(container.querySelector('[data-video-overlay-studio]')).not.toBeNull();
    expect(container.querySelector('[data-video-collection-studio]')).not.toBeNull();
  });

  it('a non-admin gets the scans only, and no maintenance', () => {
    const { container } = render(<ToolsPage media="video" isAdmin={false} />);
    const ids = [...container.querySelectorAll('.tool-card')].map((card) => card.id);
    expect(ids).toEqual(['video-library-scan-card', 'video-server-scan-card']);
    expect(container.querySelector('.tools-maintenance-hero')).toBeNull();
    expect(screen.getByText('Admins only')).not.toBeNull();
    // nothing admin-only was even asked for
    expect(urls().some((url) => url.includes('/api/video/repair'))).toBe(false);
    expect(urls().some((url) => url.includes('/api/video/backups'))).toBe(false);
  });

  it('starts a library scan through the shared scan controller', async () => {
    routes({ '/api/video/scan/status': { state: 'idle' } });
    const heard = vi.fn();
    document.addEventListener('soulsync:video-scan-start', heard as EventListener);
    const { container } = render(<ToolsPage media="video" isAdmin={false} />);
    await flush();
    const card = container.querySelector('#video-library-scan-card') as HTMLElement;
    const [target, mode] = card.querySelectorAll('select');
    fireEvent.change(target, { target: { value: 'show' } });
    fireEvent.change(mode, { target: { value: 'deep' } });
    fireEvent.click(screen.getByText('Scan Library'));
    expect(heard).toHaveBeenCalledTimes(1);
    expect((heard.mock.calls[0][0] as CustomEvent).detail).toEqual({
      mode: 'deep',
      media_type: 'show',
    });
    // it never posted the scan itself: one controller, one scanning flag
    expect(urls().some((url) => url.includes('/api/video/scan/request'))).toBe(false);
    // and nothing in it carries the old vanilla hooks a vanilla writer would find
    expect(card.querySelector('[data-video-scan-phase],[data-video-scan-run]')).toBeNull();
    document.removeEventListener('soulsync:video-scan-start', heard as EventListener);
  });

  it('says what a scan read, not "0 shows" on a movies-only scan', () => {
    expect(
      videoScanProgressText({ state: 'scanning', media_type: 'movie', movies: 12, percent: 40 }),
    ).toEqual({ phase: 'Scanning', details: '12 movies · 40%', percent: 40 });
    expect(
      videoScanProgressText({ state: 'done', media_type: 'all', movies: 3, shows: 2 }),
    ).toEqual({ phase: 'Complete', details: '3 movies, 2 shows', percent: 100 });
    expect(videoScanProgressText({ state: 'error', error: 'plex down' }).details).toBe('plex down');
  });

  it('stages a restore only after the confirm', async () => {
    routes({
      '/api/video/backups/restore': { success: true },
      '/api/video/backups': {
        success: true,
        pending_restore: false,
        backups: [{ name: 'v1.db', created_at: '2026-10-26T03:00:00', size_bytes: 2_500_000 }],
      },
    });
    confirmSpy.mockResolvedValueOnce(false);
    render(<ToolsPage media="video" />);
    await flush();
    await flush();
    fireEvent.click(screen.getByText('Restore'));
    await flush();
    expect(urls().some((url) => url.includes('/api/video/backups/restore'))).toBe(false);
    fireEvent.click(screen.getByText('Restore'));
    await flush();
    const call = fetchMock.mock.calls.find(([url]) =>
      String(url).includes('/api/video/backups/restore'),
    );
    expect(JSON.parse((call?.[1] as RequestInit).body as string)).toEqual({ name: 'v1.db' });
  });
});

// ── video findings ───────────────────────────────────────────────────────────

describe('video findings', () => {
  it('offers no albums view', async () => {
    renderVideoSurface([]);
    await flush();
    expect(screen.queryByText('Albums & Releases')).toBeNull();
    expect(screen.getByText('Issue Types')).not.toBeNull();
  });

  it('a video quality upgrade never gets the music quality question', async () => {
    renderVideoSurface([vfinding()]);
    await showList();
    fireEvent.click(screen.getByText('Grab Upgrade'));
    await flush();
    // asked once, in the catalog's words, because it replaces a file
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(confirmSpy.mock.calls[0][0].message).toBe('Each one searches for a better release.');
    // no music prompt overlay (redownload / delete / ignore)
    expect(document.querySelector('.repair-prompt-overlay, [id^="repair-prompt-"]')).toBeNull();
    const call = fetchMock.mock.calls.find(([url]) =>
      String(url).includes('/api/video/repair/findings/7/fix'),
    );
    expect(call).toBeDefined();
    expect(JSON.parse((call?.[1] as RequestInit).body as string)).toEqual({});
  });

  it('a declined confirm sends nothing', async () => {
    confirmSpy.mockResolvedValue(false);
    renderVideoSurface([
      vfinding({ id: 8, finding_type: 'naming_mismatch', job_id: 'naming_conformance' }),
    ]);
    await showList();
    fireEvent.click(screen.getByText('Rename'));
    await flush();
    expect(urls().some((url) => url.includes('/fix'))).toBe(false);
  });

  it('a metadata-only video fix just goes', async () => {
    renderVideoSurface([
      vfinding({ id: 9, finding_type: 'stale_wishlist', job_id: 'wishlist_audit' }),
    ]);
    await showList();
    fireEvent.click(screen.getByText('Remove'));
    await flush();
    expect(confirmSpy).not.toHaveBeenCalled();
    expect(urls().some((url) => url.includes('/api/video/repair/findings/9/fix'))).toBe(true);
  });

  it('a review-only type has no fix button', async () => {
    renderVideoSurface([vfinding({ finding_type: 'duplicate_movie', job_id: 'duplicate_movies' })]);
    await showList();
    expect(document.querySelector('.repair-finding-btn.fix')).toBeNull();
    expect(document.querySelector('.repair-finding-btn.dismiss')).not.toBeNull();
  });

  it("the ghost's Forget posts fix_action forget to the same endpoint", async () => {
    renderVideoSurface([
      vfinding({
        finding_type: 'youtube_ghost',
        job_id: 'youtube_ghosts',
        details: { title: 'A video', channel: 'Chan', dest_path: '/yt/a.mp4' },
      }),
    ]);
    await showList();
    fireEvent.click(document.querySelector('.repair-finding-expand-btn') as HTMLElement);
    await flush();
    fireEvent.click(screen.getByText('Forget (allow re-download)'));
    await flush();
    const call = fetchMock.mock.calls.find(([url]) =>
      String(url).includes('/api/video/repair/findings/7/fix'),
    );
    expect(JSON.parse((call?.[1] as RequestInit).body as string)).toEqual({ fix_action: 'forget' });
    // the user just chose, so no extra confirm
    expect(confirmSpy).not.toHaveBeenCalled();
  });
});

// ── the detail panels ────────────────────────────────────────────────────────

describe('video finding detail', () => {
  it('an episode quality upgrade opens (the vanilla threw here)', () => {
    render(
      <VideoFindingDetail
        finding={vfinding({
          entity_type: 'episode',
          details: {
            kind: 'episode',
            show_title: 'Severance',
            season_number: 1,
            episode_number: 4,
            episode_title: 'The You You Are',
            cutoff: '1080p',
            file: { resolution: '720p', relative_path: 'Severance/S01E04.mkv' },
          },
        })}
      />,
    );
    expect(screen.getByText(/Severance S01E04/)).not.toBeNull();
    expect(screen.getByText('now: 720p')).not.toBeNull();
    expect(screen.getByText('cutoff: 1080p')).not.toBeNull();
    // no live fetch for an episode-only finding
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('missing episodes fetch the live show and list only what is missing', async () => {
    routes({
      '/api/video/detail/show/5': {
        id: 5,
        title: 'Severance',
        has_poster: true,
        seasons: [
          {
            season_number: 1,
            title: 'Season 1',
            episodes: [
              { episode_number: 1, title: 'Good News About Hell' },
              { episode_number: 2, title: 'Half Loop' },
            ],
          },
        ],
      },
    });
    render(
      <VideoFindingDetail
        finding={vfinding({
          finding_type: 'missing_episodes',
          details: { show_id: 5, count: 1, episodes: [{ season_number: 1, episode_number: 2 }] },
        })}
      />,
    );
    await waitFor(() => expect(screen.getByText('Half Loop')).not.toBeNull());
    expect(screen.queryByText('Good News About Hell')).toBeNull();
    expect(screen.getByText('1 missing of 1')).not.toBeNull();
  });

  it('a duplicate episode shows every copy (the vanilla printed json)', () => {
    render(
      <VideoFindingDetail
        finding={vfinding({
          finding_type: 'duplicate_episode',
          details: {
            kind: 'episode_files',
            show_title: 'Lost',
            season_number: 2,
            episode_number: 3,
            spans_drives: true,
            files: [
              {
                file_id: 1,
                resolution: '1080p',
                size_bytes: 2 ** 31,
                relative_path: 'D:/Lost.mkv',
              },
              {
                file_id: 2,
                resolution: '1080p',
                size_bytes: 2 ** 31,
                relative_path: 'E:/Lost.mkv',
              },
            ],
          },
        })}
      />,
    );
    expect(document.querySelectorAll('.vrf-file').length).toBe(2);
    expect(screen.getByText('on two drives')).not.toBeNull();
    expect(document.querySelector('.repair-finding-json')).toBeNull();
  });

  it('view buttons open the detail page the video way', () => {
    const heard = vi.fn();
    document.addEventListener('soulsync:video-open-detail', heard as EventListener);
    render(
      <VideoFindingDetail
        finding={vfinding({
          finding_type: 'stale_wishlist',
          details: { title: 'Heat', kind: 'movie', library_id: 12 },
        })}
      />,
    );
    fireEvent.click(screen.getByText('View movie →'));
    expect((heard.mock.calls[0][0] as CustomEvent).detail).toEqual({
      kind: 'movie',
      id: 12,
      source: 'library',
    });
    document.removeEventListener('soulsync:video-open-detail', heard as EventListener);
  });
});

// ── triage + safety ──────────────────────────────────────────────────────────

describe('triage buckets', () => {
  const g = (finding_type: string, pending: number): FindingGroup => ({
    finding_type,
    pending,
    resolved: 0,
    dismissed: 0,
    total: pending,
    severity_max: 'info',
  });
  const info = (type: string) => VTYPES.find((entry) => entry.type === type);

  it('sorts by what the fix does, biggest type first', () => {
    const out = triageBuckets(
      [
        g('stale_wishlist', 4),
        g('youtube_ghost', 9),
        g('quality_upgrade', 2),
        g('naming_mismatch', 5),
        g('duplicate_movie', 3),
      ],
      info,
    );
    expect(out.safe.count).toBe(13);
    expect(out.safe.types.map((t) => t.type)).toEqual(['youtube_ghost', 'stale_wishlist']);
    expect(out.call.types.map((t) => t.type)).toEqual(['naming_mismatch', 'quality_upgrade']);
    expect(out.review.types.map((t) => t.type)).toEqual(['duplicate_movie']);
  });

  it('a type that downloads is never "safe", even when it touches no file', () => {
    expect(isSafeType({ ...VTYPES[0], destructive: false })).toBe(false);
    expect(isSafeType(VTYPES[3])).toBe(true);
    expect(isSafeType(undefined)).toBe(false);
  });
});

// ── keyboard triage + the albums gap (music surface) ─────────────────────────

describe('keyboard triage', () => {
  const mf = (id: number): RepairFinding => ({
    id,
    job_id: 'missing_lyrics',
    finding_type: 'missing_lyrics',
    severity: 'info',
    status: 'pending',
    title: `Track ${id}`,
    created_at: new Date().toISOString(),
    details: {},
  });

  function renderMusicList() {
    setToolsMedia('music');
    routes({
      '/api/repair/finding-types': {
        types: [
          {
            type: 'missing_lyrics',
            label: 'Missing Lyrics',
            verb: 'Apply Lyrics',
            fixable: true,
            destructive: false,
          },
        ],
      },
      '/api/repair/findings?': { items: [mf(1), mf(2), mf(3)], total: 3, page: 0 },
      '/api/repair/findings/2/dismiss': { success: true },
      '/api/repair/findings/2/fix': { success: true },
    });
    const result = render(
      <FindingsSurface jobs={[]} runs={[]} trackCount={1000} onStatusChanged={vi.fn()} />,
    );
    return result;
  }

  async function ready() {
    await flush();
    fireEvent.change(document.getElementById('repair-findings-search') as HTMLElement, {
      target: { value: 'Track' },
    });
    await flush();
    await flush();
    (document.activeElement as HTMLElement | null)?.blur();
  }

  // jsdom has no layout, so offsetParent is null everywhere. the surface's
  // "am I on screen" check reads it; give it a parent for these tests.
  function onScreen() {
    const surface = document.querySelector('.repair-findings-surface') as HTMLElement;
    Object.defineProperty(surface, 'offsetParent', { configurable: true, value: document.body });
  }

  it('j moves the cursor, x selects, d dismisses with an undo', async () => {
    renderMusicList();
    await ready();
    onScreen();
    fireEvent.keyDown(document, { key: 'j' });
    fireEvent.keyDown(document, { key: 'j' });
    await flush();
    const active = document.querySelector('.repair-finding-card.kb-active') as HTMLElement;
    expect(active?.dataset.id).toBe('2');
    fireEvent.keyDown(document, { key: 'x' });
    await flush();
    expect((active.querySelector('.repair-finding-select input') as HTMLInputElement).checked).toBe(
      true,
    );
    fireEvent.keyDown(document, { key: 'd' });
    await flush();
    expect(urls()).toContain('/api/repair/findings/2/dismiss');
    expect(document.querySelector('.repair-undo-bar')?.textContent).toContain('Track 2');
  });

  it('f fixes the row under the cursor', async () => {
    renderMusicList();
    await ready();
    onScreen();
    fireEvent.keyDown(document, { key: 'j' });
    fireEvent.keyDown(document, { key: 'j' });
    fireEvent.keyDown(document, { key: 'f' });
    await flush();
    expect(urls()).toContain('/api/repair/findings/2/fix');
  });

  it('typing in the search box is just typing', async () => {
    renderMusicList();
    await ready();
    onScreen();
    const search = document.getElementById('repair-findings-search') as HTMLElement;
    fireEvent.keyDown(search, { key: 'j' });
    fireEvent.keyDown(search, { key: 'd' });
    await flush();
    expect(document.querySelector('.repair-finding-card.kb-active')).toBeNull();
    expect(urls().some((url) => url.includes('/dismiss'))).toBe(false);
  });

  it('does nothing while the surface is hidden (simple mode)', async () => {
    renderMusicList();
    await ready();
    // no onScreen(): offsetParent is null, like a hidden chassis
    fireEvent.keyDown(document, { key: 'j' });
    fireEvent.keyDown(document, { key: 'd' });
    await flush();
    expect(urls().some((url) => url.includes('/dismiss'))).toBe(false);
  });
});

describe('the albums view says what it cannot show', () => {
  it('counts the findings with no album', async () => {
    setToolsMedia('music');
    localStorage.setItem('soulsync-findings-view-mode', 'albums');
    routes({
      '/api/repair/findings/counts': { pending: 239, resolved: 0, dismissed: 0, total: 239 },
      '/api/repair/findings/albums': {
        groups: [
          { group_by: 'album', key: 'a', album: 'A', artist: 'X', count: 100 },
          { group_by: 'album', key: 'b', album: 'B', artist: 'Y', count: 5 },
        ],
      },
    });
    render(<FindingsSurface jobs={[]} runs={[]} trackCount={1000} onStatusChanged={vi.fn()} />);
    await flush();
    await flush();
    await waitFor(() =>
      expect(document.querySelector('.repair-albums-uncovered')?.textContent).toContain('134'),
    );
    fireEvent.click(screen.getByText('See every finding by issue type'));
    await flush();
    expect(document.querySelector('.repair-albums-uncovered')).toBeNull();
    expect(localStorage.getItem('soulsync-findings-view-mode')).toBe('inbox');
  });

  it('lands on issue types when nothing was chosen', async () => {
    setToolsMedia('music');
    render(<FindingsSurface jobs={[]} runs={[]} trackCount={1000} onStatusChanged={vi.fn()} />);
    await flush();
    const selected = document.querySelector('.repair-view-mode-tab[aria-selected="true"]');
    expect(selected?.textContent).toContain('Issue Types');
  });
});

describe('nothing claims a clean library before it has looked', () => {
  it('simple mode says it is checking until the groups land', async () => {
    setToolsMedia('music');
    fetchMock.mockImplementation(() => new Promise(() => {}));
    const { OperationsStudio } = await import('./operations-studio');
    render(
      <OperationsStudio
        jobs={[]}
        progress={{}}
        runs={[]}
        onChanged={vi.fn()}
        onShowFindings={vi.fn()}
        onSwitchToAdvanced={vi.fn()}
      />,
    );
    await flush();
    expect(screen.getByText('Looking at your findings…')).not.toBeNull();
    expect(screen.queryByText('Nothing needs you')).toBeNull();
    expect(document.querySelector('.operations-exec-num')?.textContent).toBe('–');
  });

  it('the advanced health hero does too', async () => {
    setToolsMedia('music');
    fetchMock.mockImplementation(() => new Promise(() => {}));
    render(<FindingsSurface jobs={[]} runs={[]} trackCount={1000} onStatusChanged={vi.fn()} />);
    await flush();
    expect(document.querySelector('.repair-health-score')?.textContent).toBe('–');
    expect(screen.queryByText('Nothing is dragging your library down.')).toBeNull();
  });
});
