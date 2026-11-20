/**
 * Notifications and Import Lists (SETTINGS_PORT.md, video pass V3c), both on
 * the parts: Import Lists since settings-bic phase 5 (column headers, named
 * fields, a delete that asks first), Notifications since phase 6 (a block
 * per connection, events as chips, a delete that asks first), the load each time the
 * video settings page is shown,
 * a row saved alone when a field changes (read off the page), add, delete,
 * Test, and a new notification type drawing its row again.
 */

import { act, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ImportListsCard, importLists, NotificationsCard, notifications } from './video-row-lists';

type Shell = Window & Record<string, unknown>;
const shell = window as unknown as Shell;
let calls: Array<{ url: string; method: string; body?: Record<string, unknown> }>;
let answers: Record<string, unknown>;

const LISTS = [
  {
    id: 1,
    name: 'Trending',
    source: 'tmdb_chart',
    ref: 'trending_movies',
    media: 'movie',
    monitor: 'future',
    enabled: true,
  },
  {
    id: 2,
    name: 'Mine',
    source: 'imdb_list',
    ref: 'ls123',
    media: 'both',
    monitor: 'all',
    enabled: false,
  },
];
const CONNS = [
  {
    id: 4,
    name: 'Discord',
    type: 'discord',
    url: 'https://d/hook',
    token: '',
    chat_id: '',
    enabled: true,
    events: ['video_download_completed'],
  },
  {
    id: 5,
    name: 'TG',
    type: 'telegram',
    url: '',
    token: 'tok',
    chat_id: '42',
    enabled: false,
    events: [],
  },
];
const EVENTS = ['video_download_completed', 'video_download_failed', 'video_watchlist_added'];

beforeEach(() => {
  importLists.reset();
  notifications.reset();
  calls = [];
  answers = {
    'GET /api/video/downloads/config/import-lists': { lists: LISTS },
    'GET /api/video/notifications': { connections: CONNS, events: EVENTS },
  };
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string, init?: RequestInit) => {
      const method = init?.method || 'GET';
      const body =
        typeof init?.body === 'string'
          ? (JSON.parse(init.body) as Record<string, unknown>)
          : undefined;
      calls.push({ url, method, body });
      const key = `${method} ${url}`;
      if (answers[key] === 403)
        return Promise.resolve({
          ok: false,
          status: 403,
          json: () => Promise.resolve(null),
        } as Response);
      const data = key in answers ? answers[key] : { success: true, ...body };
      return Promise.resolve({ ok: true, json: () => Promise.resolve(data) } as Response);
    }),
  );
  shell.showToast = vi.fn();
});

afterEach(() => {
  vi.unstubAllGlobals();
  delete shell.showToast;
});

const posts = (url: string) =>
  calls.filter((c) => c.method === 'POST' && c.url === url).map((c) => c.body!);
function shown() {
  act(() => {
    document.dispatchEvent(
      new CustomEvent('soulsync:video-page-shown', { detail: 'video-settings' }),
    );
  });
}
const q = <T extends Element = HTMLInputElement>(sel: string) => document.querySelector<T>(sel)!;
/** a field whose wrapper is hidden: not this connection's type */
const gone = (sel: string) => !!q(sel).closest('[hidden]');
function leave(el: HTMLElement) {
  el.dispatchEvent(new Event('change', { bubbles: true }));
}

