/**
 * What the source panels look like inside the side sheet, as behaviour: every
 * control each panel had is still there, by id; connection fields come first,
 * tuning sits in named groups; torrent and usenet say which settings are for
 * music and which for video; rare ones wait behind "More options", which says
 * how many; labels name the setting without a colon or an em dash; and the
 * one Test is the sheet's (only the four panels whose own Test sent the same
 * request lost theirs).
 *
 * These replace the frozen vanilla markup comparisons (the markup was
 * replaced on purpose). The key guard (guard/) holds what each control saves.
 */

import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createSettingsStore, type SettingsStore } from '../settings-store';
import { SourcePanels } from './source-panels';
import { MoreSourcePanels } from './source-panels-more';
import { videoDownloads } from './video-downloads';

let store: SettingsStore;

beforeEach(() => {
  store = createSettingsStore({ post: () => Promise.resolve({ success: true }), delay: 0 });
  videoDownloads.reset();
  vi.stubGlobal(
    'fetch',
    vi.fn(() =>
      Promise.resolve({ ok: true, json: () => Promise.resolve({}) } as unknown as Response),
    ),
  );
});
afterEach(() => vi.unstubAllGlobals());

function all() {
  render(
    <>
      <SourcePanels store={store} part="soulseek" opened={0} active={false} />
      <SourcePanels store={store} part="hifi" opened={0} active={false} />
      <SourcePanels store={store} part="tidal-qobuz" opened={0} active={false} />
      <SourcePanels store={store} part="deezer-soundcloud-lidarr" opened={0} active={false} />
      <MoreSourcePanels store={store} part="youtube" opened={0} active={false} />
      <MoreSourcePanels store={store} part="prowlarr-torrent-usenet" opened={0} active={false} />
    </>,
  );
}

/** every form control each panel had before it was rebuilt (from the
 *  vanilla fixtures this replaces), less the one that moved to Behaviour */
const HAD: Record<string, string[]> = {
  'soulseek-settings-container': [
    'soulseek-url',
    'soulseek-api-key',
    'soulseek-search-timeout',
    'soulseek-search-timeout-buffer',
    'soulseek-search-min-delay-seconds',
    'soulseek-min-peer-speed',
    'soulseek-max-peer-queue',
    'soulseek-observed-speed-fallback-enabled',
    'soulseek-min-observed-download-speed',
    'soulseek-download-timeout',
    'soulseek-auto-clear-searches',
    'soulseek-cleanup-scope',
  ],
  'hifi-download-settings-container': ['hifi-new-instance'],
  'qobuz-settings-container': ['qobuz-email', 'qobuz-password', 'qobuz-download-token'],
  'deezer-download-settings-container': ['deezer-download-arl'],
  'soundcloud-download-settings-container': ['soundcloud-download-oauth-token'],
  'lidarr-download-settings-container': ['lidarr-url', 'lidarr-api-key'],
  'youtube-settings-container': [
    'youtube-cookies-browser',
    'youtube-cookies-paste',
    'youtube-download-delay',
    'youtube-album-bundle',
    'youtube-transcode',
    'youtube-transcode-codec',
    'youtube-transcode-bitrate',
    'ytdlp-channel',
  ],
  'prowlarr-settings-container': [
    'prowlarr-url',
    'prowlarr-api-key',
    'prowlarr-indexer-ids',
    'prowlarr-search-min-gap',
    'prowlarr-max-searches-per-minute',
  ],
  'torrent-client-settings-container': [
    'torrent-client-type',
    'torrent-client-url',
    'torrent-client-username',
    'torrent-client-password',
    'torrent-client-category',
    'torrent-client-save-path',
    'torrent-client-start-torrents',
    'torrent-download-path',
    'torrent-min-seeders',
    'torrent-stall-timeout',
    'torrent-stall-action',
    'music-seed-ratio',
    'music-seed-hours',
    'music-seed-remove-data',
    'music-seed-mode',
    'video-seed-ratio',
    'video-seed-hours',
    'video-seed-remove-data',
    'video-seed-mode',
  ],
  'usenet-client-settings-container': [
    'usenet-client-type',
    'usenet-client-url',
    'usenet-client-api-key',
    'usenet-client-username',
    'usenet-client-password',
    'usenet-client-category',
    'usenet-download-path',
    'music-usenet-remove-completed',
    'video-usenet-remove-completed',
  ],
};

