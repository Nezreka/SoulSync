/**
 * ActivityFeedCard — artefact differential + the feed behaviours (top-5,
 * separators as siblings, both placeholder variants, relative times) and the
 * toast poller (gates + type chain).
 */

import { act, cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ActivityFeedCard } from './activity-feed';
import { compareTrees, extractDashArticle, parseVanilla } from './dash-artefact';

const fetchMock = vi.fn((..._args: unknown[]) => Promise.reject(new Error('down')));
const showToast = vi.fn();

beforeEach(() => {
  fetchMock.mockClear();
  showToast.mockReset();
  vi.stubGlobal('fetch', fetchMock);
  window.showToast = showToast;
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  delete window.showToast;
  delete window._socketConnected;
  delete window.openLibraryHistoryModal;
});

async function mountCard() {
  let view: ReturnType<typeof render>;
  await act(async () => {
    view = render(<ActivityFeedCard />);
  });
  return view!;
}

function fireActivity(activities: unknown[]) {
  act(() => {
    window.dispatchEvent(new CustomEvent('ss:dashboard-activity', { detail: { activities } }));
  });
}

describe('the artefact differential', () => {
  it('renders the vanilla activity card 1:1 in its initial state', async () => {
    const vanilla = parseVanilla(
      extractDashArticle('<article class="dash-card" data-card="activity">'),
    );
    const view = await mountCard();
    compareTrees(vanilla, view.container.firstElementChild!, 'activity');
  });
});

describe('the feed', () => {
  it('renders top five with separators as SIBLINGS in the container', async () => {
    const view = await mountCard();
    const now = Math.floor(Date.now() / 1000);
    fireActivity(
      Array.from({ length: 7 }, (_, i) => ({
        icon: '🎵',
        title: `T${i}`,
        subtitle: `S${i}`,
        timestamp: now - 90,
      })),
    );
    const container = view.container.querySelector('#dashboard-activity-feed')!;
    const kids = Array.from(container.children).map((el) => el.className);
    expect(kids).toEqual([
      'activity-item',
      'activity-separator',
      'activity-item',
      'activity-separator',
      'activity-item',
      'activity-separator',
      'activity-item',
      'activity-separator',
      'activity-item',
    ]);
    expect(container.querySelector('.activity-title')!.textContent).toBe('T0');
    expect(container.querySelector('.activity-time')!.textContent).toBe('1m ago');
  });

  it('an EMPTY feed shows the placeholder with "Just now" (the markup initial says "Now")', async () => {
    const view = await mountCard();
    expect(view.container.querySelector('.activity-time')!.textContent).toBe('Now');
    fireActivity([]);
    expect(view.container.querySelector('.activity-title')!.textContent).toBe('System Started');
    expect(view.container.querySelector('.activity-time')!.textContent).toBe('Just now');
  });

  it('hydrates from /api/activity/feed on mount and polls at 2s when the socket is down', async () => {
    vi.useFakeTimers();
    fetchMock.mockImplementation((url: unknown) =>
      String(url).includes('/api/activity/feed')
        ? (Promise.resolve({
            ok: true,
            status: 200,
            json: async () => ({ activities: [{ icon: 'i', title: 'Fed', subtitle: 's' }] }),
          }) as never)
        : Promise.reject(new Error('down')),
    );
    const view = await mountCard();
    expect(view.container.querySelector('.activity-title')!.textContent).toBe('Fed');
    fetchMock.mockClear();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(
      fetchMock.mock.calls.some((call) => String(call[0]).includes('/api/activity/feed')),
    ).toBe(true);
  });

  it('the Download History head button opens the vanilla modal', async () => {
    const openLibraryHistoryModal = vi.fn();
    window.openLibraryHistoryModal = openLibraryHistoryModal;
    const view = await mountCard();
    fireEvent.click(view.container.querySelector('.dash-card__head-btn')!);
    expect(openLibraryHistoryModal).toHaveBeenCalledTimes(1);
  });
});

describe('the toast poller', () => {
  it('polls /api/activity/toasts at 3s and toasts with the vanilla type chain', async () => {
    vi.useFakeTimers();
    fetchMock.mockImplementation((url: unknown) =>
      String(url).includes('/api/activity/toasts')
        ? (Promise.resolve({
            ok: true,
            status: 200,
            json: async () => ({
              toasts: [
                { icon: '✅', title: 'Download Complete', subtitle: 'BYLT - Album' },
                { icon: '📊', title: 'Sync Failed', subtitle: 'x' },
              ],
            }),
          }) as never)
        : (Promise.resolve({ ok: true, status: 200, json: async () => ({}) }) as never),
    );
    await mountCard();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(showToast).toHaveBeenCalledWith('Download Complete: BYLT - Album', 'success');
    expect(showToast).toHaveBeenCalledWith('Sync Failed: x', 'error');
  });

  it('never toasts while the socket is pushing — core.js already does', async () => {
    vi.useFakeTimers();
    window._socketConnected = true;
    await mountCard();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(9000);
    });
    expect(showToast).not.toHaveBeenCalled();
    // And the ss:dashboard-toast event must NOT be a React toast source either
    // (handleDashboardToast toasts before dispatching — two would double up).
    act(() => {
      window.dispatchEvent(
        new CustomEvent('ss:dashboard-toast', {
          detail: { icon: '✅', title: 'Complete', subtitle: 'x' },
        }),
      );
    });
    expect(showToast).not.toHaveBeenCalled();
  });
});
