/**
 * The Quality tab's video sections (SETTINGS_PORT.md, video pass V1): the
 * markup they replaced, loading when the video settings page is shown, every
 * edit saved to the profile on screen (the Default or a named one), the
 * custom formats, the kind defaults, YouTube Quality, and the video side's
 * save button.
 */

import { act, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { shape, vanilla } from './__fixtures__/shape';
import { VideoQualitySections } from './video-quality';

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
const sent = (method: string, url: string) =>
  calls.filter((c) => c.method === method && c.url === url).map((c) => c.body);

async function shown() {
  act(() => {
    document.dispatchEvent(
      new CustomEvent('soulsync:video-page-shown', { detail: 'video-settings' }),
    );
  });
  await waitFor(() => expect(el('vq-tier-rows').children).toHaveLength(3));
  await waitFor(() =>
    expect(el('vq-format-rows').querySelectorAll('[data-vq-fmt]')).toHaveLength(1),
  );
}

describe('the markup they replaced', () => {
  it('before the video settings page is shown', () => {
    const { container } = render(<VideoQualitySections />);
    expect(shape(container)).toEqual(shape(vanilla('video-quality.vanilla.html')));
  });
});

describe('Video Quality', () => {
  it('shows the Default profile once the video page is shown', async () => {
    render(<VideoQualitySections />);
    await shown();
    expect(
      [...el('vq-tier-rows').querySelectorAll('.hybrid-source-name')].map((n) => n.textContent),
    ).toEqual(['BluRay · 1080p', 'WEB-DL · 1080p', 'HDTV · 720p']);
    expect(el('vq-tier-rows').children[2].className).toBe('hybrid-source-item disabled');
    expect(el<HTMLSelectElement>('vq-cutoff').value).toBe('1080p');
    expect(q('[data-vq-reject="cam"]').className).toBe('vq-chip on');
    expect(q('[data-vq-codec="hevc"]').className).toBe('active');
    expect(el('vq-langs').hidden).toBe(false);
    expect(q('[data-vq-lang="fr"]').getAttribute('aria-pressed')).toBe('true');
    expect(el('vq-movie-label').textContent).toBe('40 GB');
    expect(el('vq-seeders-label').textContent).toBe('At least 1 seeder');
    expect(q<HTMLSelectElement>('[data-vq-kind-default="show"]').value).toBe('3');
    expect(q('[data-vq-profile-name]').className).toBe('hidden');
    expect(q<HTMLInputElement>('[data-vq-fmt-f="override"]').value).toBe('50');
    expect(el('vq-min-format-score').value).toBe('5');
  });

  it('each edit saves the Default profile at once', async () => {
    render(<VideoQualitySections />);
    await shown();
    fireEvent.click(q('[data-vq-tier-move="web-1080p"][data-dir="-1"]'));
    expect(sent('POST', Q).at(-1)).toMatchObject({
      tiers: [
        { key: 'web-1080p', enabled: true },
        { key: 'bluray-1080p', enabled: true },
        { key: 'hdtv-720p', enabled: false },
      ],
    });
    fireEvent.click(q('[data-vq-reject="x264"]'));
    expect(sent('POST', Q).at(-1)).toMatchObject({ rejects: ['cam', 'x264'] });
    fireEvent.click(q('[data-vq-lang-mode="off"]'));
    expect(el('vq-langs').hidden).toBe(true);
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
    expect(q('[data-vq-codec="av1"]').className).toBe('active');
    expect(q<HTMLInputElement>('[data-vq-profile-name]').value).toBe('Anime');
    expect(q('[data-vq-profile-delete]').className).toBe('test-button');
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
    expect(q('[data-yq-codec="vp9"]').className).toBe('active');
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
