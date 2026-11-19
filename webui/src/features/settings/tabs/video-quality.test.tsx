/**
 * Quality > Movies and TV and Quality > YouTube (SETTINGS_PORT.md, video pass
 * V1; on the settings parts in settings-bic phase 3): the page read back,
 * loading when the video settings page is shown, every
 * edit saved to the profile on screen (the Default or a named one), the
 * custom formats, the kind defaults, YouTube Quality, and the video side's
 * save button.
 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { VideoQualitySections as Sections, YoutubeQuality } from './video-quality';
import { useVideoShown } from './video-shown';

/** both pills, as the Quality page has them */
function VideoQualitySections() {
  const shown = useVideoShown();
  return (
    <>
      <Sections />
      <YoutubeQuality shown={shown} />
    </>
  );
}

type Shell = Window & Record<string, unknown>;
const shell = window as unknown as Shell;
const Q = '/api/video/downloads/quality';
let calls: Array<{ url: string; method: string; body?: unknown }>;
let answers: Record<string, unknown>;

const PROFILE = {
  tiers: [
    { key: 'bluray-1080p', enabled: true },
    { key: 'web-1080p', enabled: true },
    { key: 'hdtv-720p', enabled: false },
  ],
  cutoff_resolution: '1080p',
  rejects: ['cam'],
  prefer_codec: 'hevc',
  prefer_hdr: 'off',
  prefer_audio: 'any',
  audio_language_mode: 'prefer',
  audio_languages: ['fr'],
  max_movie_gb: 40,
  min_format_score: 5,
  format_scores: { '7': 50 },
};

beforeEach(() => {
  calls = [];
  answers = {
    [`GET ${Q}/profiles`]: {
      profiles: [
        { id: 0, name: 'Default', profile: PROFILE },
        { id: 3, name: 'Anime', profile: { ...PROFILE, prefer_codec: 'av1' } },
      ],
    },
    [`GET ${Q}/defaults`]: { movie: 0, show: 3 },
    [`GET ${Q}/formats`]: {
      formats: [{ id: 7, name: 'Trusted', include: ['FLUX'], exclude: [], score: 20 }],
    },
    'GET /api/video/downloads/youtube-quality': {
      max_resolution: '1440p',
      video_codec: 'vp9',
      container: 'mkv',
      prefer_60fps: true,
      allow_hdr: false,
    },
  };
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string, init?: RequestInit) => {
      const method = init?.method || 'GET';
      const body = typeof init?.body === 'string' ? (JSON.parse(init.body) as unknown) : undefined;
      calls.push({ url, method, body });
      const key = `${method} ${url}`;
      const data =
        key in answers ? answers[key] : method === 'POST' ? (body ?? { success: true }) : null;
      return Promise.resolve({ ok: true, json: () => Promise.resolve(data) } as Response);
    }),
  );
  shell.showToast = vi.fn();
  shell.showConfirmDialog = vi.fn(() => Promise.resolve(true));
  document.body.removeAttribute('data-side');
});

afterEach(() => {
  vi.unstubAllGlobals();
  delete shell.showToast;
  delete shell.showConfirmDialog;
});

const el = <T extends HTMLElement = HTMLInputElement>(id: string) =>
  document.getElementById(id) as T;
const q = <T extends HTMLElement = HTMLElement>(sel: string) => document.querySelector(sel) as T;
const tierRows = () => [...el('vq-tier-rows').querySelectorAll<HTMLElement>('li[data-id]')];
const on = (sel: string) =>
  q(sel).getAttribute('aria-checked') === 'true' || q(sel).getAttribute('aria-selected') === 'true';
const sent = (method: string, url: string) =>
  calls.filter((c) => c.method === method && c.url === url).map((c) => c.body);

async function shown() {
  act(() => {
    document.dispatchEvent(
      new CustomEvent('soulsync:video-page-shown', { detail: 'video-settings' }),
    );
  });
  await waitFor(() => expect(tierRows()).toHaveLength(3));
  await waitFor(() =>
    expect(el('vq-format-rows').querySelectorAll('[data-vq-fmt]')).toHaveLength(1),
  );
}

