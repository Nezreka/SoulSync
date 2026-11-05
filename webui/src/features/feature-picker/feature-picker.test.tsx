/**
 * #1909: the "Make SoulSync yours" picker. Each switch saves on its own, a
 * failed save puts the switch back, presets save in one go, and nobody gets
 * a switch for a page they can't open.
 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { installFeatures } from '@/shell/features';
import { HttpResponse, http, server } from '@/test/msw';

import { FeaturePicker, reachable } from './feature-picker';

const FEATURES = [
  { id: 'discover', label: 'Discover', desc: 'Picks.', group: 'music', pages: ['discover'] },
  { id: 'music_videos', label: 'Music videos', desc: 'Videos.', group: 'music', pages: [] },
  { id: 'podcasts', label: 'Podcasts', desc: 'Shows.', group: 'more', pages: ['podcasts'] },
  { id: 'video', label: 'Movies & TV', desc: 'Video.', group: 'more', pages: ['video-dashboard'] },
  { id: 'tools', label: 'Tools', desc: 'Repair.', group: 'advanced', pages: ['tools'] },
];
const SIMPLE = ['music_videos', 'podcasts', 'video', 'tools'];

function payload(off: string[]) {
  return {
    success: true,
    features: FEATURES,
    groups: [
      { id: 'music', label: 'Music' },
      { id: 'more', label: 'More than music' },
      { id: 'advanced', label: 'For tinkerers' },
    ],
    presets: [
      { id: 'full', label: 'Everything', desc: 'All of it.', off: [] },
      { id: 'simple', label: 'Simple', desc: 'Less.', off: SIMPLE },
    ],
    off,
    preset: 'custom',
    hidden_pages: FEATURES.filter((f) => off.includes(f.id)).flatMap((f) => f.pages),
  };
}

type G = { isPageAllowed?: unknown; profileAllowedSides?: unknown };
let posts: unknown[] = [];
let stored: string[] = [];

beforeEach(() => {
  posts = [];
  stored = [];
  localStorage.clear();
  document.body.innerHTML =
    '<nav class="sidebar-nav"><a class="nav-button" data-page="podcasts"></a></nav>';
  server.use(
    http.get('*/api/profiles/me/features', () => HttpResponse.json(payload(stored))),
    http.post('*/api/profiles/me/features', async ({ request }) => {
      const body = (await request.json()) as { off?: string[]; preset?: string };
      posts.push(body);
      stored = body.preset === 'simple' ? SIMPLE : body.preset === 'full' ? [] : (body.off ?? []);
      return HttpResponse.json(payload(stored));
    }),
  );
  installFeatures();
});

afterEach(() => {
  delete (window as G).isPageAllowed;
  delete (window as G).profileAllowedSides;
});

async function openPicker() {
  render(<FeaturePicker />);
  await act(async () => window.openFeaturePicker?.());
  await screen.findByText('Make SoulSync yours');
  await screen.findByText('Podcasts');
}

const switchFor = (name: string) => screen.getByRole('switch', { name });

describe('the feature picker', () => {
  it('lists every feature as on for a fresh profile', async () => {
    await openPicker();
    for (const f of FEATURES) expect(switchFor(f.label)).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByRole('radio', { name: /Everything/ })).toHaveAttribute(
      'aria-checked',
      'true',
    );
  });

  it('a switch saves on its own and the sidebar follows', async () => {
    await openPicker();
    fireEvent.click(switchFor('Podcasts'));
    await waitFor(() => expect(posts).toEqual([{ off: ['podcasts'] }]));
    await waitFor(() =>
      expect(
        document.querySelector('[data-page="podcasts"]')!.classList.contains('nav-feature-hidden'),
      ).toBe(true),
    );
    expect(switchFor('Podcasts')).toHaveAttribute('aria-checked', 'false');
  });

  it('a preset saves in one go and lights up', async () => {
    await openPicker();
    fireEvent.click(screen.getByRole('radio', { name: /Simple/ }));
    await waitFor(() => expect(posts).toEqual([{ preset: 'simple' }]));
    await waitFor(() =>
      expect(screen.getByRole('radio', { name: /Simple/ })).toHaveAttribute('aria-checked', 'true'),
    );
    expect(switchFor('Discover')).toHaveAttribute('aria-checked', 'true');
    expect(switchFor('Tools')).toHaveAttribute('aria-checked', 'false');
  });

  it('a failed save puts the switch back and says so', async () => {
    await openPicker();
    server.use(
      http.post('*/api/profiles/me/features', () =>
        HttpResponse.json({ success: false, error: 'nope' }, { status: 500 }),
      ),
    );
    fireEvent.click(switchFor('Tools'));
    await screen.findByText(/didn't save/);
    expect(switchFor('Tools')).toHaveAttribute('aria-checked', 'true');
  });

  it('skips what the profile cannot open anyway', async () => {
    (window as G).isPageAllowed = (page: string) => page !== 'tools';
    (window as G).profileAllowedSides = () => 'music';
    await openPicker();
    expect(screen.queryByRole('switch', { name: 'Tools' })).toBeNull();
    expect(screen.queryByRole('switch', { name: 'Movies & TV' })).toBeNull();
    // no pages of its own: lives inside pages they have
    expect(switchFor('Music videos')).toBeInTheDocument();
  });
});

describe('reachable', () => {
  const f = (pages: string[], id = 'x') => ({ id, label: '', desc: '', group: '', pages });
  it('needs one page the profile may open', () => {
    const g = { isPageAllowed: (p: string) => p === 'b' };
    expect(reachable(f(['a', 'b']), g)).toBe(true);
    expect(reachable(f(['a']), g)).toBe(false);
    expect(reachable(f([]), g)).toBe(true);
  });
  it('drops the video side for a music-only profile', () => {
    expect(reachable(f(['video-dashboard'], 'video'), { profileAllowedSides: () => 'music' })).toBe(
      false,
    );
    expect(reachable(f(['video-dashboard'], 'video'), { profileAllowedSides: () => 'both' })).toBe(
      true,
    );
  });
});
