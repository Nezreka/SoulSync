/**
 * Library > Organization (SETTINGS_PORT.md, step 8b): the markup it replaced
 * (with the path preview drawn, and the parked vanilla panels back in their
 * places), the help words with their spaces, the template rules, each saved
 * key, the preview, Reset, and the settings search reaching a React tab.
 */

import { act, fireEvent, render, waitFor } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createSettingsHost } from '../host';
import { createSettingsStore, type SettingsPayload, type SettingsStore } from '../settings-store';
import { shape, vanilla, withHelpLines } from './__fixtures__/shape';
import { LibraryTab } from './library-folders';
import { OrganizationCard } from './library-organization';
import { musicOrgSummary, SCENARIO_KEY, templateErrors, templatesOf } from './organization';

type Shell = Window & Record<string, unknown>;
const shell = window as unknown as Shell;

let store: SettingsStore;
let posted: SettingsPayload[];
let previews: Array<Record<string, unknown>>;
let answer: (body: Record<string, unknown>) => unknown;

/** the parked panels, as index.html has them */
function park() {
  const html = readFileSync(resolve(process.cwd(), 'index.html'), 'utf8');
  const start = html.indexOf('<div id="library-parked-panels" hidden>');
  let depth = 0;
  const re = /<(\/?)div\b[^>]*>/g;
  re.lastIndex = start;
  let m: RegExpExecArray | null;
  let end = start;
  while ((m = re.exec(html))) {
    depth += m[1] ? -1 : 1;
    if (depth === 0) {
      end = m.index + m[0].length;
      break;
    }
  }
  document.body.insertAdjacentHTML('beforeend', html.slice(start, end));
  withHelpLines(document.getElementById('library-parked-panels')!);
}

beforeEach(() => {
  const sink: SettingsPayload[] = [];
  posted = sink;
  store = createSettingsStore({
    post: (body) => {
      sink.push(structuredClone(body));
      return Promise.resolve({ success: true });
    },
    delay: 0,
  });
  previews = [];
  answer = () => ({
    success: true,
    paths: {
      album_path: 'Daft Punk/Daft Punk - Discovery/01 - One More Time.flac',
      single_path: 'Daft Punk/Daft Punk - One More Time/One More Time.flac',
      single_as_album: 'Daft Punk/Daft Punk - One More Time/01 - One More Time.flac',
      compilation_path: { error: 'bad template' },
      disc_label: 'Daft Punk/Daft Punk - Discovery/Disc 2/01 - One More Time.flac',
    },
    disc_label_used: true,
  });
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string, init?: RequestInit) => {
      const body = JSON.parse(typeof init?.body === 'string' ? init.body : '{}') as Record<
        string,
        unknown
      >;
      if (url === '/api/settings/path-preview') previews.push(body);
      return Promise.resolve({ ok: true, json: () => Promise.resolve(answer(body)) } as Response);
    }),
  );
  localStorage.removeItem(SCENARIO_KEY);
});

afterEach(async () => {
  await act(() => store.flush());
  vi.unstubAllGlobals();
  delete shell.debouncedAutoSaveSettings;
  document.getElementById('library-parked-panels')?.remove();
  localStorage.removeItem(SCENARIO_KEY);
});

const card = () =>
  render(<OrganizationCard store={store} part="organization" opened={0} active={false} />);
const input = (id: string) => document.getElementById(id) as HTMLInputElement;
function saved(): SettingsPayload {
  const merged: SettingsPayload = {};
  for (const body of posted)
    for (const [k, v] of Object.entries(body)) merged[k] = { ...merged[k], ...v };
  return merged;
}

