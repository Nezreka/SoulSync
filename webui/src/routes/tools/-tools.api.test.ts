/**
 * Tools api layer. The point of most of these is the ERROR path: several of
 * these calls are on polls where treating a blip as "finished" or "empty" would
 * blank a card or stop a running job's progress from ever updating again.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  backupDownloadUrl,
  bulkFindingAction,
  clearFindings,
  clearMetadataCacheBySource,
  clearMusicBrainzCache,
  createBackup,
  deleteBackup,
  dismissFinding,
  dismissFindingChecked,
  fetchActiveMediaServer,
  fetchBackups,
  fetchBlacklist,
  fetchBulkFixStatus,
  fetchCacheHealth,
  fetchDatabaseStats,
  fetchDbUpdateStatus,
  fetchDiscoveryPoolStats,
  fetchDuplicateCleanStatus,
  fetchFindingCounts,
  fetchMediaScanStatus,
  fetchMetadataCacheStats,
  fetchMetadataUpdateStatus,
  fetchReconcileIdsStatus,
  fetchRepairFindings,
  fetchRepairHistory,
  fetchRepairJobs,
  fetchRepairProgress,
  fetchRepairStatus,
  findExactArtist,
  fixFinding,
  removeBlacklistEntry,
  requestMediaScan,
  resolveFinding,
  restoreBackup,
  runRepairJob,
  saveRepairJobSettings,
  searchLibraryArtists,
  setRepairJobEnabled,
  startBulkFix,
  startDatabaseUpdate,
  startDuplicateClean,
  startMetadataUpdate,
  startReconcileIds,
  stopBulkFix,
  stopDatabaseUpdate,
  stopDuplicateClean,
  stopMetadataUpdate,
  stopRepairJob,
  toggleRepairMaster,
} from './-tools.api';

const fetchMock = vi.fn();

/** Last request as (url, method, parsed body). */
function lastCall(): { url: string; method: string; body: unknown } {
  const [url, init] = fetchMock.mock.calls.at(-1) as [string, RequestInit | undefined];
  return {
    url,
    method: init?.method || 'GET',
    body: init?.body ? JSON.parse(init.body as string) : undefined,
  };
}

