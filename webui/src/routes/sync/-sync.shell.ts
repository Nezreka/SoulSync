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

/**
 * The tab the page opens on.
 *
 * Mirrored, not Server Playlists. Mirrored is the persistent local record of
 * every playlist regardless of where it came from — the only surface that
 * holds sync state for all of them — and it sat at position fifteen, a peer of
 * the fourteen inputs that feed it. Landing there makes the page a library of
 * playlists rather than a directory of services.
 */
export const SYNC_DEFAULT_TAB: SyncTabId = 'mirrored';

/**
 * The tabs that earn a permanent place in the strip.
 *
 * The other twelve are now reached through Add playlist, which detects the
 * service from the link rather than making you pick first. They are NOT gone —
 * every one still exists as a panel and is opened by routing — they simply do
 * not each need a chip in a strip that had fifteen of them, six of which were
 * duplicates of one another.
 *
 * What is left is genuinely three different things:
 *   mirrored  the library — the page's subject
 *   server    the other direction: reading FROM Plex/Jellyfin/Navidrome
 *   beatport  a chart browser, a different page grafted into the strip
 */
export const SYNC_PRIMARY_TAB_IDS: readonly SyncTabId[] = ['mirrored', 'server', 'beatport'];

/**
 * Which chips to render, given what is open.
 *
 * A routed tab appears in the strip WHILE IT IS ACTIVE and drops out when you
 * leave it. Without that, opening Spotify Link from the sheet would leave the
 * strip highlighting nothing and no way back — a panel with no chip is a room
 * with no door. It is appended rather than inserted in place so the three
 * permanent chips never move under the cursor.
 */
export function syncStripTabs(active: string): readonly SyncTab[] {
  // Ordered by SYNC_PRIMARY_TAB_IDS, not by SYNC_TABS: a filter would hand
  // them back in the old fifteen-tab order (server, beatport, mirrored) and
  // quietly put the library last again, which is the whole thing being fixed.
  const primary = SYNC_PRIMARY_TAB_IDS.map(
    (id) => SYNC_TABS.find((t) => t.id === id) as SyncTab,
  ).filter(Boolean);
  if (SYNC_PRIMARY_TAB_IDS.includes(active as SyncTabId)) return primary;
  const routed = SYNC_TABS.find((t) => t.id === active);
  return routed ? [...primary, routed] : primary;
}

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
 * The header buttons, in order.
 *
 * Most stay VANILLA behind a `window.x?.()` seam — their targets live in
 * manual-library-match.js, wishlist-tools.js, origin-history.js and the pool
 * modals, none of which the flip touches. Auto-Sync is React.
 *
 * Discovery Pool and Wing It Pool moved UP from the Mirrored tab's own header.
 * They were never about one tab: both are app-level overlays reviewing matched
 * and best-effort-guessed tracks across everything, and the Tools page opens
 * the Discovery Pool through the same seam. Sitting inside one tab's header
 * made them look like that tab's controls.
 */
export const SYNC_HEADER_ACTIONS: readonly SyncHeaderAction[] = [
  // Auto-Sync sits FIRST so it lands beside Add playlist. The two of them are
  // the only header buttons that change what the page will do; everything after
  // is a "what happened" surface, and grouping them by that split is more
  // useful than the order the vanilla happened to declare them in.
  {
    key: 'auto-sync',
    label: 'Auto-Sync',
    title: 'Schedule mirrored playlists to refresh, discover, sync, and queue missing tracks',
  },
  {
    key: 'discovery-pool',
    label: 'Discovery Pool',
    title: 'View matched and failed discovery tracks',
  },
  {
    key: 'wing-it-pool',
    label: 'Wing It Pool',
    title:
      'Review tracks Wing It auto-matched on a best-effort guess — verify or re-match them',
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
