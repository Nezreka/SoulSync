/**
 * The "Copy Debug Info" report — the plain-text block users paste into GitHub
 * issues. Ported verbatim from the legacy docs.js click handler so the report
 * shape stays byte-compatible with what maintainers are used to triaging.
 * Pure (data in, text out) so it is unit-testable without a DOM.
 */

interface PathEntry {
  path: string;
  exists?: boolean;
}

export interface DebugInfoResponse {
  version?: string;
  os?: string;
  docker?: boolean;
  python?: string;
  ffmpeg?: string;
  runner?: string;
  uptime?: string;
  memory_usage?: string;
  system_memory?: string;
  cpu_percent?: string;
  thread_count?: number;
  services?: {
    music_source?: string;
    spotify_connected?: boolean;
    spotify_rate_limited?: boolean;
    media_server_type?: string;
    media_server_connected?: boolean;
    soulseek_connected?: boolean;
    tidal_connected?: boolean;
    qobuz_connected?: boolean;
    discogs_connected?: boolean;
    download_source?: string;
  };
  library?: { artists?: number; albums?: number; tracks?: number };
  database_size?: string;
  watchlist_count?: number;
  wishlist_count?: number;
  automations?: { enabled?: number; total?: number };
  active_downloads?: number;
  active_syncs?: number;
  paths?: {
    download_path?: string;
    download_path_exists?: boolean;
    download_path_writable?: boolean;
    transfer_folder?: string;
    transfer_folder_exists?: boolean;
    transfer_folder_writable?: boolean;
    staging_folder?: string;
    staging_folder_exists?: boolean;
    music_videos_path?: string;
    music_videos_path_exists?: boolean;
    music_library_paths?: PathEntry[];
  };
  config?: {
    log_level?: string;
    source_mode?: string;
    hybrid_sources?: string[];
    primary_metadata_source?: string;
    quality_profile?: string;
    organization_template?: string;
    post_processing_enabled?: boolean;
    lossy_copy_enabled?: boolean;
    lossy_copy_format?: string;
    lossy_copy_bitrate?: number;
    acoustid_enabled?: boolean;
    auto_scan_enabled?: boolean;
    auto_import_enabled?: boolean;
    allow_duplicate_tracks?: boolean;
    replace_lower_quality?: boolean;
    m3u_export_enabled?: boolean;
  };
  enrichment_workers?: Record<string, string>;
  download_client_failures?: string[];
  api_rates?: Record<string, { cpm?: number; limit?: number; endpoints?: Record<string, number> }>;
  spotify_rate_limit?: {
    active?: boolean;
    endpoint?: string;
    remaining_seconds?: number;
    retry_after?: number;
  };
  available_logs?: { file: string; size: string }[];
  log_source?: string;
  recent_logs?: string[];
}

