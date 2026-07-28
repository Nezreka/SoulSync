/**
 * The Back button's label, from _updateArtistDetailBackButtonLabel
 * (library.js:464).
 *
 * The vanilla kept a stack of where you came from — a page id, or the name of
 * the previous artist when you chained artist → similar artist → artist — and
 * labelled the button from its top entry. Arrivals from a still-vanilla page
 * (search, label detail, enrichment) go through navigateToArtistDetail, which
 * pushes onto that stack, so React reads the SAME array rather than guessing.
 */

export interface BackLabelEntry {
  type: 'page' | 'artist';
  pageId?: string;
  name?: string;
}

/** Mirrors _ARTIST_DETAIL_BACK_LABELS; unknown pages fall back to Library. */
const FALLBACK_LABELS: Record<string, string> = {
  library: 'Back to Library',
  search: 'Back to Search',
  discover: 'Back to Discover',
  watchlist: 'Back to Watchlist',
  wishlist: 'Back to Wishlist',
  stats: 'Back to Stats',
  'playlist-explorer': 'Back to Explorer',
  automations: 'Back to Automations',
  dashboard: 'Back to Dashboard',
  sync: 'Back to Sync',
  'active-downloads': 'Back to Downloads',
};

function labels(): Record<string, string> {
  return (
    (window as { artistDetailBackLabels?: Record<string, string> }).artistDetailBackLabels ??
    FALLBACK_LABELS
  );
}

export function backLabelStack(): BackLabelEntry[] {
  return (window as { artistDetailLabelStack?: BackLabelEntry[] }).artistDetailLabelStack ?? [];
}

/**
 * "← Back to Search" / "← Back to Aphex Twin" / "← Back".
 *
 * An empty stack gives the plain "← Back", which is what the vanilla showed on
 * a cold load straight onto an artist url.
 */
export function backButtonLabel(stack: BackLabelEntry[] = backLabelStack()): string {
  const top = stack[stack.length - 1];
  if (!top) return '← Back';
  if (top.type === 'artist') return `← Back to ${top.name}`;
  return `← ${labels()[top.pageId ?? ''] ?? labels().library ?? FALLBACK_LABELS.library}`;
}

/**
 * Mirrors the vanilla's _artistDetailGoingBack.
 *
 * Going back re-renders the page with the PREVIOUS artist, which looks exactly
 * like a forward hop to the effect below. Without this flag the pop and the
 * push cancel out and the label never changes.
 */
let goingBack = false;

export function markGoingBack(): void {
  goingBack = true;
}

/**
 * Record an artist → artist hop, as navigateToArtistDetail did for the vanilla.
 *
 * Similar-artist bubbles are plain links, and with artist-detail React-owned
 * they route through TanStack without ever reaching navigateToArtistDetail — so
 * nothing else pushes for these hops, and after three of them the button would
 * still be offering the page you originally came from.
 */
export function pushArtistOrigin(previousArtistName: string | null | undefined): void {
  if (goingBack) {
    goingBack = false;
    return;
  }
  if (!previousArtistName) return;
  backLabelStack().push({ type: 'artist', name: previousArtistName });
}

/** Back navigation pops, so the label follows you back down the chain. */
export function popBackOrigin(): BackLabelEntry | undefined {
  markGoingBack();
  return backLabelStack().pop();
}

/** Test seam: the flag is module state and has to be resettable. */
export function resetGoingBackForTests(): void {
  goingBack = false;
}