function jsonOnce(data: unknown, ok = true, status = 200) {
  fetchMock.mockResolvedValueOnce({ ok, status, json: async () => data } as unknown as Response);
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('repair status + jobs', () => {
  it('returns the status payload', async () => {
    jsonOnce({ enabled: true, findings_pending: 3 });
    await expect(fetchRepairStatus()).resolves.toEqual({ enabled: true, findings_pending: 3 });
  });

  it('returns null rather than throwing when status is unavailable', async () => {
    // On a 5s poll: a blip must leave the card as it was, not blank it.
    jsonOnce({}, false, 500);
    await expect(fetchRepairStatus()).resolves.toBeNull();
    fetchMock.mockRejectedValueOnce(new Error('offline'));
    await expect(fetchRepairStatus()).resolves.toBeNull();
  });

  it('treats an unavailable progress hydrate as nothing running', async () => {
    jsonOnce({}, false, 500);
    await expect(fetchRepairProgress()).resolves.toEqual({});
    fetchMock.mockRejectedValueOnce(new Error('offline'));
    await expect(fetchRepairProgress()).resolves.toEqual({});
  });

  it('toggles the master switch and throws on failure', async () => {
    jsonOnce({ enabled: false });
    await expect(toggleRepairMaster()).resolves.toEqual({ enabled: false });
    expect(lastCall().method).toBe('POST');
    jsonOnce({}, false, 500);
    await expect(toggleRepairMaster()).rejects.toThrow('Failed to toggle');
  });

  it('unwraps the jobs list and defaults to empty', async () => {
    jsonOnce({ jobs: [{ job_id: 'a' }] });
    await expect(fetchRepairJobs()).resolves.toHaveLength(1);
    jsonOnce({});
    await expect(fetchRepairJobs()).resolves.toEqual([]);
    jsonOnce({}, false, 500);
    await expect(fetchRepairJobs()).rejects.toThrow('Failed to fetch jobs');
  });

  it('posts the job toggle without checking the response', async () => {
    // The vanilla flips the card visuals optimistically.
    jsonOnce({}, false, 500);
    await expect(setRepairJobEnabled('dead_file_cleaner', false)).resolves.toBeUndefined();
    expect(lastCall().url).toBe('/api/repair/jobs/dead_file_cleaner/toggle');
    expect(lastCall().body).toEqual({ enabled: false });
  });

  it('PUTs settings with the interval alongside them', async () => {
    jsonOnce({});
    await saveRepairJobSettings('acoustid_scanner', 12, { dry_run: true });
    expect(lastCall().method).toBe('PUT');
    expect(lastCall().body).toEqual({ interval_hours: 12, settings: { dry_run: true } });
  });

  it('runs a job', async () => {
    jsonOnce({});
    await runRepairJob('orphan_file_detector');
    expect(lastCall().url).toBe('/api/repair/jobs/orphan_file_detector/run');
    expect(lastCall().method).toBe('POST');
  });

  it('reports whether a stop actually stopped anything', async () => {
    jsonOnce({ stopped: true });
    await expect(stopRepairJob('x')).resolves.toEqual({ stopped: true });
    jsonOnce({ stopped: false });
    await expect(stopRepairJob('x')).resolves.toEqual({ stopped: false });
  });

  it('throws the server error from a failed stop so the button can be restored', async () => {
    jsonOnce({ error: 'not running' });
    await expect(stopRepairJob('x')).rejects.toThrow('not running');
  });

  it('unwraps history runs', async () => {
    jsonOnce({ runs: [{ job_id: 'a' }] });
    await expect(fetchRepairHistory()).resolves.toHaveLength(1);
    expect(lastCall().url).toBe('/api/repair/history?limit=50');
    jsonOnce({});
    await expect(fetchRepairHistory(10)).resolves.toEqual([]);
    expect(lastCall().url).toBe('/api/repair/history?limit=10');
  });
});

describe('findings', () => {
  it('omits empty filters rather than sending them blank', async () => {
    jsonOnce({ items: [], total: 0, page: 0 });
    await fetchRepairFindings({ jobId: '', severity: '', status: '', page: 0, limit: 30 });
    expect(lastCall().url).toBe('/api/repair/findings?page=0&limit=30');
  });

  it('sends the filters that are set', async () => {
    jsonOnce({ items: [], total: 0, page: 2 });
    await fetchRepairFindings({
      jobId: 'duplicate_scanner',
      severity: 'warning',
      status: 'pending',
      page: 2,
      limit: 60,
    });
    expect(lastCall().url).toBe(
      '/api/repair/findings?job_id=duplicate_scanner&severity=warning&status=pending&page=2&limit=60',
    );
  });

  it('defaults a partial page payload', async () => {
    jsonOnce({});
    await expect(fetchRepairFindings({ page: 0, limit: 30 })).resolves.toEqual({
      items: [],
      total: 0,
      page: 0,
    });
  });

  it('throws when findings or counts are unavailable', async () => {
    jsonOnce({}, false, 500);
    await expect(fetchRepairFindings({ page: 0, limit: 30 })).rejects.toThrow(
      'Failed to fetch findings',
    );
    jsonOnce({}, false, 500);
    await expect(fetchFindingCounts()).rejects.toThrow('Failed to fetch counts');
  });

  it('posts an empty body when a fix needs no action', async () => {
    jsonOnce({ success: true });
    await fixFinding(7);
    expect(lastCall().url).toBe('/api/repair/findings/7/fix');
    expect(lastCall().body).toEqual({});
  });

  it('passes the fix action through — it doubles as a track id and an art target', async () => {
    jsonOnce({ success: true });
    await fixFinding(7, 'staging');
    expect(lastCall().body).toEqual({ fix_action: 'staging' });
    jsonOnce({ success: true });
    await fixFinding(7, 'album');
    expect(lastCall().body).toEqual({ fix_action: 'album' });
    jsonOnce({ success: true });
    await fixFinding(7, 'track-42');
    expect(lastCall().body).toEqual({ fix_action: 'track-42' });
  });

  it('resolves and dismisses', async () => {
    jsonOnce({});
    await resolveFinding(1);
    expect(lastCall().url).toBe('/api/repair/findings/1/resolve');
    jsonOnce({});
    await dismissFinding(2);
    expect(lastCall().url).toBe('/api/repair/findings/2/dismiss');
  });

  it('reports the HTTP status for the checked dismiss used by bulk fix', async () => {
    jsonOnce({}, true);
    await expect(dismissFindingChecked(3)).resolves.toBe(true);
    jsonOnce({}, false, 500);
    await expect(dismissFindingChecked(3)).resolves.toBe(false);
  });

  it('sends the id list for a bulk action', async () => {
    jsonOnce({});
    await bulkFindingAction([1, 2, 3], 'dismiss');
    expect(lastCall().body).toEqual({ ids: [1, 2, 3], action: 'dismiss' });
  });

  it('omits absent scope from a clear', async () => {
    jsonOnce({ success: true, deleted: 5 });
    await clearFindings();
    expect(lastCall().body).toEqual({});
    jsonOnce({ success: true, deleted: 5 });
    await clearFindings('discography_backfill', 'pending');
    expect(lastCall().body).toEqual({ job_id: 'discography_backfill', status: 'pending' });
  });
});

describe('bulk fix', () => {
  it('starts in the background with only the options that are set', async () => {
    jsonOnce({ started: true, total: 400 });
    await expect(
      startBulkFix({ jobId: 'orphan_file_detector', fixAction: 'delete' }),
    ).resolves.toEqual({ started: true, total: 400 });
    expect(lastCall().url).toBe('/api/repair/findings/bulk-fix-start');
    expect(lastCall().body).toEqual({ job_id: 'orphan_file_detector', fix_action: 'delete' });
    jsonOnce({ started: true });
    await startBulkFix({});
    expect(lastCall().body).toEqual({});
  });

  it('returns null on a transient status failure so the poll keeps running', async () => {
    // Returning a finished-looking {} here would end the run's progress display
    // while the server is still fixing.
    fetchMock.mockRejectedValueOnce(new Error('offline'));
    await expect(fetchBulkFixStatus()).resolves.toBeNull();
  });

  it('swallows a failed stop', async () => {
    fetchMock.mockRejectedValueOnce(new Error('offline'));
    expect(() => stopBulkFix()).not.toThrow();
  });
});

describe('cache health', () => {
  it('returns null when unavailable instead of faking healthy', async () => {
    jsonOnce({}, false, 500);
    await expect(fetchCacheHealth()).resolves.toBeNull();
    fetchMock.mockRejectedValueOnce(new Error('offline'));
    await expect(fetchCacheHealth()).resolves.toBeNull();
  });
});

describe('database updater', () => {
  it('treats a 200 carrying success:false as a FAILED start (#859)', async () => {
    jsonOnce({ success: false, error: 'already running' }, true, 200);
    await expect(startDatabaseUpdate('incremental')).resolves.toEqual({
      ok: false,
      error: 'already running',
    });
  });

  it('treats a non-2xx as failed even without a body', async () => {
    jsonOnce({}, false, 500);
    await expect(startDatabaseUpdate('incremental')).resolves.toEqual({
      ok: false,
      error: 'Failed to start update.',
    });
  });

  it('accepts a 200 with no success flag at all', async () => {
    jsonOnce({});
    await expect(startDatabaseUpdate('incremental')).resolves.toEqual({ ok: true });
  });

  it('sends only deep_scan for a deep scan — it takes precedence server-side', async () => {
    jsonOnce({ success: true });
    await startDatabaseUpdate('deep');
    expect(lastCall().body).toEqual({ deep_scan: true });
  });

  it('sends full_refresh true/false for the other two modes', async () => {
    jsonOnce({ success: true });
    await startDatabaseUpdate('full');
    expect(lastCall().body).toEqual({ full_refresh: true });
    jsonOnce({ success: true });
    await startDatabaseUpdate('incremental');
    expect(lastCall().body).toEqual({ full_refresh: false });
  });

  it('reports the stop status', async () => {
    jsonOnce({}, true);
    await expect(stopDatabaseUpdate()).resolves.toBe(true);
    jsonOnce({}, false, 500);
    await expect(stopDatabaseUpdate()).resolves.toBe(false);
  });

  it('returns null from the progress poll so a blip does not stop it', async () => {
    fetchMock.mockRejectedValueOnce(new Error('timeout'));
    await expect(fetchDbUpdateStatus()).resolves.toBeNull();
    jsonOnce({}, false, 500);
    await expect(fetchDbUpdateStatus()).resolves.toBeNull();
  });

  it('returns null stats rather than zeroes when the endpoint fails', async () => {
    // Zeroes would render "0 artists" over a real library.
    jsonOnce({}, false, 500);
    await expect(fetchDatabaseStats()).resolves.toBeNull();
  });
});

describe('reconcile ids + duplicate cleaner', () => {
  it('surfaces the start error message', async () => {
    jsonOnce({ error: 'scan in progress' }, false, 409);
    await expect(startReconcileIds()).resolves.toEqual({ ok: false, error: 'scan in progress' });
    jsonOnce({}, false, 500);
    await expect(startReconcileIds()).resolves.toEqual({ ok: false, error: 'could not start' });
    jsonOnce({});
    await expect(startReconcileIds()).resolves.toEqual({ ok: true });
  });

  it('keeps both progress polls alive through failures', async () => {
    fetchMock.mockRejectedValueOnce(new Error('timeout'));
    await expect(fetchReconcileIdsStatus()).resolves.toBeNull();
    fetchMock.mockRejectedValueOnce(new Error('timeout'));
    await expect(fetchDuplicateCleanStatus()).resolves.toBeNull();
  });

  it('starts and stops the duplicate cleaner', async () => {
    jsonOnce({});
    await expect(startDuplicateClean()).resolves.toEqual({ ok: true });
    jsonOnce({ error: 'busy' }, false, 409);
    await expect(startDuplicateClean()).resolves.toEqual({ ok: false, error: 'busy' });
    jsonOnce({}, true);
    await expect(stopDuplicateClean()).resolves.toBe(true);
  });
});

describe('metadata updater', () => {
  it('throws the server error when the start is refused', async () => {
    jsonOnce({ success: false, error: 'no spotify auth' });
    await expect(startMetadataUpdate(30)).rejects.toThrow('no spotify auth');
  });

  it('sends the refresh interval', async () => {
    jsonOnce({ success: true });
    await startMetadataUpdate(90);
    expect(lastCall().body).toEqual({ refresh_interval_days: 90 });
  });

  it('throws when the stop fails', async () => {
    jsonOnce({}, false, 500);
    await expect(stopMetadataUpdate()).rejects.toThrow('Failed to stop metadata update');
  });

  it('unwraps the status only when success is set', async () => {
    jsonOnce({ success: true, status: { status: 'running', processed: 4 } });
    await expect(fetchMetadataUpdateStatus()).resolves.toEqual({ status: 'running', processed: 4 });
    jsonOnce({ success: false });
    await expect(fetchMetadataUpdateStatus()).resolves.toBeNull();
  });
});

describe('media server + scan', () => {
  it('unwraps the active server only on success', async () => {
    jsonOnce({ success: true, active_server: 'plex' });
    await expect(fetchActiveMediaServer()).resolves.toBe('plex');
    jsonOnce({ success: false, active_server: 'plex' });
    await expect(fetchActiveMediaServer()).resolves.toBeNull();
    fetchMock.mockRejectedValueOnce(new Error('offline'));
    await expect(fetchActiveMediaServer()).resolves.toBeNull();
  });

  it('passes the scan reason and returns the delay', async () => {
    jsonOnce({ success: true, scan_info: { delay_seconds: 60 } });
    const result = await requestMediaScan('Manual scan triggered from dashboard');
    expect(lastCall().body).toEqual({ reason: 'Manual scan triggered from dashboard' });
    expect(result.scan_info?.delay_seconds).toBe(60);
  });

  it('unwraps scan status only on success', async () => {
    jsonOnce({ success: true, status: { is_scanning: true } });
    await expect(fetchMediaScanStatus()).resolves.toEqual({ is_scanning: true });
    jsonOnce({ success: false });
    await expect(fetchMediaScanStatus()).resolves.toBeNull();
  });
});

describe('backups', () => {
  it('returns null unless the payload says success', async () => {
    jsonOnce({ success: true, count: 2, db_size_mb: 10, backups: [] });
    await expect(fetchBackups()).resolves.toMatchObject({ count: 2 });
    jsonOnce({ success: false });
    await expect(fetchBackups()).resolves.toBeNull();
  });

  it('creates a backup', async () => {
    jsonOnce({ success: true, size_mb: 42 });
    await expect(createBackup()).resolves.toEqual({ success: true, size_mb: 42 });
    expect(lastCall().method).toBe('POST');
  });

  it('encodes the filename in every backup URL', async () => {
    expect(backupDownloadUrl('my backup #1.db')).toBe(
      '/api/database/backups/my%20backup%20%231.db/download',
    );
    jsonOnce({ success: true });
    await deleteBackup('my backup #1.db');
    expect(lastCall().url).toBe('/api/database/backups/my%20backup%20%231.db');
    expect(lastCall().method).toBe('DELETE');
  });

  it('sends no body for a plain restore and a force flag for the retry', async () => {
    jsonOnce({ success: true });
    await restoreBackup('a.db');
    expect(lastCall().body).toBeUndefined();
    jsonOnce({ success: true });
    await restoreBackup('a.db', true);
    expect(lastCall().body).toEqual({ force: true });
  });

  it('surfaces a version mismatch so the caller can confirm and force', async () => {
    jsonOnce({ version_mismatch: true, backup_version: '3.1.0', current_version: '3.1.8' });
    await expect(restoreBackup('a.db')).resolves.toMatchObject({ version_mismatch: true });
  });
});

describe('metadata cache', () => {
  it('returns null stats silently — the cache may not exist yet', async () => {
    jsonOnce({}, false, 404);
    await expect(fetchMetadataCacheStats()).resolves.toBeNull();
  });

  it('clears by source through the SAME endpoint as clear-all, via a query param', async () => {
    jsonOnce({ success: true, cleared: 3 });
    await clearMetadataCacheBySource('spotify');
    expect(lastCall().url).toBe('/api/metadata-cache/clear?source=spotify');
    expect(lastCall().method).toBe('DELETE');
  });

  it('scopes the musicbrainz clear to failed lookups when asked', async () => {
    jsonOnce({ success: true });
    await clearMusicBrainzCache(true);
    expect(lastCall().url).toBe('/api/metadata-cache/clear-musicbrainz?failed_only=true');
    jsonOnce({ success: true });
    await clearMusicBrainzCache();
    expect(lastCall().url).toBe('/api/metadata-cache/clear-musicbrainz');
  });
});

describe('blacklist', () => {
  it('returns BOTH the success flag and the entries — the two callers read them differently', async () => {
    // The count ignores `success`; the modal treats !success as empty. Collapsing
    // these would make the modal show rows the vanilla hides.
    jsonOnce({ success: true, entries: [{ id: 1 }] });
    await expect(fetchBlacklist()).resolves.toEqual({ success: true, entries: [{ id: 1 }] });
    jsonOnce({ success: false, entries: [{ id: 1 }] });
    await expect(fetchBlacklist()).resolves.toEqual({ success: false, entries: [{ id: 1 }] });
  });

  it('returns an empty list rather than throwing', async () => {
    jsonOnce({});
    await expect(fetchBlacklist()).resolves.toEqual({ success: false, entries: [] });
    fetchMock.mockRejectedValueOnce(new Error('offline'));
    await expect(fetchBlacklist()).resolves.toEqual({ success: false, entries: [] });
  });

  it('reports whether a removal succeeded', async () => {
    jsonOnce({ success: true });
    await expect(removeBlacklistEntry(4)).resolves.toBe(true);
    expect(lastCall().method).toBe('DELETE');
    jsonOnce({ success: false });
    await expect(removeBlacklistEntry(4)).resolves.toBe(false);
  });
});

describe('discovery pool stats', () => {
  it('reads the nested counters', async () => {
    jsonOnce({ stats: { matched: 12, failed: 3 } });
    await expect(fetchDiscoveryPoolStats()).resolves.toEqual({ matched: 12, failed: 3 });
  });

  it('returns NULL when stats is missing so the card keeps its placeholder', async () => {
    // The vanilla reads data.stats.matched unguarded, so a missing `stats`
    // throws into an empty catch and the card keeps showing its em dash.
    // Zeroes here would print a confident "0 matched" over a failed load.
    jsonOnce({});
    await expect(fetchDiscoveryPoolStats()).resolves.toBeNull();
    fetchMock.mockRejectedValueOnce(new Error('offline'));
    await expect(fetchDiscoveryPoolStats()).resolves.toBeNull();
  });

  it('still zero-fills an individual missing counter', async () => {
    jsonOnce({ stats: { matched: 5 } });
    await expect(fetchDiscoveryPoolStats()).resolves.toEqual({ matched: 5, failed: 0 });
  });
});

describe('artist lookup for findings', () => {
  it('asks for 50 so a short name is not pushed off page one', async () => {
    jsonOnce({ artists: [] });
    await searchLibraryArtists('Low');
    expect(lastCall().url).toBe('/api/library/artists?search=Low&limit=50');
  });

  it('encodes the search term', async () => {
    jsonOnce({ artists: [] });
    await searchLibraryArtists('AC/DC');
    expect(lastCall().url).toBe('/api/library/artists?search=AC%2FDC&limit=50');
  });

  it('defaults to an empty list', async () => {
    jsonOnce({});
    await expect(searchLibraryArtists('x')).resolves.toEqual([]);
  });

  it('matches only an exact name, case-insensitively', () => {
    const artists = [
      { id: 1, name: 'Below' },
      { id: 2, name: 'low' },
      { id: 3, name: 'Flower' },
    ];
    expect(findExactArtist(artists, 'Low')?.id).toBe(2);
    expect(findExactArtist(artists, 'Lowe')).toBeNull();
  });

  it('refuses a match with no id', () => {
    expect(findExactArtist([{ id: null, name: 'Low' }], 'Low')).toBeNull();
  });
});
