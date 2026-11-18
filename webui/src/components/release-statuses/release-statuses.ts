/**
 * #2175: MusicBrainz release statuses a watchlist lets through.
 *
 * Library wide in the watchlist defaults, with an override per artist (null
 * follows the library). Only MusicBrainz has statuses: a release from any
 * other source, or with no status, always gets through. The server is the
 * source of truth (core/release_statuses.py); these mirror its order.
 */

export const RELEASE_STATUSES = [
  'official',
  'promotion',
  'bootleg',
  'pseudo-release',
  'withdrawn',
  'expunged',
  'cancelled',
] as const;

export type ReleaseStatus = (typeof RELEASE_STATUSES)[number];

export const RELEASE_STATUS_COPY: Record<ReleaseStatus, { label: string; hint: string }> = {
  official: { label: 'Official', hint: 'Released by the artist or their label' },
  promotion: { label: 'Promotion', hint: 'Promos and giveaways' },
  bootleg: { label: 'Bootleg', hint: 'Unofficial releases' },
  'pseudo-release': {
    label: 'Pseudo-Release',
    hint: 'Translated or transliterated track listings',
  },
  withdrawn: { label: 'Withdrawn', hint: 'Released, then pulled' },
  expunged: { label: 'Expunged', hint: 'Withdrawn and disowned by the artist' },
  cancelled: { label: 'Cancelled', hint: 'Announced but never released' },
};

/** A server list as known statuses, in MusicBrainz's order. Nothing usable
 *  reads as every status, which is what the server does too. */
export function normalizeStatuses(value: unknown): ReleaseStatus[] {
  if (!Array.isArray(value)) return [...RELEASE_STATUSES];
  const wanted = new Set(value.map((item) => String(item).trim().toLowerCase()));
  const statuses = RELEASE_STATUSES.filter((status) => wanted.has(status));
  return statuses.length > 0 ? statuses : [...RELEASE_STATUSES];
}

/** An artist's own statuses, or null when it follows the library. */
export function normalizeOverride(value: unknown): ReleaseStatus[] | null {
  if (!Array.isArray(value) || value.length === 0) return null;
  return normalizeStatuses(value);
}

export function toggleStatus(
  current: readonly ReleaseStatus[],
  status: ReleaseStatus,
  on: boolean,
): ReleaseStatus[] {
  const next = new Set(current);
  if (on) next.add(status);
  else next.delete(status);
  return RELEASE_STATUSES.filter((value) => next.has(value));
}

export function allStatuses(statuses: readonly ReleaseStatus[]): boolean {
  return RELEASE_STATUSES.every((status) => statuses.includes(status));
}

/** "every status" or "Official, Promotion": the short form a hint uses. */
export function describeStatuses(statuses: readonly ReleaseStatus[]): string {
  if (allStatuses(statuses)) return 'every status';
  return statuses.map((status) => RELEASE_STATUS_COPY[status].label).join(', ');
}
