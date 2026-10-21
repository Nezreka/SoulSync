/**
 * The open/close lifecycle the vanilla shell relies on, the saved draft, and
 * the ready screen's finish button.
 */

import { act, fireEvent, render, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';

import { browserHref, FinishSetupCard } from './checklist-card';
import { takeSetupPlaylistLink } from './handoff';
import { mountOnboardingHost, wireSetupPreviewControls } from './host';
import { emptyDraft } from './plan';
import { ReadyStep } from './steps/ready';
import { neighbours, stripSecrets, useOnboarding } from './store';

const DRAFT_KEY = 'soulsync:onboarding-draft';

beforeEach(() => {
  // the mounted host runs its own environment and discovery calls whenever a
  // session opens
  server.use(
    http.get('*/api/onboarding/environment', () =>
      HttpResponse.json({ docker: false, platform: 'linux', folders: {} }),
    ),
    http.post('*/api/onboarding/discover', () => HttpResponse.json({ found: {} })),
  );
  localStorage.clear();
  sessionStorage.clear();
  window._onSetupWizardComplete = null;
  act(() => useOnboarding.getState().close());
});

afterEach(() => {
  act(() => useOnboarding.getState().close());
});

describe('the saved draft', () => {
  it('keeps no secret, ever', () => {
    const d = emptyDraft();
    d.account.pin = '1234';
    d.account.password = 'pw';
    d.server.token = 'plex-token';
    d.slskd.apiKey = 'slskd-key';
    d.deezerArl = 'arl';
    d.spotify.clientSecret = 'sp';
    d.listenbrainz.token = 'lb';
    d.lastfm.apiKey = 'lf';
    d.video.tmdbKey = 'tmdb-secret';
    d.video.prowlarr.apiKey = 'pk';
    d.video.client.password = 'qb';
    d.video.client.apiKey = 'sab';
    const saved = JSON.stringify(stripSecrets(d));
    for (const secret of [
      '1234',
      '"pw"',
      'plex-token',
      'slskd-key',
      '"arl"',
      '"sp"',
      '"lb"',
      '"lf"',
      'tmdb-secret',
      '"pk"',
      '"qb"',
      '"sab"',
    ])
      expect(saved).not.toContain(secret);
  });

  it('a real session saves progress, the preview never does', () => {
    act(() => useOnboarding.getState().begin({ preview: true }));
    act(() => useOnboarding.getState().update((d) => void (d.account.name = 'Preview')));
    expect(localStorage.getItem(DRAFT_KEY)).toBeNull();

    act(() => useOnboarding.getState().begin({}));
    act(() => useOnboarding.getState().update((d) => void (d.account.name = 'Real')));
    act(() => useOnboarding.getState().go('server'));
    expect(JSON.parse(localStorage.getItem(DRAFT_KEY)!)).toMatchObject({
      step: 'server',
      draft: { account: { name: 'Real' } },
    });

    // "finish later" then coming back resumes where it stopped
    act(() => useOnboarding.getState().close());
    act(() => useOnboarding.getState().begin({}));
    expect(useOnboarding.getState().step).toBe('server');
    expect(useOnboarding.getState().draft.account.name).toBe('Real');
  });

  it('the preview starts clean even with a saved real draft', () => {
    act(() => useOnboarding.getState().begin({}));
    act(() => useOnboarding.getState().update((d) => void (d.account.name = 'Real')));
    act(() => useOnboarding.getState().begin({ preview: true }));
    expect(useOnboarding.getState().draft.account.name).toBe('');
    expect(useOnboarding.getState().step).toBe('welcome');
  });

  it('a preview started on movies and tv turns that step on', () => {
    act(() => useOnboarding.getState().begin({ preview: true, startAt: 'video' }));
    expect(useOnboarding.getState().draft.purposes).toContain('video');
  });

  it('back and next skip steps the purposes rule out', () => {
    const d = emptyDraft();
    d.purposes = ['video'];
    expect(neighbours('folders', d)).toEqual({ prev: 'server', next: 'video' });
    expect(neighbours('video', d)).toEqual({ prev: 'folders', next: 'ready' });
  });
});

describe('the shell handshake', () => {
  it('closing a real session hands control back to init.js once, the preview never does', () => {
    mountOnboardingHost();
    const done = vi.fn();
    window._onSetupWizardComplete = done;

    act(() => window.openSetupWizard?.({ preview: true }));
    act(() => window.closeSetupWizard?.());
    expect(done).not.toHaveBeenCalled();

    act(() => window.openSetupWizard?.());
    act(() => window.closeSetupWizard?.());
    act(() => window.closeSetupWizard?.());
    expect(done).toHaveBeenCalledTimes(1);
  });

  it('the developer preview button opens a preview at the chosen step', () => {
    const root = document.createElement('div');
    root.innerHTML = `
      <select id="ob-preview-step"><option value="sources" selected>x</option></select>
      <select id="ob-preview-env"><option value="bare" selected>x</option></select>
      <button id="ob-preview-open"></button>`;
    wireSetupPreviewControls(root);
    wireSetupPreviewControls(root);
    act(() => root.querySelector<HTMLButtonElement>('#ob-preview-open')!.click());
    const st = useOnboarding.getState();
    expect(st).toMatchObject({ open: true, preview: true, step: 'sources', pretend: 'bare' });
  });
});

describe('the ready screen', () => {
  function stubChecks() {
    server.use(
      http.post('*/api/onboarding/check-paths', () => HttpResponse.json({ results: {} })),
      http.post('*/api/onboarding/probe', () => HttpResponse.json({ ok: true, message: 'ok' })),
      http.get('*/api/discover/build-playlist/search-artists', () =>
        HttpResponse.json({ artists: [] }),
      ),
    );
  }

  function openAtReady(preview: boolean) {
    act(() => useOnboarding.getState().begin({ preview, startAt: 'ready' }));
    act(() =>
      useOnboarding.getState().update((d) => {
        d.account.name = 'Boulder';
        d.account.lock = 'open';
        d.server.kind = 'soulsync';
        d.playlistUrl = 'https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M';
      }),
    );
  }

  it('a failed save offers a retry, never Open SoulSync', async () => {
    stubChecks();
    const complete = vi.fn();
    server.use(
      http.post('*/api/settings', () =>
        HttpResponse.json({ success: false, error: 'Disk full' }, { status: 500 }),
      ),
      http.post('*/api/setup/complete', () => {
        complete();
        return HttpResponse.json({ success: true });
      }),
    );
    openAtReady(false);
    const screen = within(render(<ReadyStep onFinished={() => undefined} />).container);
    expect(await screen.findByText('Not saved yet')).toBeInTheDocument();
    expect(screen.getByText('Disk full')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Open SoulSync/ })).toBeNull();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
    expect(complete).not.toHaveBeenCalled();
  });

  it('a refused completion keeps the screen up with the reason', async () => {
    server.use(http.all('*/api/*', () => HttpResponse.json({ success: true })));
    stubChecks();
    server.use(
      http.post('*/api/setup/complete', () =>
        HttpResponse.json({ success: false, error: 'Try again' }, { status: 503 }),
      ),
    );
    openAtReady(false);
    const finished = vi.fn();
    const screen = within(render(<ReadyStep onFinished={finished} />).container);
    const open = await screen.findByRole('button', { name: /Open SoulSync/ });
    await waitFor(() => expect(open).toBeEnabled());
    fireEvent.click(open);
    expect(await screen.findByRole('alert')).toHaveTextContent('Try again');
    expect(finished).not.toHaveBeenCalled();
    expect(takeSetupPlaylistLink()).toBeNull();
  });

  it('the preview closes without completing anything', async () => {
    const writes = vi.fn();
    server.use(
      http.all('*/api/*', ({ request }) => {
        // reads (environment, discovery, checks) are fine in a preview
        const path = new URL(request.url).pathname;
        if (!path.startsWith('/api/onboarding/')) writes(path);
        return HttpResponse.json({ success: true, found: {}, folders: {} });
      }),
    );
    stubChecks();
    openAtReady(true);
    const finished = vi.fn();
    const screen = within(render(<ReadyStep onFinished={finished} />).container);
    const close = await screen.findByRole('button', { name: /Close preview/ });
    await waitFor(() => expect(close).toBeEnabled());
    fireEvent.click(close);
    await waitFor(() => expect(finished).toHaveBeenCalled());
    expect(writes).not.toHaveBeenCalled();
    expect(takeSetupPlaylistLink()).toBeNull();
  });
});

