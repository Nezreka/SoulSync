/**
 * The video side's media server (SETTINGS_PORT.md, video pass V3b; on the
 * settings parts in settings-bic phase 3): the card and the sheet's fields,
 * the load each time the video settings page is shown,
 * the toggle, the credentials and their notes, Test, the Jellyfin user, and
 * the library picks (the other TV libraries' Scan and Anime ticks).
 */

import { act, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  VideoServerConnection as Card,
  VideoServerFields,
  useVideoServerState,
} from './connections-video-server';
import { connNote, videoServer } from './video-server';

type Shell = Window & Record<string, unknown>;
const shell = window as unknown as Shell;
let calls: Array<{ url: string; method: string; body?: Record<string, unknown> }>;
let answers: Record<string, unknown>;

beforeEach(() => {
  videoServer.reset();
  calls = [];
  answers = {
    'GET /api/video/server': { server: 'jellyfin' },
    'GET /api/video/server-config': {
      plex: { base_url: 'http://plex:32400', has_token: true, token: 'tok', inherited: true },
      jellyfin: { base_url: 'http://jf:8096', has_key: false },
    },
    'GET /api/video/jellyfin/users': {
      success: true,
      users: [
        { id: 'u1', name: 'Boss', admin: true },
        { id: 'u2', name: 'Kid' },
      ],
      selected: 'u2',
    },
    'GET /api/video/libraries': {
      movies: [{ title: 'Movies' }, { title: 'Movies 4K' }],
      tv: [{ title: 'TV' }, { title: 'Anime' }, { title: 'Kids TV' }],
      selected: { movies: 'Movies', tv: 'TV', tv_more: ['Kids TV'], anime: ['TV'] },
    },
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
      const data = answers[`${method} ${url}`] ?? { success: true };
      return Promise.resolve({ ok: true, json: () => Promise.resolve(data) } as Response);
    }),
  );
  shell.showToast = vi.fn();
});

afterEach(() => {
  vi.unstubAllGlobals();
  delete shell.showToast;
  document.body.removeAttribute('data-side');
});

const q = <T extends Element = HTMLInputElement>(sel: string) => document.querySelector<T>(sel)!;
const posts = (url: string) =>
  calls.filter((c) => c.method === 'POST' && c.url === url).map((c) => c.body!);
async function shown() {
  act(() => {
    document.dispatchEvent(
      new CustomEvent('soulsync:video-page-shown', { detail: 'video-settings' }),
    );
  });
  await waitFor(() =>
    expect(q<HTMLSelectElement>('[data-video-lib-select="tv"]').value).toBe('TV'),
  );
  await waitFor(() => expect(q('[data-video-conn="plex-url"]').value).toBe('http://plex:32400'));
}

/** the card on the page and the fields its sheet holds */
function VideoServerConnection() {
  return (
    <>
      <Card onView={onView} />
      <VideoServerFields />
      <State />
    </>
  );
}
const onView = vi.fn();
function State() {
  const state = useVideoServerState();
  return <output data-testid="state">{`${state.tone}: ${state.text}`}</output>;
}

