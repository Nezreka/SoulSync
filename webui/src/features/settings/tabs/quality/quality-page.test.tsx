/**
 * The Quality page (settings-bic phase 3): one pill per kind, each showing
 * only what it is for, and the video profiles loaded when the music side
 * opens Movies and TV.
 */

import { act, cleanup, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { nav } from '../../nav';
import { createSettingsStore } from '../../settings-store';
import { videoShown } from '../video-shown';
import { QualityPage } from './quality-page';

beforeEach(() => {
  nav.reset();
  vi.stubGlobal(
    'fetch',
    vi.fn(() =>
      Promise.resolve(
        new Response(JSON.stringify({ success: true, profiles: [] }), {
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    ),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  document.body.removeAttribute('data-side');
});

function page() {
  const store = createSettingsStore({ post: () => Promise.resolve({ success: true }), delay: 0 });
  return render(<QualityPage store={store} opened={1} active />);
}
const panel = (view: string) =>
  document.querySelector<HTMLElement>(`[data-stg-tab="quality"][data-stg-view="${view}"]`)!;

describe('the Quality page', () => {
  it('shows only the open pill: no video cards on Music, no music cards on Movies and TV', () => {
    act(() => nav.go('quality', 'music'));
    page();
    expect(panel('music').hidden).toBe(false);
    for (const view of ['audiobooks', 'video', 'youtube']) expect(panel(view).hidden).toBe(true);
    // what each pill holds is its own
    expect(panel('music').querySelector('#quality-upgrade-policy')).not.toBeNull();
    expect(panel('music').querySelector('#vq-cutoff, #yq-resolution')).toBeNull();
    expect(panel('video').querySelector('#vq-cutoff')).not.toBeNull();
    expect(panel('video').querySelector('#yq-resolution, #quality-upgrade-policy')).toBeNull();
    expect(panel('youtube').querySelector('#yq-resolution')).not.toBeNull();
    expect(panel('audiobooks').querySelector('#audiobook-format-first')).not.toBeNull();
    // the music side never showed the video settings page: opening Movies
    // and TV loads the video profiles
    expect(videoShown.get()).toBe(0);
    act(() => nav.view('video'));
    expect(panel('music').hidden).toBe(true);
    expect(panel('video').hidden).toBe(false);
    expect(videoShown.get()).toBe(1);
  });
});