describe('the Organization card is the markup it replaced', () => {
  it('with the preview drawn, the React Audiobooks panel and the vanilla Video panel', () => {
    park();
    const { container } = card();
    const want = shape(vanilla('library-organization.vanilla.html', { afterInit: true }));
    const got = shape(container);
    expect(got.elements).toEqual(want.elements);
    expect(got.text).toBe(want.text);
    // only the Folders card's vanilla video panel waits in the park now
    expect(
      [...document.getElementById('library-parked-panels')!.children].map((el) => el.id),
    ).toEqual(['folders-video-panel']);
  });

  it('every help reads the same, spaces included', () => {
    const words = (root: Element) =>
      [...root.querySelectorAll('.setting-help-body, .help-text, .stg-help-text, .settings-hint')]
        .filter((el) => !el.closest('#organization-audiobooks-panel, #organization-video-panel'))
        .map((el) => (el.textContent || '').replace(/\s+/g, ' ').trim());
    const { container } = card();
    expect(words(container)).toEqual(
      words(vanilla('library-organization.vanilla.html', { afterInit: true })),
    );
  });

  it('a tick box keeps the space before its words', () => {
    // the markup's line break between <input> and the words is a space on
    // screen; JSX drops it unless it is written in
    const labels = (root: Element) =>
      [...root.querySelectorAll('label')]
        .filter((el) => !el.closest('#organization-audiobooks-panel, #organization-video-panel'))
        .filter((el) => el.querySelector('input[type="checkbox"]'))
        .map((el) => (el.textContent || '').replace(/\s+/g, ' ').trimEnd());
    const { container } = card();
    const want = labels(vanilla('library-organization.vanilla.html'));
    expect(want.every((t) => t.startsWith(' '))).toBe(true);
    expect(labels(container)).toEqual(want);
  });
});

describe('the rules', () => {
  it('fields show the saved template or the default; the old single default upgrades', () => {
    const t = templatesOf({
      file_organization: {
        templates: { album_path: 'A/$title', single_path: '$artist/$artist - $title/$title' },
      },
    });
    expect(t.album_path).toBe('A/$title');
    expect(t.single_path).toBe('$albumartist/$albumartist - $title/$title');
    expect(t.podcast_path).toBe('$show/Season $season/$title');
    expect(t.playlist_item).toBe('');
  });

  it('checks the templates as validateFileOrganizationTemplates did', () => {
    const errors = (templates: Record<string, string>) =>
      templateErrors({ file_organization: { templates } });
    expect(errors({})).toEqual([]);
    expect(errors({ album_path: '/$album/' })).toEqual([
      'Album template cannot end with /',
      'Album template cannot start with /',
    ]);
    expect(errors({ album_path: '$album' })).toEqual([
      'Album template must include at least one folder (use / separator)',
    ]);
    // a single may be a flat file
    expect(errors({ single_path: '$artist - $title' })).toEqual([]);
    expect(errors({ sample_path: '$artist//$chop' })).toEqual([
      'Sample template cannot have consecutive slashes //',
    ]);
    expect(errors({ podcast_path: '$Show/${title}' })).toEqual([
      'Variable "$Show" should be lowercase: "$show"',
    ]);
    expect(errors({ album_path: '$artist/$nope' })[0]).toMatch(
      /^Invalid variable "\$nope" in album template\. Valid: \$artist, \$albumartist/,
    );
  });

  it('sums the card up as LIBRARY_SUMMARIES did', () => {
    expect(musicOrgSummary(false, 'a/b/c')).toBe('organizing off');
    expect(musicOrgSummary(true, '$albumartist/$album/$track - $title')).toBe(
      '$album/$track - $title',
    );
    expect(musicOrgSummary(true, ' ')).toBe('organizing on');
  });
});

