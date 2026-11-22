/**
 * The Library tab (settings-bic phase 4): Folders, Naming, Tags and artwork,
 * Import and files, and Subtitles (the video side's), each with a kind
 * switch for only the kinds it has settings for, opening on the side the page
 * is on, the kind in the address; every control in exactly one place, and
 * nothing of one kind showing under another. The two old media cards' tab
 * strips (Folders and Organization, both reset to Music) are gone.
 */

import { act, cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { nav } from '@/features/settings/nav';
import { reveal } from '@/features/settings/search-entries';
import { createSettingsStore } from '@/features/settings/settings-store';
import { LibraryPage, kindFor } from '@/features/settings/tabs/library/library-page';

beforeEach(() => {
  nav.reset();
  vi.stubGlobal(
    'fetch',
    vi.fn(() => Promise.resolve(new Response(JSON.stringify({ success: true })))),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  document.body.removeAttribute('data-side');
});

function page() {
  const store = createSettingsStore({ post: () => Promise.resolve({ success: true }), delay: 0 });
  return render(
    <div id="settings-page">
      <LibraryPage store={store} opened={1} active />
    </div>,
  );
}
const view = (key: string) =>
  document.querySelector<HTMLElement>(`[data-stg-tab="library"][data-stg-view="${key}"]`)!;
const kinds = (key: string) =>
  [...document.querySelectorAll(`#stg-library-${key}-kind [role="tab"]`)].map((t) => t.textContent);
/** the kind panel a control is drawn in: "<pill>/<kind>" */
const home = (id: string) => {
  const el = document.getElementById(id)!;
  const kind = el.closest<HTMLElement>('[data-kind]');
  return `${el.closest<HTMLElement>('[data-stg-view]')!.dataset.stgView}/${kind?.dataset.kind ?? '-'}`;
};

describe('the Library page', () => {
  it('each pill switches only the kinds it has settings for', () => {
    act(() => nav.go('library', 'folders'));
    page();
    expect(kinds('folders')).toEqual(['Music', 'Audiobooks', 'Podcasts', 'Video']);
    expect(kinds('naming')).toEqual(['Music', 'Audiobooks', 'Podcasts', 'Video']);
    expect(kinds('tags')).toEqual(['Music', 'Audiobooks', 'Video']);
    expect(kinds('import')).toEqual(['Music', 'Audiobooks', 'Podcasts', 'Video']);
    // the subtitles are the video side's own: no kinds
    expect(document.getElementById('stg-library-subtitles-kind')).toBeNull();
  });

  it('every control is drawn once, on its pill and kind', () => {
    page();
    for (const [id, where] of [
      ['download-path', 'folders/music'],
      ['transfer-path', 'folders/music'],
      ['min-free-disk-gb', 'folders/music'],
      ['music-paths-list', 'folders/music'],
      ['audiobooks-path', 'folders/audiobooks'],
      ['audiobook-routes-list', 'folders/audiobooks'],
      ['podcasts-path', 'folders/podcasts'],
      ['video-download-path', 'folders/video'],
      ['vo-min-free', 'folders/video'],
      ['video-libraries-list', 'folders/video'],
      ['template-album-path', 'naming/music'],
      ['disc-label', 'naming/music'],
      ['reorganize-preserve-casing', 'naming/music'],
      ['template-audiobook-path', 'naming/audiobooks'],
      ['template-podcast-path', 'naming/podcasts'],
      ['vo-movie-template', 'naming/video'],
      ['vo-colon-replacement', 'naming/video'],
      ['metadata-enabled', 'tags/music'],
      ['art-source-list', 'tags/music'],
      ['artist-separator', 'tags/music'],
      ['embed-musicbrainz', 'tags/music'],
      ['genre-whitelist-enabled', 'tags/music'],
      ['audiobook-embed-metadata', 'tags/audiobooks'],
      ['vo-nfo', 'tags/video'],
      ['import-default-search-source', 'import/music'],
      ['redownload-externally-deleted', 'import/music'],
      ['audiobook-completeness-tolerance', 'import/audiobooks'],
      ['audiobook-recycle-deletes', 'import/audiobooks'],
      ['podcast-media-format', 'import/podcasts'],
      ['vo-transfer-mode', 'import/video'],
      ['vo-recycle', 'import/video'],
      ['vo-subs-dl', 'subtitles/-'],
      ['subchain-widget', 'subtitles/-'],
      ['vo-whisper-model', 'subtitles/-'],
    ]) {
      expect(document.querySelectorAll(`#${id}`), id).toHaveLength(1);
      expect(home(id), id).toBe(where);
    }
    // moved off Library (DESIGN 3.3): drawn by their new tabs
    for (const id of [
      'audiobook-marketplace',
      'allow-duplicate-tracks',
      'vo-yt-follow-count',
      'vo-sponsorblock',
      'video-monitor-specials',
      'video-billboard-autoplay',
      'm3u-relative-paths',
      'audiobook-download-mode',
    ])
      expect(document.getElementById(id), id).toBeNull();
  });

  it('a pill opens on the side the page is on; a kind picked is in the address', () => {
    act(() => nav.go('library', 'naming'));
    page();
    const panel = (kind: string) => document.getElementById(`stg-library-naming-${kind}`)!;
    expect(panel('music').hidden).toBe(false);
    for (const kind of ['audiobooks', 'podcasts', 'video']) expect(panel(kind).hidden).toBe(true);
    fireEvent.click(document.querySelector('#stg-library-naming-kind [data-value="podcasts"]')!);
    expect(nav.get().kind).toBe('podcasts');
    expect(panel('podcasts').hidden).toBe(false);
    expect(panel('music').hidden).toBe(true);
    // another pill starts from the side again
    act(() => nav.view('folders'));
    expect(document.getElementById('stg-library-folders-music')!.hidden).toBe(false);
    expect(kindFor('folders', null, 'video')).toBe('video');
    expect(kindFor('tags', 'podcasts', 'music')).toBe('music');
    expect(kindFor('import', 'audiobooks', 'video')).toBe('audiobooks');
  });

  it('the video side opens each pill on Video', () => {
    document.body.setAttribute('data-side', 'video');
    act(() => nav.go('library', 'tags'));
    page();
    expect(document.getElementById('stg-library-tags-video')!.hidden).toBe(false);
    expect(document.getElementById('stg-library-tags-music')!.hidden).toBe(true);
    expect(view('tags').querySelector('#stg-library-tags-kind')!.getAttribute('data-value')).toBe(
      'video',
    );
  });

  it('the search lands on a field of another kind by switching to it', () => {
    act(() => nav.go('library', 'folders'));
    page();
    act(() => reveal(document.getElementById('podcasts-path')!));
    expect(nav.get().kind).toBe('podcasts');
    expect(document.getElementById('stg-library-folders-podcasts')!.hidden).toBe(false);
  });
});
