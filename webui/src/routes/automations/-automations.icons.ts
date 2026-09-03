/**
 * Trigger/action emoji, lifted verbatim from _autoIcons in stats-automations.js.
 *
 * Extracted by evaluating the original object literal rather than retyping it —
 * the values are surrogate-pair emoji and hand-copying them silently mangles
 * variation selectors (the U+FE0F suffixes below).
 */
export const AUTOMATION_ICONS: Record<string, string> = {
  schedule: '⏱️',
  daily_time: '🕰️',
  weekly_time: '📅',
  app_started: '🚀',
  track_downloaded: '⬇️',
  batch_complete: '✅',
  watchlist_new_release: '🔔',
  playlist_synced: '🔄',
  playlist_changed: '✏️',
  process_wishlist: '📋',
  scan_watchlist: '👁️',
  scan_library: '🔄',
  refresh_mirrored: '📂',
  sync_playlist: '🔁',
  discover_playlist: '🔍',
  discovery_completed: '🔍',
  notify_only: '🔔',
  discord_webhook: '💬',
  ntfy: '📡',
  gotify: '📬',
  pushbullet: '🔔',
  telegram: '✉️',
  webhook: '🌐',
  signal_received: '⚡',
  fire_signal: '⚡',
  run_script: '💻',
  wishlist_processing_completed: '✅',
  watchlist_scan_completed: '✅',
  database_update_completed: '🗄️',
  download_failed: '❌',
  download_quarantined: '⚠️',
  wishlist_item_added: '➕',
  watchlist_artist_added: '👤',
  watchlist_artist_removed: '👤',
  import_completed: '📥',
  mirrored_playlist_created: '📂',
  quality_scan_completed: '📊',
  duplicate_scan_completed: '🗂️',
  library_scan_completed: '📡',
  start_database_update: '🗄️',
  start_database_update_hourly: '🗄️',
  run_duplicate_cleaner: '🗂️',
  clear_quarantine: '🗑️',
  cleanup_wishlist: '🧹',
  update_discovery_pool: '🧭',
  start_quality_scan: '📊',
  backup_database: '💾',
  refresh_beatport_cache: '🎵',
  clean_search_history: '🗑️',
  clean_completed_downloads: '✅',
  full_cleanup: '🧹',
  playlist_pipeline: '🚀',
  video_scan_library: '🎬',
  video_scan_server: '🔄',
  video_update_database: '🗄️',
  video_update_database_hourly: '🗄️',
  video_add_airing_episodes: '📺',
  video_deep_scan_movies: '🎬',
  video_deep_scan_tv: '📺',
  video_scan_watchlist_people: '🎭',
  video_scan_watchlist_channels: '📡',
  video_scan_watchlist_playlists: '🎵',
  video_scan_watchlist_studios: '🎬',
  video_process_movie_wishlist: '🎬',
  video_process_episode_wishlist: '📺',
  video_process_youtube_wishlist: '⬇️',
  video_refresh_airing_schedules: '🗓️',
  video_clean_youtube_episodes: '🧹',
  video_reenrich_stale: '🔄',
  video_clean_search_history: '🗑️',
  video_clean_completed_downloads: '✅',
  video_full_cleanup: '🧹',
  // added with the ext.to fresh releases automation (aug 24) - the vanilla
  // stats-automations.js map got it, this twin was missed
  video_extto_fresh_refresh: '✨',
  video_backup_database: '💾',
  video_apply_overlays: '🎨',
  video_clean_plex_images: '🖼️',
  video_sync_collections: '🗂️',
  video_rss_sync: '📡',
  video_seeding_sweep: '🌱',
  video_purge_recycle_bin: '🗑️',
  video_import_lists: '📥',
  monthly_time: '📅',
  video_batch_complete: '✅',
  video_library_scan_completed: '📡',
  video_download_completed: '⬇️',
  video_download_failed: '❌',
  video_import_failed: '⚠️',
  video_upgrade_completed: '📈',
  video_repair_finding_created: '🔧',
  video_repair_scan_completed: '🔧',
  video_wishlist_item_added: '➕',
  video_watchlist_added: '👁️',
  video_watchlist_removed: '🚫',
  video_collections_synced: '🗂️',
  video_overlays_applied: '🎨',
  video_database_update_completed: '🗄️',
  video_run_repair_job: '🔧',
};

/** Anything unmapped falls back to the gear, as the vanilla card did. */
export const AUTOMATION_ICON_FALLBACK = '⚙️';

export function automationIcon(type: string | null | undefined): string {
  return AUTOMATION_ICONS[type ?? ''] ?? AUTOMATION_ICON_FALLBACK;
}

/** Notification/then-action label. Two carry their own icon inline. */
export function formatNotify(type: string | null | undefined): string {
  if (type === 'discord_webhook') return 'Discord';
  if (type === 'ntfy') return 'ntfy';
  if (type === 'gotify') return 'Gotify';
  if (type === 'pushbullet') return 'Pushbullet';
  if (type === 'telegram') return 'Telegram';
  if (type === 'webhook') return 'Webhook';
  if (type === 'fire_signal') return '⚡ Signal';
  if (type === 'run_script') return '💻 Script';
  return type || '';
}
