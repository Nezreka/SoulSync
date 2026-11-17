/**
 * The Logs tab, ported from the vanilla Settings page (SETTINGS_PORT.md,
 * step 3): the same markup as the frozen fixture, and the live log viewer
 * behaving as settings.js's _logViewer* functions did.
 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { SettingsTabProps } from '../host';

import { createSettingsStore } from '../settings-store';
import { shape, vanilla } from './__fixtures__/shape';
import {
  LOG_MAX_LINES,
  classifyLogLine,
  keepLiveLines,
  logLevelClass,
  type LogLiveFrame,
} from './logs';
import { LogsTab } from './logs-tab';

const store = createSettingsStore({ post: () => Promise.resolve({ success: true }) });
const props = (active: boolean, opened = 1): SettingsTabProps => ({ store, active, opened });

let tails: string[];
let tailLines: string[];
let subscribed: Array<[string, (frame: LogLiveFrame) => void]>;
let unsubscribed: number;
let connected: boolean;

beforeEach(() => {
  tails = [];
  tailLines = ['2026-11-15 - INFO - started', '2026-11-15 - ERROR - boom'];
  subscribed = [];
  unsubscribed = 0;
  connected = true;
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string) => {
      tails.push(url);
      return Promise.resolve({ json: () => Promise.resolve({ lines: tailLines }) });
    }),
  );
  window.soulsyncLogStream = {
    subscribe: (source, onLive) => {
      if (!connected) return false;
      subscribed.push([source, onLive]);
      return true;
    },
    unsubscribe: () => {
      unsubscribed += 1;
    },
  };
  window.showToast = vi.fn();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
  delete window.soulsyncLogStream;
});

const lines = () =>
  [...document.querySelectorAll('#log-viewer-lines > div')].map((el) => el.textContent);
const count = () => document.getElementById('log-viewer-line-count')!.textContent;
const live = (frame: LogLiveFrame) => act(() => subscribed.at(-1)![1](frame));

describe('the Logs tab is the same page it replaced', () => {
  it('renders the vanilla viewer: same elements, ids, classes and words', () => {
    const { container } = render(<LogsTab {...props(false, 0)} />);
    const want = shape(vanilla('logs.vanilla.html'));
    const got = shape(container);
    expect(got.elements).toEqual(want.elements);
    expect(got.text).toBe(want.text);
  });
});

describe('the lines', () => {
  it('reads levels the way the viewer did', () => {
    expect(classifyLogLine('x - DEBUG - y')).toBe('DEBUG');
    expect(classifyLogLine('x - CRITICAL - y')).toBe('ERROR');
    expect(classifyLogLine('Traceback (most recent call last)')).toBe('ERROR');
    expect(classifyLogLine('download failed')).toBe('ERROR');
    expect(classifyLogLine('warn: slow')).toBe('WARNING');
    expect(classifyLogLine('debug output')).toBe('DEBUG');
    expect(classifyLogLine('plain line')).toBe('INFO');
    expect(logLevelClass('WARNING')).toBe('log-warning');
    expect(logLevelClass('nope')).toBe('log-plain');
    expect(keepLiveLines(['a - ERROR - x', 'a - INFO - Xyz'], 'INFO', 'xyz')).toEqual([
      'a - INFO - Xyz',
    ]);
  });
});

describe('the live viewer', () => {
  it('waits until its tab is opened, then shows the tail and goes live', async () => {
    const view = render(<LogsTab {...props(false, 0)} />);
    expect(lines()).toEqual(['Initializing log viewer...']);
    expect(count()).toBe('0 lines');
    expect(tails).toEqual([]);
    view.rerender(<LogsTab {...props(true)} />);
    await waitFor(() => expect(lines()).toEqual(tailLines));
    expect(tails).toEqual(['/api/logs/tail?source=app&lines=300']);
    expect(subscribed.map(([source]) => source)).toEqual(['app']);
    expect(count()).toBe('2 lines');
    expect(document.querySelector('#log-viewer-lines > div:last-child')!.className).toBe(
      'log-line log-error',
    );
  });

  it('adds live lines for its own source, and stops when another tab opens', async () => {
    const view = render(<LogsTab {...props(true)} />);
    await waitFor(() => expect(subscribed).toHaveLength(1));
    live({ source: 'acoustid', lines: ['other log'] });
    live({ source: 'app', lines: ['x - WARNING - slow'] });
    expect(lines()).toEqual([...tailLines, 'x - WARNING - slow']);
    view.rerender(<LogsTab {...props(false)} />);
    expect(unsubscribed).toBe(1);
    // a frame still in flight after leaving is dropped
    live({ source: 'app', lines: ['late'] });
    expect(lines()).not.toContain('late');
    // opening again does not stack a second subscription
    view.rerender(<LogsTab {...props(true, 2)} />);
    await waitFor(() => expect(subscribed).toHaveLength(2));
    view.rerender(<LogsTab {...props(true, 3)} />);
    await act(async () => {});
    expect(subscribed).toHaveLength(2);
  });

  it('a level reloads the tail for that level and filters live lines too', async () => {
    render(<LogsTab {...props(true)} />);
    await waitFor(() => expect(subscribed).toHaveLength(1));
    tailLines = ['x - ERROR - only errors'];
    fireEvent.click(screen.getByText('Error'));
    expect(screen.getByText('Error').classList.contains('active')).toBe(true);
    expect(screen.getByText('All').classList.contains('active')).toBe(false);
    expect(unsubscribed).toBe(1);
    await waitFor(() => expect(lines()).toEqual(['x - ERROR - only errors']));
    expect(tails.at(-1)).toBe('/api/logs/tail?source=app&lines=300&level=ERROR');
    live({ source: 'app', lines: ['x - INFO - hidden', 'x - ERROR - shown'] });
    expect(lines()).toEqual(['x - ERROR - only errors', 'x - ERROR - shown']);
  });

  it('search waits for typing to stop, then reloads with it', async () => {
    render(<LogsTab {...props(true)} />);
    await waitFor(() => expect(tails).toHaveLength(1));
    // the reload's tail stays in flight, so its Loading line can be seen
    vi.stubGlobal(
      'fetch',
      vi.fn((url: string) => {
        tails.push(url);
        return new Promise(() => {});
      }),
    );
    vi.useFakeTimers();
    fireEvent.change(document.getElementById('log-viewer-search')!, { target: { value: ' sp' } });
    fireEvent.change(document.getElementById('log-viewer-search')!, {
      target: { value: ' spot ' },
    });
    await act(async () => vi.advanceTimersByTime(299));
    expect(tails).toHaveLength(1);
    await act(async () => vi.advanceTimersByTime(1));
    expect(lines()).toEqual(['Loading...']);
    expect(tails).toHaveLength(2);
    expect(tails[1]).toBe('/api/logs/tail?source=app&lines=300&search=spot');
  });

  it('another source shows Loading, then its own tail and live lines', async () => {
    render(<LogsTab {...props(true)} />);
    await waitFor(() => expect(subscribed).toHaveLength(1));
    fireEvent.change(document.getElementById('log-viewer-source')!, {
      target: { value: 'acoustid' },
    });
    await waitFor(() => expect(tails.at(-1)).toBe('/api/logs/tail?source=acoustid&lines=300'));
    await waitFor(() => expect(subscribed.at(-1)![0]).toBe('acoustid'));
    live({ source: 'app', lines: ['from app'] });
    expect(lines()).not.toContain('from app');
  });

  it('keeps the last 2000 lines', async () => {
    tailLines = Array.from({ length: LOG_MAX_LINES }, (_, i) => `line ${i}`);
    render(<LogsTab {...props(true)} />);
    await waitFor(() => expect(count()).toBe('2000 lines'));
    live({ source: 'app', lines: ['newest'] });
    expect(count()).toBe('2000 lines');
    expect(lines()[0]).toBe('line 1');
    expect(lines().at(-1)).toBe('newest');
  });

  it('shows the tail without live lines when the socket is down', async () => {
    connected = false;
    render(<LogsTab {...props(true)} />);
    await waitFor(() => expect(lines()).toEqual(tailLines));
    expect(subscribed).toEqual([]);
  });

  it('a tail that fails leaves the message up and still goes live', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.reject(new Error('offline'))),
    );
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    render(<LogsTab {...props(true)} />);
    await waitFor(() => expect(subscribed).toHaveLength(1));
    expect(lines()).toEqual(['Initializing log viewer...']);
    warn.mockRestore();
  });

  it('Clear empties the view; Copy copies what is shown', async () => {
    render(<LogsTab {...props(true)} />);
    await waitFor(() => expect(lines()).toEqual(tailLines));
    const writeText = vi.fn(() => Promise.resolve());
    vi.stubGlobal('navigator', { clipboard: { writeText } });
    vi.stubGlobal('isSecureContext', true);
    fireEvent.click(screen.getByText('Copy'));
    expect(writeText).toHaveBeenCalledWith(tailLines.join('\n'));
    await waitFor(() => expect(window.showToast).toHaveBeenCalledWith('Logs copied', 'success'));
    fireEvent.click(screen.getByText('Clear'));
    expect(lines()).toEqual([]);
    expect(count()).toBe('0 lines');
  });

  it('scrolls to the newest line only while Auto-scroll is ticked', async () => {
    render(<LogsTab {...props(true)} />);
    await waitFor(() => expect(subscribed).toHaveLength(1));
    const terminal = document.getElementById('log-viewer-terminal')!;
    Object.defineProperty(terminal, 'scrollHeight', { value: 900, configurable: true });
    terminal.scrollTop = 0;
    live({ source: 'app', lines: ['a'] });
    expect(terminal.scrollTop).toBe(900);
    terminal.scrollTop = 0;
    fireEvent.click(document.getElementById('log-viewer-autoscroll')!);
    live({ source: 'app', lines: ['b'] });
    expect(terminal.scrollTop).toBe(0);
  });

  it('leaving the page altogether stops the stream', async () => {
    const view = render(<LogsTab {...props(true)} />);
    await waitFor(() => expect(subscribed).toHaveLength(1));
    view.unmount();
    expect(unsubscribed).toBe(1);
  });
});

describe('Download', () => {
  afterEach(() => {
    delete window.SoulSyncHelp;
  });

  it("opens the help sheet's Logs view: every log, its backups, secrets removed", () => {
    const open = vi.fn();
    window.SoulSyncHelp = {
      open,
      close: vi.fn(),
      toggle: vi.fn(),
      isOpen: () => false,
      openSearch: vi.fn(),
    };
    render(<LogsTab {...props(false, 0)} />);
    fireEvent.click(screen.getByText('Download'));
    expect(open).toHaveBeenCalledWith('logs');
  });

  it('without the sheet, downloads every log as one zip, still redacted', () => {
    const hrefs: string[] = [];
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(function (this: HTMLAnchorElement) {
        hrefs.push(this.getAttribute('href') ?? '');
      });
    render(<LogsTab {...props(false, 0)} />);
    fireEvent.click(screen.getByText('Download'));
    expect(hrefs).toHaveLength(1);
    expect(hrefs[0]).toMatch(/\/api\/help\/logs\/download-all$/);
    expect(hrefs[0]).not.toContain('raw');
    click.mockRestore();
  });
});
