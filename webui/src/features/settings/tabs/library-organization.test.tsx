/**
 * Library > Naming, Music and Podcasts, and the pieces of the old Organization
 * card drawn elsewhere now (tag options, wishlist rules, re-download, podcast
 * format) (settings-bic phase 4): the template rules, each saved key, the
 * "Saves as" preview under each field, a bad template marked on its own field
 * and named in the save status, Reset (asking first), and the hero.
 */

import { act, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createSettingsHost } from '../host';
import { createSettingsStore, type SettingsPayload, type SettingsStore } from '../settings-store';
import { Card } from '../ui';
import { AudiobookNaming } from './library-audiobooks';
import {
  MusicNaming,
  MusicNamingHero,
  MusicTagOptions,
  PodcastImport,
  PodcastNaming,
  RedownloadDeleted,
  WishlistRules,
} from './library-organization';
import { LibraryTab } from './library/library-page';
import {
  musicOrgSummary,
  playlistItemError,
  SCENARIO_KEY,
  templateErrors,
  templateFieldErrors,
  templatesOf,
} from './organization';

type Shell = Window & Record<string, unknown>;
const shell = window as unknown as Shell;

let store: SettingsStore;
let posted: SettingsPayload[];
let previews: Array<Record<string, unknown>>;
let answer: (body: Record<string, unknown>) => unknown;

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
  delete shell.showConfirmDialog;
  localStorage.removeItem(SCENARIO_KEY);
});

const card = () =>
  render(
    <div>
      <MusicNamingHero store={store} />
      <MusicNaming store={store} />
      <Card>
        <MusicTagOptions store={store} />
        <RedownloadDeleted store={store} />
      </Card>
      <WishlistRules store={store} />
      <AudiobookNaming store={store} />
      <PodcastNaming store={store} />
      <PodcastImport store={store} />
    </div>,
  );
/** a segmented control's value, and a pick of one of its options */
const seg = (id: string) => document.getElementById(id)!.getAttribute('data-value');
const pick = (id: string, value: string) =>
  fireEvent.click(document.querySelector(`#${id} [data-value="${CSS.escape(value)}"]`)!);
const input = (id: string) => document.getElementById(id) as HTMLInputElement;
function saved(): SettingsPayload {
  const merged: SettingsPayload = {};
  for (const body of posted)
    for (const [k, v] of Object.entries(body)) merged[k] = { ...merged[k], ...v };
  return merged;
}

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

  it('the playlist file naming rule: a file name with $title, empty is off', () => {
    const err = (playlist_item: string) =>
      playlistItemError({ file_organization: { templates: { playlist_item } } });
    expect(err('')).toBeNull();
    expect(err('$position - $title')).toBeNull();
    expect(err('$artist/$title')).toBe(
      "Playlist File Naming can't contain a folder separator ( / or \\ ): it names the file, not a path.",
    );
    expect(err('$artist\\$title')).toMatch(/folder separator/);
    expect(err('$position - $artist')).toBe(
      'Playlist File Naming must include $title so every file has a name.',
    );
  });

  it('sums the card up as LIBRARY_SUMMARIES did', () => {
    expect(musicOrgSummary(false, 'a/b/c')).toBe('organizing off');
    expect(musicOrgSummary(true, '$albumartist/$album/$track - $title')).toBe(
      '$album/$track - $title',
    );
    expect(musicOrgSummary(true, ' ')).toBe('organizing on');
  });

  it('each bad template is named by its own key', () => {
    expect(
      templateFieldErrors({
        file_organization: { templates: { album_path: '$album', sample_path: '$a//$chop' } },
      }),
    ).toEqual({
      album_path: ['Album template must include at least one folder (use / separator)'],
      sample_path: [
        'Sample template cannot have consecutive slashes //',
        'Invalid variable "$a" in sample template. Valid: $artist, $track, $album, $chop, $stem',
      ],
    });
  });
});

