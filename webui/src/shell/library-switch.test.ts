/**
 * The header's library switcher (#1199): drawn only when there is something to
 * switch between, and a pick is one POST plus the event every page follows.
 */

import { HttpResponse, http } from 'msw';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { server } from '@/test/msw';

import { LIBRARY_SCOPE_CHANGED_EVENT, pickLibrary, refreshLibrarySwitch } from './library-switch';

const SWITCHABLE = {
  success: true,
  switchable: true,
  current: 'shared',
  current_name: 'Shared library',
  target: 'shared',
  target_name: 'Shared library',
  options: [
    { id: 'shared', name: 'Shared library', root: '/music/Transfer', files: 1200 },
    { id: '3', name: 'Kim', root: '/music/kim', files: 1 },
    { id: 'all', name: 'All libraries', files: 1201 },
  ],
};

let scopes: Record<string, unknown> = SWITCHABLE;
let posted: unknown[] = [];

beforeEach(() => {
  document.body.innerHTML = '<div class="sidebar-header"><div id="profile-indicator"></div></div>';
  scopes = SWITCHABLE;
  posted = [];
  server.use(
    http.get('/api/library/v2/scopes', () => HttpResponse.json(scopes)),
    http.post('/api/library/v2/scope', async ({ request }) => {
      const body = (await request.json()) as { scope: string };
      posted.push(body);
      scopes = {
        ...SWITCHABLE,
        current: body.scope,
        current_name: "Kim's library",
        target: body.scope,
        target_name: "Kim's library",
      };
      return HttpResponse.json({ success: true, scope: body.scope });
    }),
  );
});

afterEach(() => {
  document.body.innerHTML = '';
  document.documentElement.removeAttribute('data-library-scope');
});

describe('the library switcher', () => {
  it('sits under the profile and names the current library', async () => {
    await refreshLibrarySwitch();
    const root = document.getElementById('library-switch');
    expect(root?.hidden).toBe(false);
    expect(root?.previousElementSibling?.id).toBe('profile-indicator');
    expect(root?.textContent).toContain('Shared library');
  });

  it('is drawn nowhere when there is nothing to switch between', async () => {
    scopes = { success: true, switchable: false, current: 'shared', options: [] };
    await refreshLibrarySwitch();
    const root = document.getElementById('library-switch');
    expect(root?.hidden).toBe(true);
    expect(root?.innerHTML).toBe('');
  });

  it('lists every library with its folder and says where downloads land', async () => {
    await refreshLibrarySwitch();
    (document.querySelector('.library-switch-trigger') as HTMLButtonElement).click();
    const options = [...document.querySelectorAll('.library-switch-option')];
    expect(options.map((o) => (o as HTMLElement).dataset.scope)).toEqual(['shared', '3', 'all']);
    expect(options[1].textContent).toContain('/music/kim');
    expect(document.querySelector('.library-switch-foot')?.textContent).toContain('Shared library');
  });

  it('a pick posts once and tells every page', async () => {
    await refreshLibrarySwitch();
    const heard: unknown[] = [];
    const onChange = (e: Event) => heard.push((e as CustomEvent).detail);
    window.addEventListener(LIBRARY_SCOPE_CHANGED_EVENT, onChange);
    try {
      expect(await pickLibrary('3')).toBe(true);
    } finally {
      window.removeEventListener(LIBRARY_SCOPE_CHANGED_EVENT, onChange);
    }
    expect(posted).toEqual([{ scope: '3' }]);
    expect(heard).toEqual([{ scope: '3' }]);
    expect(document.documentElement.getAttribute('data-library-scope')).toBe('own');
    expect(document.getElementById('library-switch')?.textContent).toContain('Kim');
  });

  it('closes the list at once and takes one pick at a time', async () => {
    await refreshLibrarySwitch();
    (document.querySelector('.library-switch-trigger') as HTMLButtonElement).click();
    const first = pickLibrary('3');
    expect(document.getElementById('library-switch-menu')?.hidden).toBe(true);
    expect(await pickLibrary('all')).toBe(false);
    expect(await first).toBe(true);
    expect(posted).toEqual([{ scope: '3' }]);
  });

  it('drops GETs the shared dedupe kept for the old library', async () => {
    const entries = new Map([['/api/library/v2/artists', {}]]);
    (window as { _apiGetDedupe?: unknown })._apiGetDedupe = { entries };
    try {
      await refreshLibrarySwitch();
      await pickLibrary('3');
      expect(entries.size).toBe(0);
    } finally {
      delete (window as { _apiGetDedupe?: unknown })._apiGetDedupe;
    }
  });

  it('picking the library already current sends nothing', async () => {
    await refreshLibrarySwitch();
    expect(await pickLibrary('shared')).toBe(true);
    expect(posted).toEqual([]);
  });
});
