import { createFileRoute } from '@tanstack/react-router';

import type { AudiobookSearchType } from './-audiobooks.types';

import { AudiobooksBrowsePage } from './-ui/audiobooks-page';

const SEARCH_TYPES: AudiobookSearchType[] = ['keywords', 'title', 'author', 'narrator'];

export interface AudiobooksSearch {
  /** Active query. Absent means the browse view rather than results. */
  q?: string;
  /** Which field the query searches. */
  type?: AudiobookSearchType;
  /**
   * Genre NAME for a single-genre view. Deliberately the name and not an id:
   * Audible's ids differ per storefront, so an id in a shared or bookmarked URL
   * would resolve to nothing the moment the server answered from another store.
   */
  genre?: string;
}

/**
 * Browse state lives in the URL.
 *
 * Without this, opening a book from a search result and pressing Back remounts
 * the browse page empty — the query and the genre are gone, and the listener has
 * to type it again. In the URL they survive Back, reload, and being sent to
 * someone else.
 */
export const Route = createFileRoute('/audiobooks/')({
  validateSearch: (search: Record<string, unknown>): AudiobooksSearch => {
    const q = typeof search.q === 'string' ? search.q.trim() : '';
    const rawType =
      typeof search.type === 'string' ? (search.type as AudiobookSearchType) : undefined;
    const genre = typeof search.genre === 'string' ? search.genre.trim() : '';
    return {
      ...(q ? { q } : {}),
      ...(rawType && SEARCH_TYPES.includes(rawType) ? { type: rawType } : {}),
      ...(genre ? { genre } : {}),
    };
  },
  component: AudiobooksBrowsePage,
});