it('checklist links to a docker-side address point at this host instead', () => {
  expect(browserHref('http://host.docker.internal:5030/shares')).toBe(
    `http://${window.location.hostname}:5030/shares`,
  );
  expect(browserHref('http://slskd.lan:5030/shares')).toBe('http://slskd.lan:5030/shares');
});

it('the checklist asks again when the tab comes back, so a share made in slskd ticks on return', async () => {
  let shared = false;
  let calls = 0;
  server.use(
    http.get('*/api/onboarding/checklist', () => {
      calls += 1;
      return HttpResponse.json({
        dismissed: false,
        items: [
          {
            id: 'slskd_shares',
            title: 'Share some music in slskd',
            description: '',
            action: 'Open slskd',
            done: shared,
          },
          {
            id: 'spotify',
            title: 'Connect Spotify',
            description: '',
            action: 'Connect',
            done: false,
          },
        ],
      });
    }),
  );
  const view = within(render(<FinishSetupCard />).container);
  expect(
    await view.findByRole('button', { name: 'Mark Share some music in slskd done' }),
  ).toBeInTheDocument();
  shared = true;
  act(() => document.dispatchEvent(new Event('visibilitychange')));
  expect(
    await view.findByRole('button', { name: 'Share some music in slskd, done' }),
  ).toBeInTheDocument();
  expect(calls).toBe(2);
});