describe('Import Lists', () => {
  it('loads each time the video settings page is shown', async () => {
    render(<ImportListsCard />);
    expect(q('#vq-implist-rows').children).toHaveLength(0);
    shown();
    await waitFor(() => expect(document.querySelectorAll('[data-vq-implist]')).toHaveLength(2));
    const row = q('[data-vq-implist="2"]');
    expect(q<HTMLSelectElement>('[data-vq-implist="2"] [data-vq-il-f="source"]').value).toBe(
      'imdb_list',
    );
    expect(row.querySelector<HTMLInputElement>('[data-vq-il-f="enabled"]')!.checked).toBe(false);
    shown();
    await waitFor(() =>
      expect(calls.filter((c) => c.url.endsWith('/import-lists'))).toHaveLength(2),
    );
  });

  it('the rows have column headers, a named field each, and say what a List takes', async () => {
    render(<ImportListsCard />);
    shown();
    await waitFor(() => expect(document.querySelectorAll('[data-vq-implist]')).toHaveLength(2));
    const head = q('#vq-implist-rows').firstElementChild!;
    expect([...head.children].map((c) => c.textContent)).toEqual([
      'Name',
      'From',
      'List',
      'Adds',
      'A followed show gets',
      'On',
      '',
    ]);
    const row = q('[data-vq-implist="2"]');
    for (const el of row.querySelectorAll('input, select, button'))
      expect(el.getAttribute('aria-label'), el.outerHTML).toBeTruthy();
    expect(q('[data-vq-implist="2"] [data-vq-il-f="enabled"]').getAttribute('aria-label')).toBe(
      'Use this list',
    );
    expect(q('[data-vq-implist-del="2"]').getAttribute('aria-label')).toBe('Delete Mine');
    // the List field says what goes in it, by where the list comes from
    expect(q('[data-vq-implist="1"] [data-vq-il-f="ref"]').placeholder).toBe(
      'chart, like trending_movies',
    );
    expect(q('[data-vq-implist="2"] [data-vq-il-f="ref"]').placeholder).toBe('IMDb ls id or link');
    // a movie list does not use what a followed show gets: dimmed, not disabled
    const monitor = q<HTMLSelectElement>('[data-vq-implist="1"] [data-vq-il-f="monitor"]');
    expect(monitor.disabled).toBe(false);
    expect(monitor.closest('[title="Only used for shows"]')).not.toBeNull();
    expect(document.body.textContent).not.toContain('—');
  });

  it('a field saves its row alone, read off the page; a bad one says why', async () => {
    render(<ImportListsCard />);
    shown();
    await waitFor(() => expect(document.querySelectorAll('[data-vq-implist]')).toHaveLength(2));
    // a pick: saved with the new value (heard before React's own change)
    fireEvent.change(q('[data-vq-implist="1"] [data-vq-il-f="media"]'), {
      target: { value: 'show' },
    });
    await waitFor(() => expect(posts('/api/video/downloads/config/import-lists')).toHaveLength(1));
    expect(posts('/api/video/downloads/config/import-lists')[0]).toEqual({
      id: 1,
      name: 'Trending',
      source: 'tmdb_chart',
      ref: 'trending_movies',
      media: 'show',
      monitor: 'future',
      enabled: true,
    });
    // a text field saves when it is left, not while typed
    const ref = q('[data-vq-implist="2"] [data-vq-il-f="ref"]');
    fireEvent.input(ref, { target: { value: 'nope' } });
    expect(posts('/api/video/downloads/config/import-lists')).toHaveLength(1);
    answers['POST /api/video/downloads/config/import-lists'] = { success: false };
    leave(ref);
    await waitFor(() =>
      expect(shell.showToast).toHaveBeenCalledWith('A list needs a valid source and list', 'error'),
    );
    expect(posts('/api/video/downloads/config/import-lists')[1].ref).toBe('nope');
    // what was typed stays
    expect(ref.value).toBe('nope');
  });

  it('add and delete draw the rows again', async () => {
    render(<ImportListsCard />);
    shown();
    await waitFor(() => expect(document.querySelectorAll('[data-vq-implist]')).toHaveLength(2));
    answers['POST /api/video/downloads/config/import-lists'] = {
      success: true,
      id: 9,
      name: 'New list',
      source: 'tmdb_chart',
      ref: 'trending_movies',
      media: 'both',
      monitor: 'future',
      enabled: true,
    };
    fireEvent.click(q('[data-vq-implist-add]'));
    await waitFor(() => expect(document.querySelectorAll('[data-vq-implist]')).toHaveLength(3));
    expect(posts('/api/video/downloads/config/import-lists')[0]).toEqual({
      name: 'New list',
      source: 'tmdb_chart',
      ref: 'trending_movies',
    });
    // a delete asks first: kept on Keep it, gone on Delete
    let answer = false;
    const asked: Array<Record<string, unknown>> = [];
    shell.showConfirmDialog = vi.fn((o?: Record<string, unknown>) => {
      asked.push(o ?? {});
      return Promise.resolve(answer);
    });
    fireEvent.click(q('[data-vq-implist-del="1"]'));
    await waitFor(() => expect(asked).toHaveLength(1));
    expect(asked[0]).toMatchObject({ title: 'Delete Trending?', destructive: true });
    await act(async () => {});
    expect(calls.some((c) => c.method === 'DELETE')).toBe(false);
    expect(document.querySelectorAll('[data-vq-implist]')).toHaveLength(3);
    answer = true;
    fireEvent.click(q('[data-vq-implist-del="1"]'));
    await waitFor(() => expect(document.querySelectorAll('[data-vq-implist]')).toHaveLength(2));
    expect(calls.some((c) => c.method === 'DELETE' && c.url.endsWith('/import-lists/1'))).toBe(
      true,
    );
    fireEvent.click(q('[data-vq-implist-del="2"]'));
    fireEvent.click(q('[data-vq-implist-del="9"]'));
    await waitFor(() => expect(q('#vq-implist-rows').textContent).toBe('No import lists yet.'));
    delete shell.showConfirmDialog;
  });
});