/** the wrappers other code and tests reach for by id */
const WRAPPERS = [
  'youtube-cookies-paste-group',
  'youtube-transcode-options',
  'youtube-cookie-abe-warning',
  'torrent-client-username-group',
  'torrent-client-help',
  'usenet-apikey-group',
  'usenet-username-group',
  'usenet-password-group',
  'music-seeding-goals',
  'video-seeding-goals',
  'music-usenet-remove-completed-group',
  'video-usenet-remove-completed-group',
  'video-seed-idx-rows',
  'hifi-instances-list',
  'hifi-instances-status-panel',
  'prowlarr-indexer-list',
  'ytdlp-installed',
  'ytdlp-latest',
  'ytdlp-status',
  'ytdlp-detail',
  'ytdlp-hint-badge',
  'ytdlp-update-btn',
  'qobuz-auth-logged-in',
  'qobuz-auth-form',
  'qobuz-auth-user-info',
  'qobuz-auth-status',
  'qobuz-download-token-status',
  'tidal-download-auth-btn',
  'tidal-download-auth-status',
  'tidal-download-auth-code',
  'deezer-download-status',
  'soundcloud-connection-status',
  'hifi-connection-status',
  'prowlarr-connection-status',
  'prowlarr-status-dot',
  'torrent-client-status-dot',
  'usenet-client-status-dot',
];

const panel = (id: string) => document.getElementById(id)!;

/** the sheet group a control sits under: the nearest group title above it */
function groupOf(el: Element): string {
  const root = el.closest('[data-react-panel]')!;
  let title = '';
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    if ((node as Element).tagName === 'H4') title = node.textContent ?? '';
    if (node === el) return title;
  }
  return '';
}

/** behind a closed "More options": inside a [data-more-body] */
const behindMore = (el: Element) => !!el.closest('[data-more-body]');

describe('every panel, on the new parts', () => {
  it('keeps every control it had, each inside its own panel, once', () => {
    all();
    for (const [container, ids] of Object.entries(HAD)) {
      for (const id of ids) {
        expect(document.querySelectorAll(`#${id}`), id).toHaveLength(1);
        expect(panel(container).contains(document.getElementById(id)), id).toBe(true);
      }
    }
    // the next-best release rule moved to Downloads > Behaviour
    expect(document.getElementById('video-redownload-failed')).toBeNull();
    for (const id of WRAPPERS) expect(document.getElementById(id), id).not.toBeNull();
    // a panel's subtree is the new parts': the old page's field rules skip it
    for (const container of Object.keys(HAD))
      expect(panel(container).hasAttribute('data-ui'), container).toBe(true);
  });

  it('names each setting in sentence case, with no colon, no em dash and no emoji', () => {
    all();
    for (const container of Object.keys(HAD)) {
      const labels = [...panel(container).querySelectorAll('label')].map(
        (l) => l.textContent?.trim() ?? '',
      );
      for (const label of labels) {
        expect(label, `${container}: ${label}`).not.toMatch(/:$/);
        // a name the maker writes in lower case (slskd, yt-dlp) is the exception
        if (!/^(slskd|yt-dlp)\b/.test(label)) expect(label).not.toMatch(/^[a-z]/);
      }
      const words = panel(container).textContent ?? '';
      expect(words, container).not.toContain('—');
      expect(words, container).not.toMatch(/[⬢✔✘✖☰]/);
    }
  });

  it('puts the unit inside the number field, not in the label', () => {
    all();
    const unitOf = (id: string) =>
      document.getElementById(id)!.parentElement!.querySelector('span:last-child')?.textContent;
    expect(unitOf('soulseek-search-timeout')).toBe('seconds');
    expect(unitOf('soulseek-download-timeout')).toBe('minutes');
    expect(unitOf('soulseek-min-observed-download-speed')).toBe('KB/s');
    expect(unitOf('torrent-stall-timeout')).toBe('minutes');
    expect(unitOf('youtube-download-delay')).toBe('seconds');
    const label = document.querySelector('label[for="soulseek-search-timeout"]')!.textContent;
    expect(label).toBe('Search timeout');
  });

  it('starts with the connection: the first group of each account panel', () => {
    all();
    const firstGroup = (container: string) =>
      panel(container).querySelector('h4')?.textContent ?? '';
    expect(firstGroup('soulseek-settings-container')).toBe('Connection');
    expect(firstGroup('lidarr-download-settings-container')).toBe('Connection');
    expect(firstGroup('prowlarr-settings-container')).toBe('Connection');
    expect(firstGroup('torrent-client-settings-container')).toBe('Connection');
    expect(firstGroup('usenet-client-settings-container')).toBe('Connection');
    expect(firstGroup('deezer-download-settings-container')).toBe('Account');
    expect(firstGroup('tidal-download-settings-container')).toBe('Account');
    expect(firstGroup('qobuz-settings-container')).toBe('Account');
    expect(groupOf(document.getElementById('soulseek-url')!)).toBe('Connection');
    expect(groupOf(document.getElementById('soulseek-search-timeout')!)).toBe('Searching');
  });
});