describe('the video media server', () => {
  it('one card on the parts: a pick-one row, Test and View configuration in its head, no colons', async () => {
    const { container } = render(<VideoServerConnection />);
    await shown();
    const cards = container.querySelectorAll('[data-card]');
    expect(cards).toHaveLength(1);
    expect(cards[0].getAttribute('data-card')).toBe('Server connections');
    // one heading: the old group's own "Server Connection" heading went
    expect(container.querySelectorAll('h3')).toHaveLength(0);
    const radios = [...container.querySelectorAll('[role="radio"]')];
    expect(radios.map((r) => r.textContent)).toEqual(['Plex', 'Jellyfin', 'Standalone']);
    expect(q('[data-video-server-toggle="jellyfin"]').getAttribute('aria-checked')).toBe('true');
    fireEvent.click(
      [...container.querySelectorAll('button')].find(
        (b) => b.textContent === 'View configuration',
      )!,
    );
    expect(onView).toHaveBeenCalled();
    for (const l of container.querySelectorAll('label')) expect(l.textContent).not.toMatch(/:\s*$/);
    // its state in words: the key is missing
    expect(document.querySelector('[data-testid="state"]')!.textContent).toBe('warn: Not set up');
  });

  it('loads the server, its fields, the user and the libraries', async () => {
    render(<VideoServerConnection />);
    await shown();
    expect(q('[data-video-server-toggle="jellyfin"]').getAttribute('aria-checked')).toBe('true');
    expect(q<HTMLElement>('[data-video-server-config="plex"]').hidden).toBe(true);
    expect(q<HTMLElement>('[data-video-server-config="jellyfin"]').hidden).toBe(false);
    expect(q('[data-video-conn="plex-token"]').value).toBe('tok');
    expect(q('[data-video-conn-note="plex"]').textContent).toBe(
      'Inherited from your music Plex connection. Change it to use a different server for video.',
    );
    expect(q('[data-video-conn-note="jellyfin"]').textContent).toBe(
      'Add the API key to finish this connection.',
    );
    await waitFor(() =>
      expect(q<HTMLElement>('[data-video-jellyfin-user-wrap]').hidden).toBe(false),
    );
    expect(q<HTMLSelectElement>('[data-video-jellyfin-user]').value).toBe('u2');
    expect(
      [...document.querySelectorAll('[data-video-jellyfin-user] option')].map((o) => o.textContent),
    ).toEqual(['Select User', 'Boss (admin)', 'Kid']);
    expect(q<HTMLSelectElement>('[data-video-lib-select="movies"]').value).toBe('Movies');
    // the other TV libraries, without the main one
    expect(
      [...document.querySelectorAll('[data-video-lib-tv-scan]')].map((l) =>
        l.getAttribute('data-video-lib-tv-scan'),
      ),
    ).toEqual(['Anime', 'Kids TV']);
    expect(q('[data-video-lib-tv-scan="Kids TV"]').checked).toBe(true);
    expect(q<HTMLElement>('[data-video-lib-tv-more-row]').hidden).toBe(false);
  });

  it('a field saves both servers when left, then reads the notes again', async () => {
    render(<VideoServerConnection />);
    await shown();
    const key = q('[data-video-conn="jellyfin-key"]');
    fireEvent.input(key, { target: { value: 'jk' } });
    key.dispatchEvent(new Event('change', { bubbles: true }));
    await waitFor(() => expect(posts('/api/video/server-config')).toHaveLength(1));
    expect(posts('/api/video/server-config')[0]).toEqual({
      plex: { base_url: 'http://plex:32400', token: 'tok' },
      jellyfin: { base_url: 'http://jf:8096', api_key: 'jk' },
    });
    expect(shell.showToast).toHaveBeenCalledWith('Connection saved', 'success');
    await waitFor(() =>
      expect(calls.filter((c) => c.url === '/api/video/server-config')).toHaveLength(3),
    );
  });

  it('the toggle shows that server and makes it the video server', async () => {
    render(<VideoServerConnection />);
    await shown();
    fireEvent.click(q('[data-video-server-toggle="plex"]'));
    expect(q<HTMLElement>('[data-video-server-config="plex"]').hidden).toBe(false);
    await waitFor(() => expect(posts('/api/video/server')).toEqual([{ server: 'plex' }]));
    await waitFor(() =>
      expect(shell.showToast).toHaveBeenCalledWith('Video server set to Plex', 'success'),
    );
  });

  it('Standalone: no server to test or configure, no libraries to pick, and it says what it does', async () => {
    const { container } = render(<VideoServerConnection />);
    await shown();
    fireEvent.click(q('[data-video-server-toggle="soulsync"]'));
    await waitFor(() => expect(posts('/api/video/server')).toEqual([{ server: 'soulsync' }]));
    expect(q('[data-video-server-toggle="soulsync"]').getAttribute('aria-checked')).toBe('true');
    expect(shell.showToast).toHaveBeenCalledWith(
      'Video library set to Standalone: SoulSync scans your folders',
      'success',
    );
    expect(document.querySelector('[data-video-lib-select]')).toBeNull();
    expect(q<HTMLElement>('[data-video-standalone]').textContent).toMatch(
      /scans your Movies and TV folders/,
    );
    const btn = (label: string) =>
      [...container.querySelectorAll('button')].find((b) => b.textContent === label)!;
    expect(btn('Test server').disabled).toBe(true);
    expect(btn('View configuration').disabled).toBe(true);
    expect(document.querySelector('[data-testid="state"]')!.textContent).toBe(
      'ok: no media server',
    );
    // and back: the libraries return
    fireEvent.click(q('[data-video-server-toggle="plex"]'));
    await waitFor(() =>
      expect(document.querySelector('[data-video-lib-select="tv"]')).not.toBeNull(),
    );
  });

  it('with both servers connected: a switch to keep them in step, its state in words, Link now', async () => {
    answers['GET /api/video/server/links'] = {
      success: true,
      enabled: false,
      other: 'plex',
      movies: 0,
      movies_total: 8929,
      shows: 0,
      shows_total: 3482,
      last: { state: 'idle' },
    };
    answers['POST /api/video/server/links'] = {
      success: true,
      started: true,
      enabled: true,
      other: 'plex',
      movies: 8812,
      movies_total: 8929,
      shows: 3400,
      shows_total: 3482,
      last: { state: 'done' },
    };
    render(<VideoServerConnection />);
    await shown();
    await waitFor(() =>
      expect(q<HTMLElement>('[data-video-links-status]').textContent).toBe(
        'Off. Plex is connected too.',
      ),
    );
    expect(q<HTMLButtonElement>('[data-video-links-run]').disabled).toBe(true);
    fireEvent.click(q('[data-video-links-switch]'));
    await waitFor(() => expect(posts('/api/video/server/links')).toEqual([{ enabled: true }]));
    await waitFor(() =>
      expect(q<HTMLElement>('[data-video-links-status]').textContent).toBe(
        '8,812 of 8,929 movies and 3,400 of 3,482 shows are on Plex too.',
      ),
    );
    fireEvent.click(q('[data-video-links-run]'));
    await waitFor(() =>
      expect(posts('/api/video/server/links')).toEqual([{ enabled: true }, { run: true }]),
    );
  });

  it('one server: nothing to link, no row', async () => {
    answers['GET /api/video/server/links'] = { success: true, enabled: false, other: null };
    render(<VideoServerConnection />);
    await shown();
    expect(document.querySelector('[data-video-links-switch]')).toBeNull();
  });

  it('Test saves quietly first, then says what the server said', async () => {
    answers['POST /api/video/server-config/test'] = { success: false, error: 'refused' };
    render(<VideoServerConnection />);
    await shown();
    fireEvent.click(q('[data-video-conn-test="jellyfin"]'));
    await waitFor(() =>
      expect(shell.showToast).toHaveBeenCalledWith('Jellyfin connection failed: refused', 'error'),
    );
    expect(posts('/api/video/server-config/test')).toEqual([{ server: 'jellyfin' }]);
    expect(shell.showToast).not.toHaveBeenCalledWith('Connection saved', 'success');
    // and says it in words where the server is
    await waitFor(() =>
      expect(document.querySelector('[data-testid="state"]')!.textContent).toBe('bad: refused'),
    );
  });

  it('a user picked saves and reads its libraries', async () => {
    render(<VideoServerConnection />);
    await shown();
    await waitFor(() =>
      expect(q<HTMLSelectElement>('[data-video-jellyfin-user]').value).toBe('u2'),
    );
    fireEvent.change(q('[data-video-jellyfin-user]'), { target: { value: 'u1' } });
    await waitFor(() => expect(posts('/api/video/jellyfin/user')).toEqual([{ user: 'u1' }]));
    await waitFor(() =>
      expect(shell.showToast).toHaveBeenCalledWith('Jellyfin user updated', 'success'),
    );
    expect(calls.filter((c) => c.url === '/api/video/libraries' && c.method === 'GET').length).toBe(
      2,
    );
  });

  it('the picks and ticks save at once; Anime ticks Scan; the main pick keeps its Anime', async () => {
    render(<VideoServerConnection />);
    await shown();
    fireEvent.click(q('[data-video-lib-tv-anime="Anime"]'));
    await waitFor(() => expect(posts('/api/video/libraries')).toHaveLength(1));
    expect(posts('/api/video/libraries')[0]).toEqual({
      movies: 'Movies',
      tv: 'TV',
      tv_more: ['Anime', 'Kids TV'],
      anime: ['Anime', 'TV'],
    });
    expect(q('[data-video-lib-tv-scan="Anime"]').checked).toBe(true);
    await waitFor(() => expect(q('[data-video-lib-save-status]').textContent).toBe('Saved'));
    // a new main pick: the old one joins the others with its ticks (Anime,
    // which it kept as the main pick), as the page did
    fireEvent.change(q('[data-video-lib-select="tv"]'), { target: { value: 'Kids TV' } });
    await waitFor(() => expect(posts('/api/video/libraries')).toHaveLength(2));
    expect(posts('/api/video/libraries')[1]).toEqual({
      movies: 'Movies',
      tv: 'Kids TV',
      tv_more: ['Anime'],
      anime: ['TV', 'Anime'],
    });
    expect(
      [...document.querySelectorAll('[data-video-lib-tv-scan]')].map((l) =>
        l.getAttribute('data-video-lib-tv-scan'),
      ),
    ).toEqual(['TV', 'Anime']);
  });

  it('the libraries say what is wrong; the save button saves both quietly', async () => {
    answers['GET /api/video/libraries'] = { movies: [], tv: [], selected: {} };
    render(<VideoServerConnection />);
    act(() => {
      document.dispatchEvent(
        new CustomEvent('soulsync:video-page-shown', { detail: 'video-settings' }),
      );
    });
    await waitFor(() =>
      expect(q('[data-video-lib-save-status]').textContent).toBe(
        'No movie or TV libraries found. Check the server connection and that the user above can see them.',
      ),
    );
    const button = document.createElement('button');
    button.id = 'save-settings';
    document.body.append(button);
    document.body.setAttribute('data-side', 'video');
    fireEvent.click(button);
    await waitFor(() => expect(posts('/api/video/libraries')).toHaveLength(1));
    expect(posts('/api/video/server-config')).toHaveLength(1);
    expect(shell.showToast).not.toHaveBeenCalled();
    button.remove();
  });

  it('the notes', () => {
    expect(connNote('plex', false, false, false)).toBe(
      'Not connected: add a server URL and token.',
    );
    expect(connNote('jellyfin', false, true, false)).toBe(
      'Add the server URL to finish this connection.',
    );
    expect(connNote('plex', true, true, false)).toBe('Custom video connection.');
  });
});

describe('the note under a half-filled video connection', () => {
  it('says music’s connection is in use instead of not set up', async () => {
    const { connNote } = await import('./video-server');
    expect(connNote('plex', true, false, false, true)).toBe(
      'Using your music Plex connection until this one has both a server URL and a token.',
    );
    // without a working music connection it still asks for the token
    expect(connNote('plex', true, false, false, false)).toBe(
      'Add the token to finish this connection.',
    );
    expect(connNote('plex', false, false, true, true)).toMatch(/^Not connected/);
  });
});
