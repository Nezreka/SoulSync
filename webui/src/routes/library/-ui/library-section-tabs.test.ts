import { describe, expect, it } from 'vitest';

import { librarySectionSearch } from './library-v2-page';

/** UI-02: switching section has to reset paging.
 *
 * Artists and Wanted page independently, and only the Wanted button reset the
 * page number. So "Wanted page 2 → Artists" asked for page 2 of a one-page
 * artist list: the API returned no rows for a library that has twelve artists,
 * the empty state read "Your library is empty / Import library", and the
 * pagination that would have led back is hidden when there is only one page.
 * Reloading the same URL reproduced it exactly; the user had to change a filter
 * or edit the URL by hand.
 */
describe('library section switch', () => {
  it('resets paging when leaving Wanted for Artists', () => {
    expect(librarySectionSearch({ section: 'wanted', page: 2, sort: 'name' }, 'artists')).toEqual({
      section: 'artists',
      page: 1,
      sort: 'name',
      q: '',
      artist: undefined,
      album: undefined,
    });
  });

  it('resets paging when leaving Artists for Wanted', () => {
    expect(librarySectionSearch({ section: 'artists', page: 5 }, 'wanted')).toMatchObject({
      section: 'wanted',
      page: 1,
    });
  });

  it('keeps every other search parameter', () => {
    expect(
      librarySectionSearch(
        { section: 'wanted', page: 3, sort: 'added', monitored: true },
        'artists',
      ),
    ).toMatchObject({ sort: 'added', monitored: true });
  });

  it('clears the drill-down the previous section was in', () => {
    expect(
      librarySectionSearch({ section: 'artists', artist: 42, album: 7, q: 'muse' }, 'wanted'),
    ).toMatchObject({ artist: undefined, album: undefined, q: '' });
  });
});
