/**
 * The sync page's shell — the tab table and the small pure decisions the
 * header and tab strip make. Transcribed from index.html 2249-2295 and the
 * tab handler at sync-services.js 3694-3811.
 */

export type SyncTabId =
  | 'server'
  | 'spotify'
  | 'spotify-public'
  | 'itunes-link'
  | 'tidal'
  | 'qobuz'
  | 'deezer'
  | 'deezer-link'
  | 'youtube'
  | 'beatport'
  | 'listenbrainz-sync'
  | 'lastfm-sync'
  | 'soulsync-discovery-sync'
  | 'import-file'
  | 'mirrored';

export interface SyncTab {
  id: SyncTabId;
  /** The visible label AND the title attribute — the vanilla uses one string
   *  for both on all fifteen (2250-2295). */
  label: string;
  /** The `tab-icon <x>-icon` sprite class. Two tabs share a sprite: the
   *  Spotify pair and the Deezer pair, since the link variants are the same
   *  service. */
  icon: string;
  /**
   * `data-link="true"` in the markup. It marks the three URL-import tabs —
   * paste a link rather than browse an account — and nothing in the vanilla
   * JS reads it; it exists for CSS. Carried because dropping an attribute the
   * stylesheet may key off is exactly the kind of silent visual regression
   * the artefact check cannot see.
   */
  link?: true;
}

/**
 * All fifteen, in the order the strip renders them. `server` is first and is
 * the default; a divider follows it (index.html 2253), separating "your media
 * server" from the fifteen sources.
 */
export const SYNC_TABS: readonly SyncTab[] = [
  { id: 'server', label: 'Server Playlists', icon: 'server-icon' },
  { id: 'spotify', label: 'Spotify', icon: 'spotify-icon' },
  { id: 'spotify-public', label: 'Spotify Link', icon: 'spotify-icon', link: true },
  { id: 'itunes-link', label: 'iTunes Link', icon: 'itunes-icon', link: true },
  { id: 'tidal', label: 'Tidal', icon: 'tidal-icon' },
  { id: 'qobuz', label: 'Qobuz', icon: 'qobuz-icon' },
  { id: 'deezer', label: 'Deezer', icon: 'deezer-icon' },
  { id: 'deezer-link', label: 'Deezer Link', icon: 'deezer-icon', link: true },
  { id: 'youtube', label: 'YouTube', icon: 'youtube-icon' },
  { id: 'beatport', label: 'Beatport', icon: 'beatport-icon' },
  // 3760-3763: the id is `listenbrainz-sync`, NOT `listenbrainz`, because the
  // vanilla resolves panels by `${tabId}-tab-content` and the DISCOVER page
  // already owns `#listenbrainz-tab-content`. React scopes its own DOM so the
  // collision cannot recur, but the id is load-bearing for the CSS and for
  // anyone reading both codebases, so it stays.
  { id: 'listenbrainz-sync', label: 'ListenBrainz', icon: 'listenbrainz-icon' },
  { id: 'lastfm-sync', label: 'Last.fm', icon: 'lastfm-icon' },
  { id: 'soulsync-discovery-sync', label: 'SoulSync Discovery', icon: 'soulsync-discovery-icon' },
  { id: 'import-file', label: 'Import', icon: 'import-file-icon' },
  { id: 'mirrored', label: 'Mirrored', icon: 'mirrored-icon' },
];

/** The tab the page opens on — `active` in the markup at 2250. */
export const SYNC_DEFAULT_TAB: SyncTabId = 'server';

const IDS = new Set<string>(SYNC_TABS.map((t) => t.id));

/**
 * The vanilla has no equivalent: it reads `button.dataset.tab` and trusts it,
 * then does an UNGUARDED `getElementById(`${tabId}-tab-content`).classList`
 * (3714). A tab whose pane was missing would throw mid-handler and leave the
 * strip half-updated — active class moved, no panel shown. All fifteen resolve
 * today (checked), but the port takes an unknown id back to the default rather
 * than rendering nothing.
 */
export function normalizeSyncTab(tab: string | null | undefined): SyncTabId {
  return IDS.has(tab as string) ? (tab as SyncTabId) : SYNC_DEFAULT_TAB;
}

export interface SyncHeaderAction {
  key: string;
  label: string;
  title: string;
}

/**
 * The four header buttons (index.html 2237-2241), in order. Three of them stay
 * VANILLA behind a `window.x?.()` seam — their targets live in
 * manual-library-match.js, wishlist-tools.js and origin-history.js, none of
 * which the flip touches. The fourth, Auto-Sync, is React now.
 */
export const SYNC_HEADER_ACTIONS: readonly SyncHeaderAction[] = [
  {
    key: 'auto-sync',
    label: 'Auto-Sync',
    title: 'Schedule mirrored playlists to refresh, discover, sync, and queue missing tracks',
  },
  {
    key: 'library-match',
    label: 'Library Match',
    title: 'Manually link source tracks to library tracks',
  },
  { key: 'sync-history', label: 'Sync History', title: 'View sync history' },
  {
    key: 'download-origins',
    label: 'Download Origins',
    title: 'See every track your playlist syncs downloaded',
  },
];
