/** video display rules shared by the movie, tv and youtube pages */

import type { CatalogMovie, CatalogShow, VideoFile } from '../-player.types';

export type VideoSort = 'added' | 'title' | 'year' | 'rating';

export const VIDEO_SORTS: { value: VideoSort; label: string }[] = [
  { value: 'added', label: 'Recently added' },
  { value: 'title', label: 'A to Z' },
  { value: 'year', label: 'Newest' },
  { value: 'rating', label: 'Top rated' },
];

type Sortable = Pick<CatalogMovie | CatalogShow, 'title' | 'year' | 'rating' | 'added_at'>;

/** titles sort without a leading "the" or "a", like a shelf */
export function sortKey(title: string): string {
  return title.replace(/^(the|a|an)\s+/i, '').toLocaleLowerCase();
}

export function sortTitles<T extends Sortable>(items: readonly T[], sort: VideoSort): T[] {
  const out = items.slice();
  switch (sort) {
    case 'title':
      return out.sort((a, b) => sortKey(a.title).localeCompare(sortKey(b.title)));
    case 'year':
      return out.sort(
        (a, b) => (b.year ?? 0) - (a.year ?? 0) || sortKey(a.title).localeCompare(sortKey(b.title)),
      );
    case 'rating':
      return out.sort((a, b) => (b.rating ?? 0) - (a.rating ?? 0));
    case 'added':
    default:
      return out.sort((a, b) =>
        (a.added_at ?? '') < (b.added_at ?? '')
          ? 1
          : (a.added_at ?? '') > (b.added_at ?? '')
            ? -1
            : 0,
      );
  }
}

/** every genre in the list, most common first */
export function genresOf(items: readonly { genres: string[] }[]): string[] {
  const counts = new Map<string, number>();
  for (const item of items) for (const g of item.genres) counts.set(g, (counts.get(g) ?? 0) + 1);
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([g]) => g);
}

function channels(n: number | null | undefined): string {
  if (!n) return '';
  if (n === 6) return '5.1';
  if (n === 8) return '7.1';
  if (n === 2) return 'Stereo';
  if (n === 1) return 'Mono';
  return `${n}ch`;
}

/** 4K for 2160, and a p on a bare number: "720" reads as 720p */
export function resolutionLabel(value: string | null | undefined): string {
  if (!value) return '';
  const n = Number.parseInt(value, 10);
  if (n >= 2000) return '4K';
  return /^\d+$/.test(value.trim()) ? `${value.trim()}p` : value;
}

/** "4K · HDR10 · HEVC" and "TrueHD Atmos 7.1" lines for a version */
export function versionLines(file: VideoFile): { name: string; detail: string } {
  const name = [
    file.resolution_label || resolutionLabel(file.resolution) || 'Unknown',
    file.dynamic_range || '',
    file.release_source ? file.release_source.replace(/-/g, ' ').toUpperCase() : '',
  ]
    .filter(Boolean)
    .join(' · ');
  const audio = [
    file.audio_codec?.toUpperCase(),
    file.atmos ? 'Atmos' : '',
    channels(file.audio_channels),
  ]
    .filter(Boolean)
    .join(' ');
  const size = file.size_bytes
    ? `${(file.size_bytes / 1e9).toFixed(file.size_bytes > 1e10 ? 0 : 1)} GB`
    : '';
  const detail = [file.video_codec?.toUpperCase(), audio, size].filter(Boolean).join(' · ');
  return { name, detail };
}

/** best copy first: the one that plays as is, then the sharpest */
export function rankVersions(files: readonly VideoFile[]): VideoFile[] {
  const verdictRank = { yes: 0, maybe: 1, no: 2 } as const;
  const resRank = (f: VideoFile) => Number.parseInt(f.resolution ?? '0', 10) || 0;
  return files
    .slice()
    .sort(
      (a, b) =>
        verdictRank[a.playable?.verdict ?? 'maybe'] - verdictRank[b.playable?.verdict ?? 'maybe'] ||
        resRank(b) - resRank(a),
    );
}

/** ?sort= and ?genre= on the movie and tv browse pages */
export interface BrowseSearch {
  sort?: VideoSort;
  genre?: string;
}

export function validateBrowseSearch(search: Record<string, unknown>): BrowseSearch {
  const out: BrowseSearch = {};
  if (
    search.sort === 'title' ||
    search.sort === 'year' ||
    search.sort === 'rating' ||
    search.sort === 'added'
  )
    out.sort = search.sort;
  if (typeof search.genre === 'string' && search.genre) out.genre = search.genre;
  return out;
}
