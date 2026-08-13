/**
 * Differential tests for the sidebar component and its log feed —
 * index.html 3301-3315 and api-monitor.js's poller/socket pair.
 */

import { act, fireEvent, render, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { SyncActionsState } from '../-sync.sidebar';

import { SyncSidebar, useSyncLog } from './sync-sidebar';

function state(over: Partial<SyncActionsState> = {}): SyncActionsState {
  return { running: false, selectedCount: 0, currentIndex: 0, queueLength: 0, ...over };
}

function renderSidebar(over: Partial<React.ComponentProps<typeof SyncSidebar>> = {}) {
  const props: React.ComponentProps<typeof SyncSidebar> = {
    state: state(),
    onStartSync: vi.fn(),
    visible: false,
    logText: '',
    ...over,
  };
  return { props, ...render(<SyncSidebar {...props} />) };
}

beforeEach(() => {
  // The log hook runs on every render, `logText` or not — it only overrides
  // what is DISPLAYED. Without a stub each render reaches MSW for /api/logs,
  // and the response lands after the test has finished, so every case in the
  // file would carry an unhandled-request warning and an act() warning that
  // have nothing to do with what it asserts.
  //
  // The default never settles: it models a request still in flight, which
  // produces no state update at all. Tests that care about the feed replace
  // it with one that resolves.
  vi.stubGlobal('fetch', vi.fn().mockReturnValue(new Promise(() => {})));
});

afterEach(() => {
  vi.unstubAllGlobals();
  delete window._socketConnected;
});

describe('the markup (3301-3315)', () => {
  it('renders both sections with their headings', () => {
    const { container } = renderSidebar();
    const headings = Array.from(container.querySelectorAll('.sidebar-section h4'));
    expect(headings.map((h) => h.textContent)).toEqual(['Sync Actions', 'Sync Progress']);
    // The second section carries the extra class its flex-grow rule keys off.
    expect(container.querySelectorAll('.sidebar-section')[1].className).toContain(
      'progress-section',
    );
  });

  it('keeps the ids helper.js anchors its tour copy to', () => {
    // helper.js 798-811 keys help bubbles off these exact selectors, and 3449
    // lists them among the sync page's tour anchors. Renaming any of them
    // would silently empty a bubble rather than fail anything.
    const { container } = renderSidebar();
    for (const selector of ['.sync-sidebar', '#start-sync-btn', '#sync-log-area']) {
      expect(container.querySelector(selector), selector).not.toBeNull();
    }
  });

  it('renders the log area readonly, inside the progress bar container', () => {
    const { container } = renderSidebar();
    const log = container.querySelector('#sync-log-area') as HTMLTextAreaElement;
    expect(log.tagName).toBe('TEXTAREA');
    expect(log.readOnly).toBe(true);
    const bar = container.querySelector('#sync-progress-bar') as HTMLElement;
    expect(bar.parentElement?.className).toBe('progress-bar-container');
    expect(bar.className).toBe('progress-bar-fill');
  });
});

describe('visibility', () => {
  it('carries the visible modifier only when shown', () => {
    const hidden = renderSidebar({ visible: false });
    expect(hidden.container.firstElementChild?.className).toBe('sync-sidebar');
    hidden.unmount();

    const shown = renderSidebar({ visible: true });
    expect(shown.container.firstElementChild?.className).toBe('sync-sidebar sync-sidebar--visible');
  });

  it('stays MOUNTED while hidden, so the log feed keeps running', () => {
    // The vanilla polls for the whole time the page is open, regardless of
    // whether the sidebar is on screen.
    const { container } = renderSidebar({ visible: false });
    expect(container.querySelector('#sync-log-area')).not.toBeNull();
  });
});

describe('the actions section', () => {
  it('shows the selection line and the idle button', () => {
    const { container } = renderSidebar({ state: state({ selectedCount: 3 }) });
    expect(container.querySelector('#selection-info')?.textContent).toBe('3 playlists selected');
    const btn = container.querySelector('#start-sync-btn') as HTMLButtonElement;
    expect(btn.textContent).toBe('Start Sync');
    expect(btn.disabled).toBe(false);
    expect(btn.className).toBe('neo-button');
  });

  it('disables the button with an empty selection', () => {
    const { container } = renderSidebar();
    expect((container.querySelector('#start-sync-btn') as HTMLButtonElement).disabled).toBe(true);
  });

  it('becomes a live Cancel while running', () => {
    const { container } = renderSidebar({
      state: state({ running: true, selectedCount: 0, queueLength: 2, currentName: 'Mix' }),
    });
    const btn = container.querySelector('#start-sync-btn') as HTMLButtonElement;
    expect(btn.textContent).toBe('Cancel Sequential Sync');
    expect(btn.disabled).toBe(false);
    expect(container.querySelector('#selection-info')?.textContent).toBe('Syncing 1/2: Mix');
  });

  it('fires ONE callback for both meanings', () => {
    // The vanilla handler is a single toggle; the caller decides from
    // `running`. Two callbacks here would let a caller wire them
    // inconsistently and reproduce the start-then-cancel bug.
    const onStartSync = vi.fn();
    const { container } = renderSidebar({ state: state({ selectedCount: 1 }), onStartSync });
    fireEvent.click(container.querySelector('#start-sync-btn') as HTMLElement);
    expect(onStartSync).toHaveBeenCalledTimes(1);
  });

  it('cannot fire while disabled', () => {
    const onStartSync = vi.fn();
    const { container } = renderSidebar({ onStartSync });
    fireEvent.click(container.querySelector('#start-sync-btn') as HTMLElement);
    expect(onStartSync).not.toHaveBeenCalled();
  });
});

describe('the progress section', () => {
  it('sits at 0% and the idle text with no run', () => {
    const { container } = renderSidebar();
    expect((container.querySelector('#sync-progress-bar') as HTMLElement).style.width).toBe('0%');
    expect(container.querySelector('#sync-progress-text')?.textContent).toBe('Ready to sync...');
  });

  it('tracks the run', () => {
    const { container } = renderSidebar({
      state: state({ running: true, currentIndex: 1, queueLength: 4 }),
    });
    expect((container.querySelector('#sync-progress-bar') as HTMLElement).style.width).toBe('25%');
    expect(container.querySelector('#sync-progress-text')?.textContent).toBe('2 of 4 playlists');
  });
});

describe('the log area scroll rule', () => {
  /**
   * jsdom does no layout, so scrollHeight/clientHeight are 0 and every position
   * would read as "at the top". They are defined per-element here to model a
   * box with real overflow — 1000 tall in a 100 viewport, so the bottom is
   * scrollTop 900.
   */
  function overflowing(el: HTMLTextAreaElement) {
    Object.defineProperty(el, 'scrollHeight', { value: 1000, configurable: true });
    Object.defineProperty(el, 'clientHeight', { value: 100, configurable: true });
  }

  function mount() {
    const view = renderSidebar({ logText: 'first' });
    const el = view.container.querySelector('#sync-log-area') as HTMLTextAreaElement;
    overflowing(el);
    return { ...view, el };
  }

  it('leaves a reader parked in the middle where they were', () => {
    const { rerender, el, props } = mount();
    el.scrollTop = 400;
    fireEvent.scroll(el);

    rerender(<SyncSidebar {...props} logText="second" />);
    expect(el.scrollTop).toBe(400);
  });

  it('returns to the top for a reader who was at the top', () => {
    const { rerender, el, props } = mount();
    el.scrollTop = 500;
    fireEvent.scroll(el); // parked mid-way...
    el.scrollTop = 5;
    fireEvent.scroll(el); // ...then back near the top
    el.scrollTop = 500; // a stale offset the update should discard

    rerender(<SyncSidebar {...props} logText="second" />);
    expect(el.scrollTop).toBe(0);
  });

  it('measures where the reader LEFT the box, not where it ended up', () => {
    // The whole reason the metrics are captured on scroll rather than read off
    // the element in the effect: after the commit the element can hold a
    // position the vanilla never measures.
    //
    // Both halves of this case are chosen so the two implementations DISAGREE.
    // The reader left the box mid-scroll at 400, which says "leave it alone".
    // The element then ends up at 950 — past the bottom of 900 — which the
    // same rule reads as "return to the top". Reading the element would snap
    // to 0; reading the metrics leaves 950 where it is.
    const { rerender, el, props } = mount();
    el.scrollTop = 400;
    fireEvent.scroll(el);
    el.scrollTop = 950; // moved with no scroll event of its own

    rerender(<SyncSidebar {...props} logText="second" />);
    expect(el.scrollTop).toBe(950);
  });

  it('does not touch the scroll position when the text is unchanged', () => {
    // A re-render driven by something else — a selection change here — must
    // not disturb the reader. Chosen so the two behaviours DISAGREE: the
    // recorded metrics say "at the top", so a rule that ran on this render
    // would snap the box to 0, while leaving it alone keeps 400.
    const { rerender, el, props } = mount();
    el.scrollTop = 5;
    fireEvent.scroll(el);
    el.scrollTop = 400;

    rerender(<SyncSidebar {...props} logText="first" state={state({ selectedCount: 2 })} />);
    expect(el.scrollTop).toBe(400);
  });
});

describe('useSyncLog on its own', () => {
  it('starts on the markup placeholder, before any data', () => {
    // The textarea's own initial content (3313) — so a page with no feed yet
    // reads the same as the vanilla did at load.
    const { result } = renderHook(() => useSyncLog());
    expect(result.current).toBe('Waiting for sync to start...');
  });

  it('is exported for the page to drive the textarea from outside', async () => {
    const { result } = renderHook(() => useSyncLog());
    await act(async () => {
      window.dispatchEvent(new CustomEvent('ss:sync-logs', { detail: { logs: ['x', 'y'] } }));
    });
    expect(result.current).toBe('x\ny');
  });
});

describe('the log feed', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  function stubFetch(logs: string[]) {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ logs }),
    });
    vi.stubGlobal('fetch', fetchMock);
    return fetchMock;
  }

  it('hydrates once on mount, ungated', async () => {
    const fetchMock = stubFetch(['boot']);
    // Even with the socket up, the mount hydrate runs — otherwise the
    // textarea would sit on its placeholder until the first push arrives.
    window._socketConnected = true;
    render(<SyncSidebar state={state()} onStartSync={vi.fn()} visible />);
    expect(fetchMock).toHaveBeenCalledWith('/api/logs');
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await act(async () => {}); // settle it, so the state update stays inside the test
  });

  it('polls every 3s when the socket is down', async () => {
    const fetchMock = stubFetch(['a']);
    window._socketConnected = false;
    render(<SyncSidebar state={state()} onStartSync={vi.fn()} visible />);
    fetchMock.mockClear();

    await act(async () => {
      vi.advanceTimersByTime(3000);
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await act(async () => {
      vi.advanceTimersByTime(3000);
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('does NOT poll while the socket is up — the push owns updates', async () => {
    const fetchMock = stubFetch(['a']);
    window._socketConnected = true;
    render(<SyncSidebar state={state()} onStartSync={vi.fn()} visible />);
    fetchMock.mockClear();

    await act(async () => {
      vi.advanceTimersByTime(9000);
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('stops polling when unmounted', async () => {
    const fetchMock = stubFetch(['a']);
    window._socketConnected = false;
    const { unmount } = render(<SyncSidebar state={state()} onStartSync={vi.fn()} visible />);
    unmount();
    fetchMock.mockClear();

    await act(async () => {
      vi.advanceTimersByTime(9000);
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('renders the socket push', async () => {
    stubFetch([]);
    const { container } = render(<SyncSidebar state={state()} onStartSync={vi.fn()} visible />);
    await act(async () => {}); // let the mount hydrate settle first
    await act(async () => {
      window.dispatchEvent(
        new CustomEvent('ss:sync-logs', { detail: { logs: ['[12:00] one', '[12:01] two'] } }),
      );
    });
    expect((container.querySelector('#sync-log-area') as HTMLTextAreaElement).value).toBe(
      '[12:00] one\n[12:01] two',
    );
  });

  it('KEEPS the last good text when a malformed frame arrives', async () => {
    stubFetch([]);
    const { container } = render(<SyncSidebar state={state()} onStartSync={vi.fn()} visible />);
    const log = () => (container.querySelector('#sync-log-area') as HTMLTextAreaElement).value;
    await act(async () => {});

    await act(async () => {
      window.dispatchEvent(new CustomEvent('ss:sync-logs', { detail: { logs: ['good'] } }));
    });
    expect(log()).toBe('good');

    await act(async () => {
      window.dispatchEvent(new CustomEvent('ss:sync-logs', { detail: { error: 'boom' } }));
    });
    expect(log()).toBe('good');
  });

  it('does NOT let an in-flight fetch overwrite a newer push', async () => {
    // The fetch is in flight for as long as the server takes. A push that
    // lands meanwhile is strictly newer, so the older answer must be dropped
    // when it finally resolves — otherwise the feed visibly rewinds.
    let settle!: (value: unknown) => void;
    const pending = new Promise((resolve) => {
      settle = resolve;
    });
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockReturnValue(
          pending.then(() => ({ ok: true, json: async () => ({ logs: ['stale'] }) })),
        ),
    );

    const { container } = render(<SyncSidebar state={state()} onStartSync={vi.fn()} visible />);
    const log = () => (container.querySelector('#sync-log-area') as HTMLTextAreaElement).value;

    await act(async () => {
      window.dispatchEvent(new CustomEvent('ss:sync-logs', { detail: { logs: ['fresh'] } }));
    });
    expect(log()).toBe('fresh');

    await act(async () => {
      settle(null);
    });
    expect(log()).toBe('fresh');
  });

  it('keeps its text when the fetch fails', async () => {
    window._socketConnected = false;
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ logs: ['ok'] }) });
    vi.stubGlobal('fetch', fetchMock);
    const { container } = render(<SyncSidebar state={state()} onStartSync={vi.fn()} visible />);
    const log = () => (container.querySelector('#sync-log-area') as HTMLTextAreaElement).value;

    // Flush the mount hydrate. `waitFor` cannot be used here — its retry loop
    // runs on timers, which are faked, so it would hang until the test times
    // out rather than polling.
    await act(async () => {});
    expect(log()).toBe('ok');

    fetchMock.mockRejectedValue(new Error('offline'));
    await act(async () => {
      vi.advanceTimersByTime(3000);
    });
    expect(log()).toBe('ok');
  });
});
