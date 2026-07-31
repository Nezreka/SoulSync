import type { EnhancedData } from './-artist-detail.enhanced';

/**
 * Keeps the vanilla artistDetailPageState in step with the React page.
 *
 * This is not bookkeeping — it is load-bearing. A dozen functions the React
 * page invokes through window read this object rather than taking arguments:
 *
 *   playArtistRadio / openArtistArtPicker / openDiscographyModal
 *       read currentArtistId + currentArtistName. Without them they bail out
 *       with "No artist selected" and the button does nothing at all.
 *   deleteLibraryAlbum / deleteLibraryTrack / runEnrichment / showReorganizeModal
 *   / _showMobileTrackActions / showTrackRedownloadModal /
 *   openMissingTrackManageModal / openManualMatchModal / redownloadLibraryAlbum
 *       read enhancedData for the artist name and to patch their own copy of
 *       the album list, and selectedTracks to drop ids they just deleted.
 *
 * library.js exports the object onto window for exactly this reason: a
 * top-level `let` in a classic script is a global LEXICAL binding that no
 * module can reach.
 */
export interface VanillaArtistState {
  currentArtistId: unknown;
  currentArtistName: string | null;
  currentArtistSource: string | null;
  enhancedData: EnhancedData | null;
  selectedTracks: Set<string>;
  [key: string]: unknown;
}

function vanillaState(): VanillaArtistState | null {
  return (window as { artistDetailPageState?: VanillaArtistState }).artistDetailPageState ?? null;
}

/**
 * Which artist the page is showing.
 *
 * Mirrors what populateArtistDetailPage set, including the library-upgrade
 * case: when the backend resolves a source-artist click to an existing library
 * record, the id it hands back is the library primary key, and the library-only
 * endpoints need THAT id rather than the one in the URL.
 */
export function syncVanillaArtist(patch: {
  id?: unknown;
  name?: string | null;
  source?: string | null;
}): void {
  const state = vanillaState();
  if (!state) return;
  if ('id' in patch) state.currentArtistId = patch.id ?? null;
  if ('name' in patch) state.currentArtistName = patch.name ?? null;
  if ('source' in patch) state.currentArtistSource = patch.source ?? null;
}

/** The vanilla's own teardown (clearArtistDetailPageState), for unmount. */
export function clearVanillaArtist(): void {
  syncVanillaArtist({ id: null, name: null, source: null });
}

/** The Enhanced payload, so album/track actions can read and patch it. */
export function syncVanillaEnhancedData(data: EnhancedData | null): void {
  const state = vanillaState();
  if (state) state.enhancedData = data;
}

/**
 * The ticked track ids.
 *
 * The Set is MUTATED rather than replaced: the vanilla deletes from it after a
 * track delete, and swapping the object out would leave those writes landing on
 * a Set nobody reads.
 */
export function syncVanillaSelection(selected: Set<string>): void {
  const state = vanillaState();
  if (!state?.selectedTracks) return;
  state.selectedTracks.clear();
  for (const id of selected) state.selectedTracks.add(id);
}
