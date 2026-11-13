import { describe, expect, it } from 'vitest';

import { automationGlyph } from './automation-art';

const glyph = (action_type: string, trigger_type = 'schedule') =>
  automationGlyph({ action_type, trigger_type, owned_by: null });

describe('automationGlyph', () => {
  it("gives the video side's own kinds of work their own picture", () => {
    expect(glyph('video_apply_overlays')).toBe('layers');
    expect(glyph('video_sync_collections')).toBe('layers');
    expect(glyph('video_add_airing_episodes')).toBe('calendar');
    expect(glyph('video_refresh_airing_schedules')).toBe('calendar');
    expect(glyph('video_rss_sync')).toBe('feed');
    expect(glyph('video_extto_fresh_refresh')).toBe('feed');
    expect(glyph('video_import_lists')).toBe('feed');
  });

  it('does not read every refresh as a feed', () => {
    // "refresh" holds "fresh": the music refreshes kept their own glyphs
    expect(glyph('metasync_refresh_charts')).toBe('clock');
    expect(glyph('refresh_beatport_cache')).toBe('clock');
    expect(glyph('refresh_mirrored')).toBe('playlist');
  });

  it('keeps the shared kinds on both sides', () => {
    expect(glyph('video_process_movie_wishlist')).toBe('star');
    expect(glyph('process_wishlist')).toBe('star');
    expect(glyph('video_backup_database')).toBe('database');
    expect(glyph('video_scan_watchlist_people')).toBe('radar');
    expect(glyph('video_scan_watchlist_playlists')).toBe('playlist');
    expect(glyph('video_clean_plex_images')).toBe('broom');
  });

  it('reads an event trigger with no better picture as a bolt', () => {
    expect(glyph('notify_only', 'video_download_completed')).toBe('bolt');
  });
});