describe('Notifications', () => {
  it('loads the connections and their events; a non-admin sees none', async () => {
    render(<NotificationsCard />);
    shown();
    await waitFor(() => expect(document.querySelectorAll('[data-vq-notify]')).toHaveLength(2));
    // the target fields follow the type
    expect(gone('[data-vq-notify="4"] [data-vq-nt-f="url"]')).toBe(false);
    expect(gone('[data-vq-notify="4"] [data-vq-nt-f="token"]')).toBe(true);
    expect(gone('[data-vq-notify="5"] [data-vq-nt-f="url"]')).toBe(true);
    expect(gone('[data-vq-notify="5"] [data-vq-nt-f="chat_id"]')).toBe(false);
    // the events are chips, each a real tick box with its hook
    expect(
      [...document.querySelectorAll('[data-vq-notify="4"] [role="group"] label')].map(
        (l) => l.textContent,
      ),
    ).toEqual(['Imported', 'Failed', 'Followed']);
    expect(q('[data-vq-notify="4"] [role="group"]').getAttribute('aria-label')).toBe(
      'What Discord hears',
    );
    // each field and button named for the connection it is in
    expect(q('[data-vq-notify="4"] [data-vq-nt-f="enabled"]').getAttribute('aria-label')).toBe(
      'Use Discord',
    );
    expect(q('[data-vq-notify-test="5"]').getAttribute('aria-label')).toBe('Test TG');
    expect(q('[data-vq-notify-del="5"]').getAttribute('aria-label')).toBe('Delete TG');
    expect(q('[data-vq-notify="4"] [data-vq-nt-f="enabled"]').getAttribute('role')).toBe('switch');
    expect(q('[data-vq-notify="4"] [data-vq-nt-ev="video_download_completed"]').checked).toBe(true);
  });

  it('a non-admin (403) sees nothing, not an empty list', async () => {
    answers['GET /api/video/notifications'] = 403;
    render(<NotificationsCard />);
    shown();
    await waitFor(() => expect(calls.some((c) => c.url === '/api/video/notifications')).toBe(true));
    expect(q('#vq-notify-rows').textContent).toBe('');
  });

  it('an event ticked saves the block with its events in order', async () => {
    render(<NotificationsCard />);
    shown();
    await waitFor(() => expect(document.querySelectorAll('[data-vq-notify]')).toHaveLength(2));
    fireEvent.click(q('[data-vq-notify="4"] [data-vq-nt-ev="video_watchlist_added"]'));
    await waitFor(() => expect(posts('/api/video/notifications')).toHaveLength(1));
    expect(posts('/api/video/notifications')[0]).toEqual({
      id: 4,
      name: 'Discord',
      type: 'discord',
      url: 'https://d/hook',
      token: '',
      chat_id: '',
      enabled: true,
      events: ['video_download_completed', 'video_watchlist_added'],
    });
  });

  it('a new type is drawn again, so its fields swap', async () => {
    render(<NotificationsCard />);
    shown();
    await waitFor(() => expect(document.querySelectorAll('[data-vq-notify]')).toHaveLength(2));
    answers['POST /api/video/notifications'] = { ...CONNS[0], type: 'telegram', success: true };
    fireEvent.change(q('[data-vq-notify="4"] [data-vq-nt-f="type"]'), {
      target: { value: 'telegram' },
    });
    await waitFor(() => expect(gone('[data-vq-notify="4"] [data-vq-nt-f="token"]')).toBe(false));
    expect(gone('[data-vq-notify="4"] [data-vq-nt-f="url"]')).toBe(true);
    expect(posts('/api/video/notifications')[0].type).toBe('telegram');
  });

  it('Test sends the block as it is, off while it runs', async () => {
    answers['POST /api/video/notifications/test'] = { success: false, error: 'bad hook' };
    render(<NotificationsCard />);
    shown();
    await waitFor(() => expect(document.querySelectorAll('[data-vq-notify]')).toHaveLength(2));
    const test = q<HTMLButtonElement>('[data-vq-notify-test="5"]');
    fireEvent.click(test);
    expect(test.disabled).toBe(true);
    await waitFor(() => expect(shell.showToast).toHaveBeenCalledWith('bad hook', 'error'));
    expect(test.disabled).toBe(false);
    expect(posts('/api/video/notifications/test')[0]).toMatchObject({
      id: 5,
      type: 'telegram',
      token: 'tok',
      chat_id: '42',
    });
  });

  it('add puts a Discord connection in; delete takes it out', async () => {
    render(<NotificationsCard />);
    shown();
    await waitFor(() => expect(document.querySelectorAll('[data-vq-notify]')).toHaveLength(2));
    answers['POST /api/video/notifications'] = {
      success: true,
      id: 7,
      name: 'Discord',
      type: 'discord',
      url: 'https://discord.com/api/webhooks/REPLACE-ME',
      enabled: true,
      events: [],
    };
    fireEvent.click(q('[data-vq-notify-add]'));
    await waitFor(() => expect(document.querySelectorAll('[data-vq-notify]')).toHaveLength(3));
    expect(posts('/api/video/notifications')[0]).toEqual({
      name: 'Discord',
      type: 'discord',
      url: 'https://discord.com/api/webhooks/REPLACE-ME',
    });
    // a delete asks first: Keep it keeps it and sends nothing
    shell.showConfirmDialog = vi.fn().mockResolvedValueOnce(false).mockResolvedValue(true);
    fireEvent.click(q('[data-vq-notify-del="7"]'));
    await act(() => Promise.resolve());
    expect(calls.some((c) => c.method === 'DELETE')).toBe(false);
    expect(document.querySelectorAll('[data-vq-notify]')).toHaveLength(3);
    fireEvent.click(q('[data-vq-notify-del="7"]'));
    await waitFor(() => expect(document.querySelectorAll('[data-vq-notify]')).toHaveLength(2));
    expect(shell.showConfirmDialog).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'Delete Discord?', destructive: true }),
    );
    delete shell.showConfirmDialog;
  });
});