describe('the pages', () => {
  it('before the video settings page is shown: waiting, every control there, no colon', () => {
    const { container } = render(<VideoQualitySections />);
    expect(container.textContent).toContain('Loading the video quality profiles.');
    expect(container.textContent).toContain('Loading the YouTube profile.');
    for (const id of [
      'vq-profile-select',
      'vq-profile-name',
      'vq-cutoff',
      'vq-rejects',
      'vq-codec',
      'vq-hdr',
      'vq-audio',
      'vq-prefer-repack',
      'vq-lang-mode',
      'vq-library',
      'vq-movie-size',
      'vq-episode-size',
      'vq-movie-pref',
      'vq-episode-pref',
      'vq-min-seeders',
      'vq-min-format-score',
      'yq-resolution',
      'yq-codec',
      'yq-container',
      'yq-60fps',
      'yq-hdr',
    ])
      expect(container.querySelectorAll(`#${id}`), id).toHaveLength(1);
    for (const l of container.querySelectorAll('label'))
      expect(l.textContent, l.textContent ?? '').not.toMatch(/:\s*$/);
    expect(container.textContent).not.toMatch(/—/);
  });

  it('reads the profile and the YouTube profile back in words', async () => {
    render(<VideoQualitySections />);
    await shown();
    const heroes = [
      ...document.querySelectorAll('[data-video-quality] p, [data-youtube-quality] p'),
    ];
    expect(heroes[0].textContent).toBe(
      'Default takes BluRay · 1080p, then WEB-DL · 1080p. It stops upgrading at 1080p.',
    );
    await waitFor(() =>
      expect(document.querySelector('[data-youtube-quality] p')!.textContent).toBe(
        'Followed channels download up to 1440p, as MKV, preferring VP9.',
      ),
    );
    expect(document.querySelector('[data-video-quality]')!.textContent).toContain(
      '2 of 3Qualities on',
    );
  });
});