describe('the card', () => {
  it('loads every field with its default, and each change saves its own key', async () => {
    act(() =>
      store.hydrate({
        file_organization: { templates: { audiobook_path: '$author/$title' } },
        wishlist: {},
        library: {},
      }),
    );
    card();
    expect(input('file-organization-enabled').checked).toBe(true);
    expect(input('auto-disambiguation').checked).toBe(true);
    expect(input('detect-multi-artist-compilations').checked).toBe(true);
    expect(input('artistletter-symbol-fallback').checked).toBe(false);
    expect(input('template-single-path').value).toBe('$albumartist/$albumartist - $title/$title');
    expect(input('disc-label').value).toBe('Disc');
    expect(input('artist-separator').value).toBe(', ');
    expect(input('allow-duplicate-tracks').checked).toBe(true);
    expect(input('wishlist-ignore-ttl').value).toBe('30');
    expect(input('reorganize-preserve-casing').checked).toBe(true);
    expect(input('write-artist-nfo').checked).toBe(false);
    expect(input('redownload-externally-deleted').checked).toBe(true);
    expect(input('podcast-media-format').value).toBe('audio');

    fireEvent.change(input('template-album-path'), {
      target: { value: '$albumartist/$album/$title' },
    });
    fireEvent.click(input('unicode-substitution'));
    fireEvent.change(input('collab-artist-mode'), { target: { value: 'all' } });
    fireEvent.change(input('artist-separator'), { target: { value: '; ' } });
    fireEvent.click(input('feat-in-title'));
    fireEvent.click(input('wishlist-wing-it-guesses'));
    fireEvent.change(input('wishlist-ignore-ttl'), { target: { value: '900' } });
    fireEvent.click(input('write-artist-nfo'));
    fireEvent.change(input('template-podcast-path'), { target: { value: '$show/$title' } });
    fireEvent.change(input('podcast-media-format'), { target: { value: 'both' } });
    fireEvent.click(input('redownload-externally-deleted'));
    await waitFor(() => expect(store.hasPending()).toBe(false));
    expect(saved()).toEqual({
      file_organization: {
        'templates.album_path': '$albumartist/$album/$title',
        unicode_substitution: true,
        collab_artist_mode: 'all',
        'templates.podcast_path': '$show/$title',
      },
      // one key of each dict: the rest of it is kept on the server
      metadata_enhancement: { 'tags.artist_separator': '; ', 'tags.feat_in_title': true },
      wishlist: { wing_it_guesses: true, ignore_ttl_days: 365 },
      library: { write_artist_nfo: true, redownload_externally_deleted: false },
      podcasts: { media_format: 'both' },
    });
    // the audiobook template the server holds is untouched in the copy
    expect(store.getState().settings.file_organization?.templates).toMatchObject({
      audiobook_path: '$author/$title',
      album_path: '$albumartist/$album/$title',
    });
    // a blank window saves the default
    fireEvent.change(input('wishlist-ignore-ttl'), { target: { value: '' } });
    expect(input('wishlist-ignore-ttl').value).toBe('');
    await waitFor(() => expect(store.hasPending()).toBe(false));
    expect(saved().wishlist?.ignore_ttl_days).toBe(30);
  });

  it('a template that would not work holds every save back, and the page save hears why', async () => {
    const host = createSettingsHost({ store, tabs: { library: LibraryTab }, effects: [] });
    act(() => store.hydrate({ file_organization: {} }));
    card();
    fireEvent.change(input('template-album-path'), { target: { value: '$album' } });
    store.set('ui_appearance', 'sidebar_visualizer', 'wave');
    expect(await store.flush()).toBe(false);
    expect(posted).toEqual([]);
    expect(host.templateErrors()).toEqual([
      'Album template must include at least one folder (use / separator)',
    ]);
    fireEvent.change(input('template-album-path'), { target: { value: '$albumartist/$album' } });
    expect(await store.flush()).toBe(true);
    expect(saved()).toEqual({
      file_organization: { 'templates.album_path': '$albumartist/$album' },
      ui_appearance: { sidebar_visualizer: 'wave' },
    });
  });

  it('the summary follows the switch and the album template once loaded', () => {
    card();
    const summary = () => document.querySelector('[data-stg-summary="library-music-org"]')!;
    expect(summary().textContent).toBe('File naming templates & reorganize');
    act(() => store.hydrate({ file_organization: { enabled: false } }));
    expect(summary().textContent).toBe('organizing off');
    expect(summary().classList.contains('is-live')).toBe(true);
    fireEvent.click(input('file-organization-enabled'));
    expect(summary().textContent).toBe('$albumartist - $album/$track - $title');
  });

  it('Reset writes every default, the audiobook template included', async () => {
    act(() =>
      store.hydrate({
        file_organization: {
          templates: { album_path: 'x/y', compilation_path: 'C/$album', playlist_item: '$title' },
        },
      }),
    );
    card();
    fireEvent.change(input('template-audiobook-path'), { target: { value: 'mine' } });
    fireEvent.change(input('template-album-path'), { target: { value: 'typed/$title' } });
    fireEvent.click(
      document.querySelector('#organization-music-panel .form-group:last-child button')!,
    );
    expect(input('template-audiobook-path').value).toBe('$author/$series/$seriespos - $title');
    expect(input('template-album-path').value).toBe(
      '$albumartist/$albumartist - $album/$track - $title',
    );
    await waitFor(() => expect(store.hasPending()).toBe(false));
    expect(saved().file_organization).toEqual({
      'templates.album_path': '$albumartist/$albumartist - $album/$track - $title',
      'templates.single_path': '$albumartist/$albumartist - $title/$title',
      'templates.playlist_path': '$playlist/$artist - $title',
      'templates.video_path': '$artist/$title-video',
      'templates.podcast_path': '$show/Season $season/$title',
      'templates.audiobook_path': '$author/$series/$seriespos - $title',
      'templates.sample_path': '$artist/$track - $chop',
    });
    // the compilation template and playlist file naming are left alone, as before
    expect(input('template-compilation-path').value).toBe('C/$album');
    expect(input('template-playlist-item').value).toBe('$title');
  });

  it('an emptied template stays empty until the next load shows the default', async () => {
    act(() => store.hydrate({ file_organization: { templates: { album_path: 'x/y' } } }));
    card();
    fireEvent.change(input('template-album-path'), { target: { value: '' } });
    await waitFor(() => expect(store.hasPending()).toBe(false));
    expect(input('template-album-path').value).toBe('');
    expect(saved().file_organization).toEqual({ 'templates.album_path': '' });
    act(() => store.hydrate({ file_organization: { templates: { album_path: '' } } }));
    expect(input('template-album-path').value).toBe(
      '$albumartist/$albumartist - $album/$track - $title',
    );
  });
});

