import type { LibraryV2AlbumSummary } from './-library-v2.types';

/**
 * One release, one card.
 *
 * The catalogue can hold a release as more than one row: a track of an album
 * filed in another folder is its own album on the media server, and a
 * provider's entry for a release sits beside the owned copy until the
 * duplicate repair folds it. The server says which row stands for the release
 * (`release_lead_id`, the copy with the most of it) and nothing is merged.
 *
 * A row from a server that does not send the field stands for itself.
 */
export function standsForItsRelease(entry: LibraryV2AlbumSummary): boolean {
  return entry.release_lead_id == null || entry.release_lead_id === entry.id;
}

/** A discography: each release once, as its best copy. */
export function releaseLeads(entries: LibraryV2AlbumSummary[]): LibraryV2AlbumSummary[] {
  return entries.filter(standsForItsRelease);
}

/**
 * What is on disk: every owned copy stays, each as itself. Only a provider's
 * entry standing behind another row goes, since it is a release you already
 * have, listed again as one you want.
 */
export function withoutShadowEntries(entries: LibraryV2AlbumSummary[]): LibraryV2AlbumSummary[] {
  return entries.filter((entry) => entry.origin !== 'discography' || standsForItsRelease(entry));
}

/** "2 copies" for a release held more than once, else nothing. */
export function copiesNote(entry: LibraryV2AlbumSummary): string {
  const copies = entry.release_copies ?? 0;
  return copies > 1 ? `${copies} copies` : '';
}