describe('Video Quality', () => {
  it('shows the Default profile once the video page is shown', async () => {
    render(<VideoQualitySections />);
    await shown();
    expect(tierRows().map((r) => r.dataset.id)).toEqual(['bluray-1080p', 'web-1080p', 'hdtv-720p']);
    expect(tierRows()[0].textContent).toContain('BluRay · 1080p');
    // a quality switched off says so, and its switch is off
    expect(tierRows()[2].textContent).toContain('Switched off');
    expect(q<HTMLInputElement>('[data-vq-tier-toggle="hdtv-720p"]').checked).toBe(false);
    expect(el<HTMLSelectElement>('vq-cutoff').value).toBe('1080p');
    expect(on('[data-vq-reject="cam"]')).toBe(true);
    expect(on('[data-vq-codec="hevc"]')).toBe(true);
    expect(el('vq-langs').closest('[hidden]')).toBeNull();
    expect(on('[data-vq-lang="fr"]')).toBe(true);
    expect(el('vq-movie-label').textContent).toBe('40 GB');
    expect(el('vq-seeders-label').textContent).toBe('At least 1 seeder');
    expect(q<HTMLSelectElement>('[data-vq-kind-default="show"]').value).toBe('3');
    expect(q('[data-vq-profile-name]').closest('[hidden]')).not.toBeNull();
    expect(q<HTMLInputElement>('[data-vq-fmt-f="override"]').value).toBe('50');
    expect(el('vq-min-format-score').value).toBe('5');
  });

  it('each edit saves the Default profile at once', async () => {
    render(<VideoQualitySections />);
    await shown();
    // the ladder is the order list: its row menu moves it
    fireEvent.click(screen.getByRole('button', { name: 'More for WEB-DL · 1080p' }));
    fireEvent.click(screen.getByRole('menuitem', { name: /^Move up/ }));
    expect(sent('POST', Q).at(-1)).toMatchObject({
      tiers: [
        { key: 'web-1080p', enabled: true },
        { key: 'bluray-1080p', enabled: true },
        { key: 'hdtv-720p', enabled: false },
      ],
    });
    fireEvent.click(q('[data-vq-reject="x264"]'));
    expect(sent('POST', Q).at(-1)).toMatchObject({ rejects: ['cam', 'x264'] });
    fireEvent.click(q('[data-vq-tier-toggle="hdtv-720p"]'));
    expect(sent('POST', Q).at(-1)).toMatchObject({
      tiers: [
        { key: 'web-1080p', enabled: true },
        { key: 'bluray-1080p', enabled: true },
        { key: 'hdtv-720p', enabled: true },
      ],
    });
    fireEvent.click(q('[data-vq-lang-mode="off"]'));
    expect(el('vq-langs').closest('[hidden]')).not.toBeNull();
    fireEvent.click(q('[data-vq-library="4k"]'));
    expect(sent('POST', Q).at(-1)).toMatchObject({ audio_language_mode: 'off', library: '4k' });
    expect(shell.showToast).not.toHaveBeenCalled();
  });

  it('a slider shows the drag, and saves on letting go', async () => {
    render(<VideoQualitySections />);
    await shown();
    const before = sent('POST', Q).length;
    // dragging is input events; the change event comes on letting go
    fireEvent.input(el('vq-episode-pref'), { target: { value: '2.5' } });
    expect(el('vq-episode-pref-label').textContent).toBe('Around 2.5 GB');
    expect(sent('POST', Q)).toHaveLength(before);
    // the browser's change event: the slider let go
    el('vq-episode-pref').dispatchEvent(new Event('change', { bubbles: true }));
    await waitFor(() => expect(sent('POST', Q)).toHaveLength(before + 1));
    expect(sent('POST', Q).at(-1)).toMatchObject({ preferred_episode_gb: 2.5 });
  });

  it('a named profile saves through the profiles endpoint, with its name', async () => {
    render(<VideoQualitySections />);
    await shown();
    fireEvent.change(q('[data-vq-profile-select]'), { target: { value: '3' } });
    expect(on('[data-vq-codec="av1"]')).toBe(true);
    expect(q<HTMLInputElement>('[data-vq-profile-name]').value).toBe('Anime');
    expect(q('[data-vq-profile-delete]').closest('[hidden]')).toBeNull();
    fireEvent.click(q('[data-vq-hdr="prefer"]'));
    await waitFor(() => expect(sent('POST', `${Q}/profiles`)).toHaveLength(1));
    expect(sent('POST', `${Q}/profiles`)[0]).toMatchObject({
      id: 3,
      name: 'Anime',
      profile: { prefer_hdr: 'prefer', prefer_codec: 'av1' },
    });
    // Delete asks, then falls back to the Default
    answers[`DELETE ${Q}/profiles/3`] = { success: true };
    fireEvent.click(q('[data-vq-profile-delete]'));
    await waitFor(() => expect(calls.some((c) => c.method === 'DELETE')).toBe(true));
    expect(shell.showConfirmDialog).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'Delete this quality profile?', destructive: true }),
    );
    await waitFor(() => expect(q<HTMLSelectElement>('[data-vq-profile-select]').value).toBe('0'));
  });

  it('a named profile says what it is for; the Default is always both (#2168)', async () => {
    answers[`PUT ${Q}/profiles/3/media-type`] = { success: true, id: 3, media_type: 'tv' };
    render(<VideoQualitySections />);
    await shown();
    expect(q('[data-vq-used-for]')).toBeNull();
    fireEvent.change(q('[data-vq-profile-select]'), { target: { value: '3' } });
    expect(on('[data-vq-used-for-pick="both"]')).toBe(true);
    fireEvent.click(q('[data-vq-used-for-pick="tv"]'));
    await waitFor(() =>
      expect(shell.showToast).toHaveBeenCalledWith('Offered only for TV', 'success'),
    );
    expect(sent('PUT', `${Q}/profiles/3/media-type`)[0]).toEqual({ media_type: 'tv' });
    expect(on('[data-vq-used-for-pick="tv"]')).toBe(true);
    answers[`PUT ${Q}/profiles/3/media-type`] = { success: true, id: 3, media_type: null };
    fireEvent.click(q('[data-vq-used-for-pick="both"]'));
    await waitFor(() =>
      expect(sent('PUT', `${Q}/profiles/3/media-type`)[1]).toEqual({ media_type: null }),
    );
  });

  it("a kind's default saves on its own and says so", async () => {
    answers[`POST ${Q}/defaults`] = { movie: 3, show: 3 };
    render(<VideoQualitySections />);
    await shown();
    fireEvent.change(q('[data-vq-kind-default="movie"]'), { target: { value: '3' } });
    await waitFor(() =>
      expect(shell.showToast).toHaveBeenCalledWith('New movies use Anime', 'success'),
    );
    expect(sent('POST', `${Q}/defaults`)[0]).toEqual({ kind: 'movie', profile_id: 3 });
  });
});

