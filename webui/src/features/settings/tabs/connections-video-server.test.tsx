/**
 * The video side's Server Connection (SETTINGS_PORT.md, video pass V3b): the
 * markup it replaced, the load each time the video settings page is shown,
 * the toggle, the credentials and their notes, Test, the Jellyfin user, and
 * the library picks (the other TV libraries' Scan and Anime ticks).
 */

import { act, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { shape, vanilla } from './__fixtures__/shape';
import { VideoServerConnection } from './connections-video-server';
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

describe('the video Server Connection', () => {
  it('is the markup it replaced', () => {
    const { container } = render(<VideoServerConnection />);
    expect(shape(container)).toEqual(shape(vanilla('video-server.vanilla.html')));
  });

  it('loads the server, its fields, the user and the libraries', async () => {
    render(<VideoServerConnection />);
    await shown();
    expect(q('[data-video-server-toggle="jellyfin"]').className).toBe('server-toggle-btn active');
    expect(q('[data-video-server-config="plex"]').classList.contains('hidden')).toBe(true);
    expect(q('[data-video-server-config="jellyfin"]').classList.contains('hidden')).toBe(false);
    expect(q('[data-video-conn="plex-token"]').value).toBe('tok');
    expect(q('[data-video-conn-note="plex"]').textContent).toBe(
      'Inherited from your Music Plex connection — edit to use a different server for video.',
    );
    expect(q('[data-video-conn-note="jellyfin"]').textContent).toBe(
      'Add the API key to finish this connection.',
    );
    await waitFor(() =>
      expect(q<HTMLElement>('[data-video-jellyfin-user-wrap]').style.display).toBe('block'),
    );
    expect(q<HTMLSelectElement>('[data-video-jellyfin-user]').value).toBe('u2');
    expect(
      [...document.querySelectorAll('[data-video-jellyfin-user] option')].map((o) => o.textContent),
    ).toEqual(['Select User', 'Boss (admin)', 'Kid']);
    expect(q<HTMLSelectElement>('[data-video-lib-select="movies"]').value).toBe('Movies');
    // the other TV libraries, without the main one
    expect(
      [...document.querySelectorAll('.video-library-tv-more-item span')].map((s) => s.textContent),
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
    expect(q('[data-video-server-config="plex"]').classList.contains('hidden')).toBe(false);
    await waitFor(() => expect(posts('/api/video/server')).toEqual([{ server: 'plex' }]));
    await waitFor(() =>
      expect(shell.showToast).toHaveBeenCalledWith('Video server set to Plex', 'success'),
    );
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
      [...document.querySelectorAll('.video-library-tv-more-item span')].map((s) => s.textContent),
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
      'Not connected — add a server URL and token.',
    );
    expect(connNote('jellyfin', false, true, false)).toBe(
      'Add the server URL to finish this connection.',
    );
    expect(connNote('plex', true, true, false)).toBe('Custom video connection.');
  });
});