describe('the page', () => {
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
    // the small choices are segments now
    expect(seg('disc-label')).toBe('Disc');
    expect(seg('collab-artist-mode')).toBe('first');
    expect(seg('artist-separator')).toBe(', ');
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
    pick('collab-artist-mode', 'all');
    pick('artist-separator', '; ');
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

  it('a template that would not work is marked on its own field and named in the save status', async () => {
    const host = createSettingsHost({ store, tabs: { library: LibraryTab }, effects: [] });
    act(() => store.hydrate({ file_organization: {} }));
    card();
    const row = (id: string) => input(id).closest<HTMLElement>('[data-row]')!;
    fireEvent.change(input('template-album-path'), { target: { value: '$album' } });
    store.set('ui_appearance', 'sidebar_visualizer', 'wave');
    // it still holds every save back, as the old page refused its whole save
    expect(await store.flush()).toBe(false);
    expect(posted).toEqual([]);
    expect(host.templateErrors()).toEqual([
      'Album template must include at least one folder (use / separator)',
    ]);
    // ...but now says so on the field, and the save status names the template
    expect(input('template-album-path').getAttribute('aria-invalid')).toBe('true');
    expect(row('template-album-path').querySelector('[role="alert"]')!.textContent).toBe(
      'Album template must include at least one folder (use / separator)',
    );
    expect(host.blockers()).toEqual([
      {
        message:
          'The album path template would not work: Album template must include at least one folder (use / separator).',
      },
    ]);
    // two bad templates are two things to fix, each on its own field
    fireEvent.change(input('template-podcast-path'), { target: { value: '$Show' } });
    expect(host.blockers()).toHaveLength(2);
    expect(row('template-podcast-path').querySelector('[role="alert"]')!.textContent).toMatch(
      /should be lowercase/,
    );
    fireEvent.change(input('template-podcast-path'), { target: { value: '$show' } });
    // the playlist file naming rule holds the saves back too
    fireEvent.change(input('template-album-path'), { target: { value: '$albumartist/$album' } });
    expect(row('template-album-path').querySelector('[role="alert"]')).toBeNull();
    fireEvent.change(input('template-playlist-item'), { target: { value: '$artist' } });
    expect(await store.flush()).toBe(false);
    expect(host.playlistItemError()).toMatch(/must include \$title/);
    expect(row('template-playlist-item').querySelector('[role="alert"]')!.textContent).toMatch(
      /must include \$title/,
    );
    fireEvent.change(input('template-playlist-item'), { target: { value: '' } });
    expect(await store.flush()).toBe(true);
    expect(saved()).toMatchObject({
      file_organization: {
        'templates.album_path': '$albumartist/$album',
        'templates.playlist_item': '',
      },
      ui_appearance: { sidebar_visualizer: 'wave' },
    });
  });

  it('the old playlist path template shows only when it is what holds the save back', () => {
    act(() => store.hydrate({ file_organization: {} }));
    card();
    const row = () => input('template-playlist-path').closest<HTMLElement>('[data-row]')!;
    expect(row().hidden).toBe(true);
    act(() => store.hydrate({ file_organization: { templates: { playlist_path: '$playlist' } } }));
    expect(row().hidden).toBe(false);
    expect(row().textContent).toContain('no longer used');
  });

  it('the hero reads the album template back; templates off says so', async () => {
    act(() => store.hydrate({ file_organization: {} }));
    card();
    await waitFor(() =>
      expect(document.body.textContent).toContain(
        'An album track is saved as Daft Punk / Daft Punk - Discovery / 01 - One More Time.flac',
      ),
    );
    fireEvent.click(input('file-organization-enabled'));
    expect(document.body.textContent).toContain(
      "Custom templates are off, so files keep SoulSync's built-in layout.",
    );
  });

  it('Reset asks first, then writes every default, the audiobook template included', async () => {
    act(() =>
      store.hydrate({
        file_organization: {
          templates: { album_path: 'x/y', compilation_path: 'C/$album', playlist_item: '$title' },
        },
      }),
    );
    card();
    let answerIs = false;
    const confirm = vi.fn(() => Promise.resolve(answerIs));
    shell.showConfirmDialog = confirm;
    fireEvent.change(input('template-audiobook-path'), { target: { value: 'mine' } });
    fireEvent.change(input('template-album-path'), { target: { value: 'typed/$title' } });
    // kept: nothing changes
    await act(async () => {
      fireEvent.click(document.getElementById('file-org-reset')!);
    });
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(input('template-album-path').value).toBe('typed/$title');
    answerIs = true;
    await act(async () => {
      fireEvent.click(document.getElementById('file-org-reset')!);
    });
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
  /** what is drawn under a field: its "Saves as" folders and file, or a note */
  const slot = (id: string) => input(id).closest<HTMLElement>('[data-row]')!;
  const crumbs = (id: string) =>
    [...slot(id).querySelectorAll('[data-template-preview] > span:not([aria-hidden])')].map(
      (el) => el.textContent,
    );

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
    // the folders, then the file
    await waitFor(() =>
      expect(crumbs('template-album-path')).toEqual([
        'Daft Punk',
        'Daft Punk - Discovery',
        '01 - One More Time.flac',
      ]),
    );
    // a single the source calls one gets the album-template line under it
    expect(slot('template-single-path').textContent).toContain(
      'A release the source calls a single uses the album template until you change this one',
    );
    expect(document.querySelector('[data-disc-preview]')!.textContent).toBe(
      'Disc 2 of a two-disc album is saved in Daft Punk / Daft Punk - Discovery / Disc 2 /',
    );
    expect(slot('template-compilation-path').textContent).toContain('bad template');
    expect(slot('template-video-path').textContent).toContain('Nothing to preview');

    fireEvent.change(input('template-album-path'), { target: { value: '$album/$title' } });
    await waitFor(() => expect(previews).toHaveLength(2));
    expect((previews[1].templates as Record<string, string>).album_path).toBe('$album/$title');
  });

  it('the example is remembered and asked for; organizing off hides it all', async () => {
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
    expect(document.getElementById('path-preview-scenarios-row')!.hidden).toBe(true);
    expect(slot('template-album-path').querySelector('[data-template-preview]')).toBeNull();
    await new Promise((done) => setTimeout(done, 20));
    expect(previews).toHaveLength(2);
  });

  it('says when the preview cannot be had', async () => {
    answer = () => ({ success: false, error: 'boom' });
    act(() => store.hydrate({ file_organization: {} }));
    card();
    await waitFor(() =>
      expect(slot('template-sample-path').textContent).toContain('Preview unavailable: boom'),
    );
  });
});