describe('Custom Formats', () => {
  it('a field saves the format when left; the override is the profile', async () => {
    answers[`POST ${Q}/formats`] = {
      success: true,
      id: 7,
      name: 'Trusted+',
      include: ['FLUX'],
      exclude: [],
      score: 20,
    };
    render(<VideoQualitySections />);
    await shown();
    const name = q<HTMLInputElement>('[data-vq-fmt-f="name"]');
    fireEvent.input(name, { target: { value: 'Trusted+' } });
    name.dispatchEvent(new Event('change', { bubbles: true }));
    await waitFor(() => expect(sent('POST', `${Q}/formats`)).toHaveLength(1));
    expect(sent('POST', `${Q}/formats`)[0]).toEqual({
      id: 7,
      name: 'Trusted+',
      include: ['FLUX'],
      exclude: [],
      score: 20,
    });
    const override = q<HTMLInputElement>('[data-vq-fmt-f="override"]');
    fireEvent.input(override, { target: { value: '' } });
    override.dispatchEvent(new Event('change', { bubbles: true }));
    await waitFor(() => expect(sent('POST', Q).at(-1)).toMatchObject({ format_scores: {} }));
  });

  it('adds and deletes formats', async () => {
    answers[`POST ${Q}/formats`] = {
      success: true,
      id: 9,
      name: 'New format',
      include: ['REPLACE-ME'],
      exclude: [],
      score: 10,
    };
    answers[`DELETE ${Q}/formats/7`] = { success: true };
    render(<VideoQualitySections />);
    await shown();
    fireEvent.click(q('[data-vq-format-add]'));
    await waitFor(() =>
      expect(el('vq-format-rows').querySelectorAll('[data-vq-fmt]')).toHaveLength(2),
    );
    expect(sent('POST', `${Q}/formats`)[0]).toEqual({
      name: 'New format',
      include: ['REPLACE-ME'],
      score: 10,
    });
    fireEvent.click(q('[data-vq-fmt-del="7"]'));
    await waitFor(() =>
      expect(el('vq-format-rows').querySelectorAll('[data-vq-fmt]')).toHaveLength(1),
    );
  });
});

describe('YouTube Quality and the save button', () => {
  it('loads, saves each change, and the video side save button saves both profiles', async () => {
    render(<VideoQualitySections />);
    await shown();
    await waitFor(() => expect(el<HTMLSelectElement>('yq-resolution').value).toBe('1440p'));
    expect(on('[data-yq-codec="vp9"]')).toBe(true);
    expect(el('yq-60fps').checked).toBe(true);
    fireEvent.click(q('[data-yq-container="mp4"]'));
    expect(sent('POST', '/api/video/downloads/youtube-quality').at(-1)).toMatchObject({
      container: 'mp4',
    });
    const before = calls.length;
    const button = document.createElement('button');
    button.id = 'save-settings';
    document.body.append(button);
    button.click();
    expect(calls.length).toBe(before);
    document.body.setAttribute('data-side', 'video');
    button.click();
    await waitFor(() => expect(calls.length).toBe(before + 2));
    expect(
      calls
        .slice(before)
        .map((c) => c.url)
        .sort(),
    ).toEqual([Q, '/api/video/downloads/youtube-quality']);
    button.remove();
  });
});