export function buildDebugInfoText(data: DebugInfoResponse): string {
  const ck = '✓';
  const ex = '✗';
  let text = 'SoulSync Debug Info\n';
  text += '═══════════════════════════════════\n\n';

  text += '── System ──\n';
  text += `Version:     ${data.version}\n`;
  text += `OS:          ${data.os}${data.docker ? ' (Docker)' : ''}\n`;
  text += `Python:      ${data.python}\n`;
  text += `ffmpeg:      ${data.ffmpeg || 'unknown'}\n`;
  text += `Runner:      ${data.runner || 'unknown'}\n`;
  text += `Uptime:      ${data.uptime || 'unknown'}\n`;
  text += `Memory:      ${data.memory_usage || '?'} (system: ${data.system_memory || '?'})\n`;
  text += `CPU:         ${data.cpu_percent || '?'}\n`;
  text += `Threads:     ${data.thread_count || '?'}\n\n`;

  text += '── Services ──\n';
  text += `Music Source:  ${data.services?.music_source || 'unknown'}\n`;
  text += `Spotify:       ${data.services?.spotify_connected ? ck + ' Connected' : ex + ' Disconnected'}${data.services?.spotify_rate_limited ? ' (RATE LIMITED)' : ''}\n`;
  text += `Media Server:  ${data.services?.media_server_type || 'none'} ${data.services?.media_server_connected ? ck + ' Connected' : ex + ' Disconnected'}\n`;
  text += `Soulseek:      ${data.services?.soulseek_connected ? ck + ' Connected' : ex + ' Disconnected'}\n`;
  text += `Tidal:         ${data.services?.tidal_connected ? ck + ' Connected' : ex + ' Disconnected'}\n`;
  text += `Qobuz:         ${data.services?.qobuz_connected ? ck + ' Connected' : ex + ' Disconnected'}\n`;
  text += `Discogs:       ${data.services?.discogs_connected ? ck + ' Connected' : ex + ' No Token'}\n`;
  text += `Download Mode: ${data.services?.download_source || 'unknown'}\n\n`;

  text += '── Library ──\n';
  text += `Artists:  ${data.library?.artists?.toLocaleString() || '0'}\n`;
  text += `Albums:   ${data.library?.albums?.toLocaleString() || '0'}\n`;
  text += `Tracks:   ${data.library?.tracks?.toLocaleString() || '0'}\n`;
  text += `Database: ${data.database_size || 'unknown'}\n`;
  text += `Watchlist: ${data.watchlist_count || 0} artists\n`;
  text += `Wishlist:  ${data.wishlist_count || 0} pending\n`;
  text += `Automations: ${data.automations?.enabled || 0} enabled / ${data.automations?.total || 0} total\n\n`;

  text += '── Active ──\n';
  text += `Downloads: ${data.active_downloads || 0}\n`;
  text += `Syncs:     ${data.active_syncs || 0}\n\n`;

  text += '── Paths ──\n';
  const pathStatus = (exists?: boolean, writable?: boolean) =>
    exists ? (writable ? ck + ' ok' : ck + ' exists ' + ex + ' not writable') : ex + ' missing';
  text += `Input:    ${data.paths?.download_path || '(not set)'} [${pathStatus(data.paths?.download_path_exists, data.paths?.download_path_writable)}]\n`;
  text += `Output:   ${data.paths?.transfer_folder || '(not set)'} [${pathStatus(data.paths?.transfer_folder_exists, data.paths?.transfer_folder_writable)}]\n`;
  text += `Import:   ${data.paths?.staging_folder ? data.paths.staging_folder + ' [' + (data.paths.staging_folder_exists ? ck + ' ok' : ex + ' missing') + ']' : '(not configured — optional)'}\n`;
  if (data.paths?.music_videos_path) {
    text += `Videos:   ${data.paths.music_videos_path} [${data.paths.music_videos_path_exists ? ck + ' ok' : ex + ' missing'}]\n`;
  }
  if (data.paths?.music_library_paths?.length) {
    text += `Library Paths:\n`;
    data.paths.music_library_paths.forEach((p) => {
      text += `  ${p.path} [${p.exists ? ck + ' ok' : ex + ' missing'}]\n`;
    });
  }
  text += '\n';

  text += '── Config ──\n';
  if (data.config) {
    text += `Log Level:        ${data.config.log_level || 'INFO'}\n`;
    text += `Source Mode:      ${data.config.source_mode || 'unknown'}\n`;
    if (data.config.source_mode === 'hybrid' && data.config.hybrid_sources?.length) {
      text += `Hybrid Priority:  ${data.config.hybrid_sources.join(' → ')}\n`;
    }
    text += `Metadata Source:  ${data.config.primary_metadata_source || 'deezer'}\n`;
    text += `Quality Profile:  ${data.config.quality_profile || 'default'}\n`;
    text += `Folder Template:  ${data.config.organization_template || '(default)'}\n`;
    text += `Post-Processing:  ${data.config.post_processing_enabled ? 'enabled' : 'disabled'}\n`;
    if (data.config.lossy_copy_enabled) {
      text += `Lossy Copy:       ${data.config.lossy_copy_format?.toUpperCase()} @ ${data.config.lossy_copy_bitrate}kbps\n`;
    }
    text += `AcoustID:         ${data.config.acoustid_enabled ? 'enabled' : 'disabled'}\n`;
    text += `Auto Scan:        ${data.config.auto_scan_enabled ? 'enabled' : 'disabled'}\n`;
    text += `Auto Import:      ${data.config.auto_import_enabled ? 'enabled' : 'disabled'}\n`;
    text += `Duplicate Tracks: ${data.config.allow_duplicate_tracks ? 'allowed' : 'rejected'}\n`;
    text += `Replace Quality:  ${data.config.replace_lower_quality ? 'enabled' : 'disabled'}\n`;
    text += `M3U Export:       ${data.config.m3u_export_enabled ? 'enabled' : 'disabled'}\n`;
  }
  text += '\n';

  text += '── Enrichment Workers ──\n';
  if (data.enrichment_workers) {
    const active: string[] = [];
    const paused: string[] = [];
    Object.entries(data.enrichment_workers).forEach(([name, status]) => {
      (status === 'active' ? active : paused).push(name);
    });
    text += `Active:  ${active.length > 0 ? active.join(', ') : 'none'}\n`;
    text += `Paused:  ${paused.length > 0 ? paused.join(', ') : 'none'}\n`;
  }
  text += '\n';

  if (data.download_client_failures?.length) {
    text += '── Download Client Failures ──\n';
    data.download_client_failures.forEach((f) => {
      text += `  ❌ ${f}\n`;
    });
    text += '\n';
  }

  text += '── API Rates (calls/min) ──\n';
  if (data.api_rates) {
    Object.entries(data.api_rates).forEach(([svc, info]) => {
      const cpm = info.cpm || 0;
      const limit = info.limit || '?';
      const pct = info.limit ? Math.round((cpm / info.limit) * 100) : 0;
      text += `${svc.padEnd(14)} ${String(cpm).padStart(5)}/min  (limit: ${limit}, ${pct}%)`;
      if (info.endpoints && Object.keys(info.endpoints).length > 0) {
        text += `  endpoints: ${Object.entries(info.endpoints)
          .map(([e, c]) => `${e}:${c}`)
          .join(', ')}`;
      }
      text += '\n';
    });
  }
  if (data.spotify_rate_limit?.active) {
    const rl = data.spotify_rate_limit;
    const mins = Math.ceil((rl.remaining_seconds || 0) / 60);
    text += `\n*** SPOTIFY RATE LIMITED ***\n`;
    text += `Triggered by: ${rl.endpoint || 'unknown'}\n`;
    text += `Remaining:    ${mins} minutes\n`;
    text += `Retry-After:  ${rl.retry_after || '?'}s\n`;
  }
  text += '\n';

  if (data.available_logs?.length) {
    text += '── Log Files ──\n';
    data.available_logs.forEach((log) => {
      text += `  ${log.file.padEnd(24)} ${log.size}\n`;
    });
    text += '\n';
  }

  text += `── Logs: ${data.log_source || 'app'}.log (last ${data.recent_logs?.length || 0} lines) ──\n`;
  if (data.recent_logs?.length) {
    data.recent_logs.forEach((line) => {
      text += line + '\n';
    });
  } else {
    text += '(no log lines)\n';
  }
  text +=
    '\n---\nPaste this output into your GitHub issue at https://github.com/Nezreka/SoulSync/issues\n';

  return text;
}
