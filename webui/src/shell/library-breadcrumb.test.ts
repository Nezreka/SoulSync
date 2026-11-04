/**
 * The sidebar's "Library / <Artist>". Every artist opens on the Library page
 * now (the one artist page), and the breadcrumb only knew the old
 * artist-detail page, so it stopped showing the artist.
 */

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { _updateSidebarLibraryBreadcrumb, setLibraryArtistBreadcrumb } from './library-globals';

const text = () => document.querySelector('[data-page="library"] .nav-text')?.textContent;

beforeEach(() => {
  document.body.innerHTML =
    '<button data-page="library"><span class="nav-text">Library</span></button>';
  (globalThis as { currentPage?: string }).currentPage = 'library';
});

afterEach(() => {
  setLibraryArtistBreadcrumb(null);
});

describe('the Library breadcrumb', () => {
  it('names the artist the Library page shows', () => {
    setLibraryArtistBreadcrumb('Tycho');
    expect(text()).toBe('Library / Tycho');
    expect(document.querySelector('.nav-text-context')?.getAttribute('title')).toBe('Tycho');
  });

  it('goes back to plain Library once no artist is shown', () => {
    setLibraryArtistBreadcrumb('Tycho');
    setLibraryArtistBreadcrumb(null);
    expect(text()).toBe('Library');
  });

  it('shortens a long name and keeps it whole on hover', () => {
    setLibraryArtistBreadcrumb('Godspeed You! Black Emperor');
    expect(text()).toBe('Library / Godspeed You!…');
  });

  it('says nothing about an artist on another page', () => {
    setLibraryArtistBreadcrumb('Tycho');
    (globalThis as { currentPage?: string }).currentPage = 'search';
    _updateSidebarLibraryBreadcrumb();
    expect(text()).toBe('Library');
  });
});
