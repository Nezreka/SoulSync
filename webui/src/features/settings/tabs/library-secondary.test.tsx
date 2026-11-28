/**
 * #2332: the secondary library card. its rules save under library.secondary_*,
 * the folder joins the additional music libraries, a server that returns
 * none of it shows a banner, and moving owned albums previews first.
 */

import { act, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createSettingsStore, type SettingsPayload, type SettingsStore } from '../settings-store';
import { SecondaryLibrary, secondaryValues } from './library-secondary';

type Shell = Window & Record<string, unknown>;
const shell = window as unknown as Shell;

let store: SettingsStore;
let posted: SettingsPayload[];

beforeEach(() => {
  const sink: SettingsPayload[] = [];
  posted = sink;
  store = createSettingsStore({
    post: (body) => {
      sink.push(structuredClone(body));
      return Promise.resolve({ success: true });
    },
    delay: 0,
  });
  shell.SoulSyncFolderCheck = { refresh: vi.fn() };
  shell.showToast = vi.fn();
});

afterEach(async () => {
  await act(() => store.flush());
  vi.unstubAllGlobals();
  delete shell.SoulSyncFolderCheck;
  delete shell.showToast;
  delete shell.showConfirmDialog;
});

function saved(): SettingsPayload {
  const merged: SettingsPayload = {};
  for (const body of posted)
    for (const [k, v] of Object.entries(body)) merged[k] = { ...merged[k], ...v };
  return merged;
}

const input = (id: string) => document.getElementById(id) as HTMLInputElement;

function stubFetch(routes: Record<string, unknown>) {
  const calls: Array<{ url: string; body?: unknown }> = [];
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string, init?: RequestInit) => {
      calls.push({ url, body: init?.body ? JSON.parse(String(init.body)) : undefined });
      const key = Object.keys(routes).find((k) => url.endsWith(k)) ?? '';
      const value =
        typeof routes[key] === 'function'
          ? (routes[key] as (b: unknown) => unknown)(calls.at(-1)?.body)
          : routes[key];
      return Promise.resolve({ ok: true, json: () => Promise.resolve(value ?? {}) } as Response);
    }),
  );
  return calls;
}

describe('the values', () => {
  it('is off by default, holiday music the first rule', () => {
    expect(secondaryValues({})).toEqual({
      enabled: false,
      path: '',
      name: 'Holiday',
      holiday: true,
      genres: '',
      titles: '',
    });
  });
});

describe('the card', () => {
  it('saves the switch and the rules under library', async () => {
    stubFetch({ '/api/secondary-library': { success: true, check: {} } });
    act(() => store.hydrate({ library: {} }));
    render(<SecondaryLibrary store={store} />);
    fireEvent.click(input('secondary-enabled'));
    fireEvent.change(input('secondary-album-titles'), { target: { value: 'christmas|xmas' } });
    await waitFor(() => expect(store.hasPending()).toBe(false));
    expect(saved().library).toMatchObject({
      secondary_enabled: true,
      secondary_album_titles: 'christmas|xmas',
    });
    expect(document.body.textContent).toContain('must keep access to it all year');
  });

  it('adds the folder to the library folders when it is on', async () => {
    stubFetch({ '/api/secondary-library': { success: true, check: {} } });
    const onFolder = vi.fn();
    act(() =>
      store.hydrate({ library: { secondary_enabled: true, secondary_path: '/music-holiday' } }),
    );
    render(<SecondaryLibrary store={store} onFolder={onFolder} />);
    await waitFor(() => expect(onFolder).toHaveBeenCalledWith('/music-holiday'));
  });

  it('shows a banner when the server returns none of the folder', async () => {
    stubFetch({
      '/api/secondary-library': {
        success: true,
        check: { checked: true, hidden: true, server: 'navidrome', folder: '/music-holiday' },
      },
    });
    act(() =>
      store.hydrate({ library: { secondary_enabled: true, secondary_path: '/music-holiday' } }),
    );
    render(<SecondaryLibrary store={store} />);
    await waitFor(() =>
      expect(document.getElementById('secondary-library-hidden')!.textContent).toContain(
        'Navidrome returned none of the music in /music-holiday',
      ),
    );
  });

  it('previews, asks, then moves', async () => {
    const items = [
      {
        album_id: 1,
        title: 'Merry Christmas',
        artist: 'Mariah Carey',
        why: 'holiday',
        action: 'move',
        src: '/a',
        dst: '/b',
      },
      {
        album_id: 2,
        title: 'White Christmas',
        artist: 'Bing',
        why: 'holiday',
        action: 'shared folder',
        src: '/c',
        dst: '',
      },
    ];
    const calls = stubFetch({
      '/api/secondary-library': { success: true, check: {} },
      '/api/secondary-library/move': (body: unknown) =>
        (body as { apply?: boolean }).apply
          ? { success: true, items: [{ ...items[0], action: 'moved' }, items[1]], moved: 1 }
          : { success: true, items, to_move: 1 },
    });
    const confirm = vi.fn(() => Promise.resolve(true));
    shell.showConfirmDialog = confirm;
    act(() =>
      store.hydrate({ library: { secondary_enabled: true, secondary_path: '/music-holiday' } }),
    );
    render(<SecondaryLibrary store={store} />);
    fireEvent.click(input('secondary-preview'));
    const plan = () => document.getElementById('secondary-plan')!.textContent;
    await waitFor(() => expect(plan()).toContain('1 album would move to Holiday.'));
    expect(plan()).toContain('Bing: White Christmas (holiday music): shared folder');
    expect(calls.filter((c) => c.url.endsWith('/move')).map((c) => c.body)).toEqual([
      { apply: false },
    ]);
    fireEvent.click(input('secondary-move'));
    await waitFor(() => expect(plan()).toContain('Moved 1 album.'));
    expect(confirm).toHaveBeenCalled();
    expect(calls.filter((c) => c.url.endsWith('/move')).map((c) => c.body)).toEqual([
      { apply: false },
      { apply: true },
    ]);
  });

  it('says so when no rule is on', () => {
    stubFetch({ '/api/secondary-library': { success: true, check: {} } });
    act(() =>
      store.hydrate({
        library: { secondary_enabled: true, secondary_path: '/x', secondary_match_holiday: false },
      }),
    );
    render(<SecondaryLibrary store={store} />);
    expect(document.body.textContent).toContain('No rule is on');
  });
});
