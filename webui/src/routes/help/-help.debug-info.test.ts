import { describe, expect, it } from 'vitest';

import { buildDebugInfoText } from './-help.debug-info';

describe('buildDebugInfoText', () => {
  it('renders the full report shape maintainers triage by', () => {
    const text = buildDebugInfoText({
      version: '3.1.9',
      os: 'Linux',
      docker: true,
      python: '3.11.15',
      services: { spotify_connected: true, soulseek_connected: false },
      library: { artists: 1234, albums: 5678, tracks: 91011 },
      config: { source_mode: 'hybrid', hybrid_sources: ['soulseek', 'youtube'] },
      api_rates: { spotify: { cpm: 30, limit: 60, endpoints: { search: 12 } } },
      recent_logs: ['line one', 'line two'],
      log_source: 'app',
    });
    expect(text).toContain('SoulSync Debug Info');
    expect(text).toContain('Version:     3.1.9');
    expect(text).toContain('OS:          Linux (Docker)');
    expect(text).toContain('Spotify:       ✓ Connected');
    expect(text).toContain('Soulseek:      ✗ Disconnected');
    expect(text).toContain('Artists:  1,234');
    expect(text).toContain('Hybrid Priority:  soulseek → youtube');
    expect(text).toContain('spotify           30/min  (limit: 60, 50%)');
    expect(text).toContain('endpoints: search:12');
    expect(text).toContain('── Logs: app.log (last 2 lines) ──');
    expect(text).toContain('line one');
    expect(text).toContain('Paste this output into your GitHub issue');
  });

  it('degrades gracefully with an empty payload', () => {
    const text = buildDebugInfoText({});
    expect(text).toContain('ffmpeg:      unknown');
    expect(text).toContain('Downloads: 0');
    expect(text).toContain('(no log lines)');
  });

  it('surfaces an active Spotify rate limit loudly', () => {
    const text = buildDebugInfoText({
      spotify_rate_limit: { active: true, endpoint: '/search', remaining_seconds: 300 },
    });
    expect(text).toContain('*** SPOTIFY RATE LIMITED ***');
    expect(text).toContain('Triggered by: /search');
    expect(text).toContain('Remaining:    5 minutes');
  });
});
