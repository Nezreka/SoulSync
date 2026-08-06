/**
 * The export core, pinned against stats-automations.js 662-819.
 *
 * The vanilla side is DOM- and fetch-coupled (it builds an overlay, then polls),
 * so there is nothing to lift and run differentially here the way timeAgo was.
 * Instead every branch is pinned by literal, and the user-visible copy is
 * cross-checked against the vanilla text so a silent reword fails.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import type { ExportJob } from './-sync.export';

import {
  EXPORT_DESTINATIONS,
  EXPORT_LINK_COLOR,
  EXPORT_POLL_MS,
  EXPORT_POLL_RETRY_MS,
  EXPORT_START_ERROR_STATUS,
  EXPORT_STARTING_STATUS,
  exportDestinationLabel,
  exportDownloadUrl,
  exportNotConnectedStatus,
  exportPollOutcome,
  exportServiceLabel,
  exportStartOutcome,
  isExportModeGated,
  isServiceExport,
} from './-sync.export';

const STATS = readFileSync(resolve(process.cwd(), 'static/stats-automations.js'), 'utf8');

describe('EXPORT_DESTINATIONS (669-684)', () => {
  it('offers the four modes in the vanilla order, ListenBrainz push accented', () => {
    expect(EXPORT_DESTINATIONS.map((d) => d.mode)).toEqual([
      'push',
      'download',
      'spotify',
      'deezer',
    ]);
    expect(EXPORT_DESTINATIONS.filter((d) => d.primary).map((d) => d.mode)).toEqual(['push']);
  });

  it('carries the vanilla copy verbatim', () => {
    expect(EXPORT_DESTINATIONS.map((d) => d.title)).toEqual([
      'Sync to ListenBrainz',
      'Download .jspf file',
      'Sync to Spotify',
      'Sync to Deezer',
    ]);
    for (const dest of EXPORT_DESTINATIONS) {
      expect(STATS).toContain(dest.title);
      expect(STATS).toContain(dest.detail);
    }
  });

  it('the accent styling belongs to the FIRST button only', () => {
    // The primary flag is the only thing separating the two button styles, so
    // it is pinned against the markup that carries them.
    expect(STATS).toContain('background:rgba(var(--accent-rgb),0.12)');
    expect(STATS).toContain('background:rgba(255,255,255,0.04)');
  });
});

describe('mode classification', () => {
  it('spotify and deezer are the service modes; push and download are not', () => {
    expect(isServiceExport('spotify')).toBe(true);
    expect(isServiceExport('deezer')).toBe(true);
    expect(isServiceExport('push')).toBe(false);
    expect(isServiceExport('download')).toBe(false);
  });

  it('capitalises the service name, and everything else pushes to ListenBrainz', () => {
    expect(exportServiceLabel('spotify')).toBe('Spotify');
    expect(exportServiceLabel('deezer')).toBe('Deezer');
    expect(exportDestinationLabel('spotify')).toBe('Spotify');
    expect(exportDestinationLabel('deezer')).toBe('Deezer');
    expect(exportDestinationLabel('push')).toBe('ListenBrainz');
    expect(exportDestinationLabel('download')).toBe('ListenBrainz');
  });
});

describe('isExportModeGated (717-719)', () => {
  it('gates a service the account is not connected to', () => {
    expect(isExportModeGated('spotify', [])).toBe(true);
    expect(isExportModeGated('deezer', ['spotify'])).toBe(true);
    expect(isExportModeGated('spotify', ['spotify'])).toBe(false);
  });

  it('never gates the two ListenBrainz choices', () => {
    expect(isExportModeGated('push', [])).toBe(false);
    expect(isExportModeGated('download', [])).toBe(false);
  });

  it('gates NOTHING when the probe never answered — the vanilla swallows it', () => {
    expect(isExportModeGated('spotify', null)).toBe(false);
    expect(isExportModeGated('deezer', null)).toBe(false);
  });
});

describe('fixed statuses', () => {
  it('paints "Starting export…" in violet before the POST (733)', () => {
    expect(EXPORT_STARTING_STATUS).toEqual({ text: 'Starting export…', color: '#a78bfa' });
    expect(STATS).toContain('Starting export…');
  });

  it('a thrown start POST paints a bare "Export error" (753)', () => {
    expect(EXPORT_START_ERROR_STATUS).toEqual({ text: 'Export error', color: '#ef4444' });
    expect(STATS).toContain('Export error');
  });

  it('the nudge for a greyed-out choice hides after 9s (699)', () => {
    expect(exportNotConnectedStatus('spotify')).toEqual({
      text: 'Connect Spotify in Settings → Connections to export here',
      color: '#f59e0b',
      autoHideMs: 9000,
    });
    expect(exportNotConnectedStatus('deezer').text).toBe(
      'Connect Deezer in Settings → Connections to export here',
    );
    expect(STATS).toContain('in Settings → Connections to export here');
  });

  it('every anchor uses the same link blue', () => {
    expect(EXPORT_LINK_COLOR).toBe('#38bdf8');
  });

  it('polls at 1s, and backs off to 2s after a failed tick', () => {
    expect(EXPORT_POLL_MS).toBe(1000);
    expect(EXPORT_POLL_RETRY_MS).toBe(2000);
    expect(STATS).toContain('_pollPlaylistExport(jobId, playlistId, mode, name), 1000)');
    expect(STATS).toContain('_pollPlaylistExport(jobId, playlistId, mode, name), 2000)');
  });
});

describe('exportStartOutcome (742-749)', () => {
  it('a Spotify auth demand renders the URL as an underlined LINK, not a popup', () => {
    const outcome = exportStartOutcome({ needs_auth: true, auth_url: 'https://accounts/x' });
    expect(outcome.jobId).toBeUndefined();
    expect(outcome.status).toEqual({
      text: 'Spotify needs permission to create playlists —',
      link: { url: 'https://accounts/x', label: 'authorize', underline: true },
      suffix: ', then click Export again.',
      color: '#f59e0b',
      autoHideMs: 20000,
    });
    expect(STATS).toContain('Spotify needs permission to create playlists');
    expect(STATS).toContain(', then click Export again.');
  });

  it('needs_auth WITHOUT a url falls through to the ordinary failure arm', () => {
    expect(exportStartOutcome({ needs_auth: true }).status).toEqual({
      text: 'Export failed to start',
      color: '#ef4444',
    });
  });

  it('reports the backend error, or the generic one when it sent none', () => {
    expect(exportStartOutcome({ success: false, error: 'No LB token' }).status).toEqual({
      text: 'No LB token',
      color: '#ef4444',
    });
    expect(exportStartOutcome({}).status?.text).toBe('Export failed to start');
  });

  it('success WITHOUT a job_id is still a failure (the && in 745)', () => {
    expect(exportStartOutcome({ success: true }).jobId).toBeUndefined();
    expect(exportStartOutcome({ success: true }).status?.text).toBe('Export failed to start');
  });

  it('a started job returns its id and paints nothing over "Starting export…"', () => {
    expect(exportStartOutcome({ success: true, job_id: 'j1' })).toEqual({
      status: null,
      jobId: 'j1',
    });
  });
});

describe('exportPollOutcome — resolving (762-765)', () => {
  const resolving = (job: ExportJob) => exportPollOutcome(job, 'push', 'j1');

  it('reports done/total with a rounded percent', () => {
    expect(resolving({ phase: 'resolving', done: 3, total: 8 }).status).toEqual({
      text: 'Matching 3/8 (38%)',
      color: '#38bdf8',
    });
  });

  it('a zero total means 0%, not a division by zero', () => {
    expect(resolving({ phase: 'resolving', total: 0 }).status?.text).toBe('Matching 0/0 (0%)');
  });

  it('appends the matched count whenever stats.resolved is PRESENT — zero included', () => {
    expect(
      resolving({ phase: 'resolving', done: 1, total: 2, stats: { resolved: 4 } })?.status?.text,
    ).toBe('Matching 1/2 (50%) · 4 matched');
    // `!= null`, not truthiness: a genuine zero still reports.
    expect(
      resolving({ phase: 'resolving', done: 1, total: 2, stats: { resolved: 0 } })?.status?.text,
    ).toBe('Matching 1/2 (50%) · 0 matched');
    expect(resolving({ phase: 'resolving', done: 1, total: 2, stats: {} })?.status?.text).toBe(
      'Matching 1/2 (50%)',
    );
  });

  it('keeps polling', () => {
    expect(resolving({ phase: 'resolving' }).terminal).toBe(false);
  });
});

describe('exportPollOutcome — pushing (767-769)', () => {
  it('names the destination it is pushing to', () => {
    expect(exportPollOutcome({ phase: 'pushing' }, 'spotify', 'j1').status).toEqual({
      text: 'Pushing to Spotify…',
      color: '#a78bfa',
    });
    expect(exportPollOutcome({ phase: 'pushing' }, 'deezer', 'j1').status?.text).toBe(
      'Pushing to Deezer…',
    );
    expect(exportPollOutcome({ phase: 'pushing' }, 'push', 'j1').status?.text).toBe(
      'Pushing to ListenBrainz…',
    );
    expect(exportPollOutcome({ phase: 'pushing' }, 'download', 'j1').status?.text).toBe(
      'Pushing to ListenBrainz…',
    );
    expect(exportPollOutcome({ phase: 'pushing' }, 'push', 'j1').terminal).toBe(false);
  });
});

describe('exportPollOutcome — done, the SERVICE arm (772-779)', () => {
  const done: ExportJob = {
    phase: 'done',
    stats: { resolved: 12, from_search: 3, unmatched: 2 },
    push: { url: 'https://open.spotify/p/1' },
  };

  it('reports added / live-matched / not-on-service and links the new playlist', () => {
    const outcome = exportPollOutcome(done, 'spotify', 'j1');
    expect(outcome.status).toEqual({
      text: 'Exported to Spotify · 12 added (3 matched live) · 2 not on Spotify',
      color: '#22c55e',
      link: { url: 'https://open.spotify/p/1', label: 'open' },
      autoHideMs: 12000,
    });
    expect(outcome.terminal).toBe(true);
    expect(outcome.toast).toEqual({
      message: 'Playlist exported to Spotify (12 added (3 matched live) · 2 not on Spotify)',
      type: 'success',
    });
    expect(outcome.downloadUrl).toBeUndefined();
  });

  it('drops both optional clauses when their counts are zero or absent', () => {
    const outcome = exportPollOutcome({ phase: 'done', stats: { resolved: 5 } }, 'deezer', 'j1');
    expect(outcome.status?.text).toBe('Exported to Deezer · 5 added');
    expect(outcome.status?.link).toBeUndefined();
    expect(outcome.toast?.message).toBe('Playlist exported to Deezer (5 added)');
  });

  it('no stats at all still reports zero added', () => {
    expect(exportPollOutcome({ phase: 'done' }, 'spotify', 'j1').status?.text).toBe(
      'Exported to Spotify · 0 added',
    );
  });

  it('reads push.url — NOT the ListenBrainz arm’s push.playlist_url', () => {
    const outcome = exportPollOutcome(
      { phase: 'done', push: { playlist_url: 'https://lb/x' } },
      'spotify',
      'j1',
    );
    expect(outcome.status?.link).toBeUndefined();
  });

  it('pins the vanilla wording', () => {
    expect(STATS).toContain('matched live');
    expect(STATS).toContain('Playlist exported to ');
  });
});

describe('exportPollOutcome — done, the DOWNLOAD arm (783-786)', () => {
  it('hands off the .jspf and reports coverage for 8s, with no toast', () => {
    const outcome = exportPollOutcome(
      { phase: 'done', summary: { included: 9, total: 10, skipped: 1 } },
      'download',
      'job-7',
    );
    expect(outcome.downloadUrl).toBe('/api/playlists/export/download/job-7');
    expect(outcome.status).toEqual({
      text: 'Downloaded · 9/10 matched · 1 unmatched',
      color: '#22c55e',
      autoHideMs: 8000,
    });
    expect(outcome.toast).toBeUndefined();
    expect(outcome.terminal).toBe(true);
  });

  it('drops the unmatched clause at zero, and defaults a missing summary', () => {
    expect(
      exportPollOutcome({ phase: 'done', summary: { included: 4, total: 4 } }, 'download', 'j')
        .status?.text,
    ).toBe('Downloaded · 4/4 matched');
    expect(exportPollOutcome({ phase: 'done' }, 'download', 'j').status?.text).toBe(
      'Downloaded · 0/0 matched',
    );
  });

  it('builds the download url from the job id', () => {
    expect(exportDownloadUrl('abc')).toBe('/api/playlists/export/download/abc');
  });
});

describe('exportPollOutcome — done, the LISTENBRAINZ arm (787-791)', () => {
  it('links the new LB playlist through push.playlist_url and toasts', () => {
    const outcome = exportPollOutcome(
      {
        phase: 'done',
        summary: { included: 7, total: 9, skipped: 2 },
        push: { playlist_url: 'https://lb/playlist/9' },
      },
      'push',
      'j1',
    );
    expect(outcome.status).toEqual({
      text: 'Synced to ListenBrainz · 7/9 matched · 2 unmatched',
      color: '#22c55e',
      link: { url: 'https://lb/playlist/9', label: 'view' },
      autoHideMs: 12000,
    });
    expect(outcome.toast).toEqual({
      message: 'Playlist synced to ListenBrainz (7/9 matched · 2 unmatched)',
      type: 'success',
    });
    expect(outcome.terminal).toBe(true);
  });

  it('still reports without a url, and ignores the service arm’s push.url', () => {
    expect(
      exportPollOutcome({ phase: 'done', push: { url: 'https://spotify/x' } }, 'push', 'j1').status
        ?.link,
    ).toBeUndefined();
  });
});

describe('exportPollOutcome — error and unknown phases', () => {
  it('an error phase reports the backend message for 10s and stops', () => {
    expect(exportPollOutcome({ phase: 'error', error: 'MB timeout' }, 'push', 'j').status).toEqual({
      text: 'MB timeout',
      color: '#ef4444',
      autoHideMs: 10000,
    });
    expect(exportPollOutcome({ phase: 'error' }, 'push', 'j').status?.text).toBe('Export failed');
    expect(exportPollOutcome({ phase: 'error' }, 'push', 'j').terminal).toBe(true);
  });

  it('a phase it does not know paints nothing and keeps polling', () => {
    expect(exportPollOutcome({ phase: 'queued' }, 'push', 'j')).toEqual({
      status: null,
      terminal: false,
    });
    expect(exportPollOutcome({}, 'push', 'j')).toEqual({ status: null, terminal: false });
  });
});