describe('torrent and usenet: for music, for video', () => {
  it('each tuning setting sits under the side it is for', () => {
    all();
    const under = (id: string) => groupOf(document.getElementById(id)!);
    for (const id of [
      'torrent-min-seeders',
      'torrent-stall-timeout',
      'torrent-stall-action',
      'music-seed-ratio',
      'music-seed-hours',
      'music-seed-remove-data',
      'music-seed-mode',
      'torrent-download-path',
      'music-usenet-remove-completed',
      'usenet-download-path',
    ])
      expect(under(id), id).toBe('For music');
    for (const id of [
      'video-seed-ratio',
      'video-seed-hours',
      'video-seed-remove-data',
      'video-seed-mode',
      'video-usenet-remove-completed',
    ])
      expect(under(id), id).toBe('For video');
    // neither panel still says "Seeding goals (torrents):" twice
    expect(document.body.textContent).not.toContain('Seeding goals (torrents)');
  });
});

describe('More options', () => {
  it('holds the rare settings, closed, and says how many', () => {
    all();
    const soulseek = panel('soulseek-settings-container');
    const more = within(soulseek).getByRole('button', { name: /^More options/ });
    expect(more.getAttribute('aria-expanded')).toBe('false');
    const body = document.getElementById(more.getAttribute('aria-controls')!)!;
    expect(body.hidden).toBe(true);
    const inside = [...body.querySelectorAll('input, select')].map((el) => el.id);
    expect(inside).toEqual([
      'soulseek-search-timeout-buffer',
      'soulseek-search-min-delay-seconds',
      'soulseek-min-observed-download-speed',
      'soulseek-download-timeout',
      'soulseek-auto-clear-searches',
      'soulseek-cleanup-scope',
    ]);
    expect(more.textContent).toContain(`· ${inside.length}`);
    // first-run settings are not behind it
    for (const id of ['soulseek-url', 'soulseek-api-key', 'soulseek-search-timeout'])
      expect(behindMore(document.getElementById(id)!), id).toBe(false);
    fireEvent.click(more);
    expect(body.hidden).toBe(false);
  });

  it('every More in a panel counts what it holds', () => {
    all();
    for (const container of Object.keys(HAD)) {
      for (const more of panel(container).querySelectorAll<HTMLElement>('[data-more]')) {
        const count = /· (\d+)/.exec(more.textContent ?? '')?.[1];
        if (!count) continue;
        const body = document.getElementById(more.getAttribute('aria-controls')!)!;
        const held = body.querySelectorAll('input, select, textarea').length;
        expect(held, `${container}: ${more.textContent}`).toBe(Number(count));
      }
    }
  });

  it('help is one line; the rest is behind the "i"', () => {
    all();
    fireEvent.click(
      within(panel('soulseek-settings-container')).getByRole('button', { name: /^More options/ }),
    );
    const row = document
      .getElementById('soulseek-search-min-delay-seconds')!
      .closest('[data-row]')!;
    const info = within(row as HTMLElement).getByRole('button', {
      name: 'More about this setting',
    });
    const rest = document.getElementById(info.getAttribute('aria-controls')!)!;
    expect(rest.hidden).toBe(true);
    expect(row.textContent).toContain('Forces a gap between searches.');
    fireEvent.click(info);
    expect(rest.hidden).toBe(false);
    expect(rest.textContent).toContain('Bell Canada');
  });
});

describe('one Test per thing', () => {
  it("the four panels whose Test was the sheet foot's request have none; the rest say what they show", () => {
    all();
    for (const gone of ['lidarr-test-btn', 'torrent-client-test-btn', 'usenet-client-test-btn'])
      expect(document.getElementById(gone), gone).toBeNull();
    for (const container of Object.keys(HAD))
      expect(
        within(panel(container)).queryByRole('button', { name: /^Test .*Connection$/i }),
        container,
      ).toBeNull();
    // kept, renamed for what they show
    const name = (id: string) => document.getElementById(id)!.textContent;
    expect(name('deezer-download-test-btn')).toBe('Check account');
    expect(name('soundcloud-test-btn')).toBe('Check status');
    expect(name('hifi-test-btn')).toBe('Check status');
    expect(name('prowlarr-test-btn')).toBe('Test and load indexers');
    expect(name('hifi-instances-check-btn')).toBe('Check All Instances');
    // every button still on a panel that tests something is one of those
    const testing = [...document.querySelectorAll('[data-react-panel] button')]
      .map((b) => b.textContent ?? '')
      .filter((t) => /test|check/i.test(t));
    expect(testing.sort()).toEqual(
      [
        'Check account',
        'Check status',
        'Check status',
        'Check All Instances',
        'Test and load indexers',
      ].sort(),
    );
  });

  it('a saved key shows as dots and says why it cannot be shown', () => {
    act(() => store.hydrate({ soulseek: { api_key: '__redacted_unchanged__' } }));
    render(<SourcePanels store={store} part="soulseek" opened={0} active={false} />);
    const field = document.getElementById('soulseek-api-key') as HTMLInputElement;
    expect(field.type).toBe('password');
    expect(field.closest('[data-field]')!.textContent).toContain(
      'Saved keys are never sent back to the browser.',
    );
    expect(screen.queryByRole('button', { name: 'Show what you typed' })).toBeNull();
  });
});