describe('the path preview', () => {
  const slot = (id: string) => input(id).nextElementSibling as HTMLElement;

  it('renders each template once loaded, with its notes, and follows typing', async () => {
    card();
    expect(previews).toEqual([]);
    act(() => store.hydrate({ file_organization: { disc_label: 'CD' } }));
    await waitFor(() => expect(previews).toHaveLength(1));
    expect(previews[0]).toMatchObject({
      scenario: 'standard',
      templates: { album_path: '$albumartist/$albumartist - $album/$track - $title' },
      settings: { auto_disambiguation: true, collab_artist_mode: 'first', disc_label: 'CD' },
    });
    await waitFor(() =>
      expect(slot('template-album-path').textContent).toBe(
        'Daft Punk/Daft Punk - Discovery/01 - One More Time.flac',
      ),
    );
    const album = slot('template-album-path');
    expect(album.className).toBe('path-preview');
    expect([...album.querySelectorAll('code > span')].map((s) => s.className)).toEqual([
      'path-preview-folder',
      'path-preview-sep',
      'path-preview-folder',
      'path-preview-sep',
      'path-preview-file',
    ]);
    expect(album.querySelector('code')!.title).toBe(
      'Daft Punk/Daft Punk - Discovery/01 - One More Time.flac',
    );
    // a single the source calls one gets the album-template line under it
    expect(slot('template-single-path').querySelectorAll('.path-preview-row')).toHaveLength(2);
    expect(slot('template-single-path').textContent).toContain(
      'A release the source calls a single uses the album template until you change this one',
    );
    expect(slot('disc-label').textContent).toContain('Disc 2 of a two-disc album');
    expect(slot('template-compilation-path').textContent).toBe('bad template');
    expect(slot('template-video-path').textContent).toBe('Nothing to preview');

    fireEvent.change(input('template-album-path'), { target: { value: '$album/$title' } });
    await waitFor(() => expect(previews).toHaveLength(2));
    expect((previews[1].templates as Record<string, string>).album_path).toBe('$album/$title');
  });

  it('the scenario is remembered and asked for; organizing off hides it all', async () => {
    act(() => store.hydrate({ file_organization: {} }));
    card();
    await waitFor(() => expect(previews).toHaveLength(1));
    const multidisc = document.querySelector<HTMLButtonElement>('[data-scenario="multidisc"]')!;
    fireEvent.click(multidisc);
    await waitFor(() => expect(previews).toHaveLength(2));
    expect(previews[1].scenario).toBe('multidisc');
    expect(localStorage.getItem(SCENARIO_KEY)).toBe('multidisc');
    expect(multidisc.getAttribute('aria-checked')).toBe('true');

    fireEvent.click(input('file-organization-enabled'));
    expect(document.getElementById('path-preview-scenarios')!.hidden).toBe(true);
    expect(slot('template-album-path').hidden).toBe(true);
    await new Promise((done) => setTimeout(done, 20));
    expect(previews).toHaveLength(2);
  });

  it('says when the preview cannot be had', async () => {
    answer = () => ({ success: false, error: 'boom' });
    act(() => store.hydrate({ file_organization: {} }));
    card();
    await waitFor(() =>
      expect(slot('template-sample-path').textContent).toBe('Preview unavailable: boom'),
    );
    expect(slot('template-sample-path').firstElementChild!.className).toBe(
      'path-preview-row path-preview-error',
    );
  });
});
