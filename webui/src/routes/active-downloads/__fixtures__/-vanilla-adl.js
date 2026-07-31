/*
 * FROZEN FIXTURE — do not edit, do not lint, do not "fix".
 *
 * This is the vanilla Downloads page exactly as it stood in pages-extra.js
 * immediately before it was deleted (commit 4544a2483^, lines 2491-4213).
 * It exists so -adl.helpers.differential.test.ts can keep comparing the React
 * port against the REAL functions it replaced, rather than against
 * expectations someone typed from memory.
 *
 * It is never loaded by the app. Nothing imports it. Changing it would only
 * make the parity test agree with a lie.
 *
 * The leading dash is required, not cosmetic: this lives under src/routes/,
 * and TanStack's generator treats every file there as a route candidate. It
 * warns on each build about a route file that exports no Route until the name
 * is prefixed (routeFileIgnorePrefix: '-').
 */

// ============================================
// ACTIVE DOWNLOADS PAGE — Centralized Live View
// ============================================

let _adlPoller = null;
let _adlFilter = 'all';
let _adlData = [];
let _adlBatches = [];
let _adlBatchHistory = [];
let _adlExpandedBatches = new Set();
let _adlBatchHistoryPoller = null;
let _adlFilterBatchId = null; // When set, main list shows only this batch
let _adlFetchCount = 0; // used to rate-limit periodic quarantine refresh
const _batchColorMap = {};
const _batchCompletedAt = {}; // batch_id -> timestamp when first seen as complete
let _batchColorNext = 0;

function _getBatchColor(batchId) {
  if (!batchId) return -1;
  if (_batchColorMap[batchId] === undefined) {
    // Deterministic color from batch_id hash for consistency across reloads
    let hash = 0;
    for (let i = 0; i < batchId.length; i++)
      hash = ((hash << 5) - hash + batchId.charCodeAt(i)) | 0;
    _batchColorMap[batchId] = Math.abs(hash) % 8;
  }
  return _batchColorMap[batchId];
}

// Per-batch progress samples for a client-side ETA (no backend timing needed
// for Phase A). batch_id -> [{t: ms, done: int}], capped to the recent window.
const _adlRateSamples = {};
const _ADL_RATE_WINDOW = 8;

function _adlSampleRate(batchId, done) {
  const arr = _adlRateSamples[batchId] || (_adlRateSamples[batchId] = []);
  const now = Date.now();
  const last = arr[arr.length - 1];
  if (!last || last.done !== done) arr.push({ t: now, done });
  while (arr.length > _ADL_RATE_WINDOW) arr.shift();
  // tracks/sec over the sampled window
  if (arr.length < 2) return 0;
  const first = arr[0];
  const dt = (arr[arr.length - 1].t - first.t) / 1000;
  const dd = arr[arr.length - 1].done - first.done;
  return dt > 0 && dd > 0 ? dd / dt : 0;
}

function _adlFmtDuration(sec) {
  if (!sec || sec < 0 || !isFinite(sec)) return '';
  if (sec < 60) return `${Math.round(sec)}s`;
  if (sec < 3600) return `${Math.round(sec / 60)}m`;
  return `${Math.floor(sec / 3600)}h ${Math.round((sec % 3600) / 60)}m`;
}

// ETA string for a batch's stat line. Album bundles use the downloader's own
// speed/size; track batches use the client-side completion rate.
function _adlBatchEta(batch) {
  if (batch.phase === 'album_downloading') {
    const ab = batch.album_bundle || {};
    const bits = [];
    if (ab.speed) bits.push(ab.speed);
    if (ab.downloaded && ab.size) bits.push(`${ab.downloaded} / ${ab.size}`);
    return bits.join(' · ');
  }
  if (batch.phase !== 'downloading') return '';
  const total = batch.total || 0;
  const done = (batch.completed || 0) + (batch.failed || 0);
  const remaining = total - done;
  if (remaining <= 0) return '';
  const rate = _adlSampleRate(batch.batch_id, done); // tracks/sec
  if (rate <= 0) return '';
  return `~${_adlFmtDuration(remaining / rate)} left`;
}

// Glanceable aggregate strip atop the panel: batches · downloading · queued ·
// speed · ETA. Hidden when nothing is active.
function _adlRenderBatchSummary(activeBatches) {
  const el = document.getElementById('adl-batch-summary');
  if (!el) return;
  if (!activeBatches.length) {
    el.style.display = 'none';
    return;
  }

  let downloading = 0,
    queued = 0,
    remaining = 0,
    rate = 0,
    bundleSpeed = '';
  for (const b of activeBatches) {
    downloading += b.active || 0;
    queued += b.queued || 0;
    if (b.phase === 'downloading') {
      const done = (b.completed || 0) + (b.failed || 0);
      remaining += Math.max(0, (b.total || 0) - done);
      rate += _adlSampleRate(b.batch_id, done);
    }
    if (b.phase === 'album_downloading' && b.album_bundle && b.album_bundle.speed && !bundleSpeed) {
      bundleSpeed = b.album_bundle.speed;
    }
  }

  const parts = [`${activeBatches.length} batch${activeBatches.length === 1 ? '' : 'es'}`];
  if (downloading) parts.push(`${downloading} downloading`);
  if (queued) parts.push(`${queued} queued`);
  if (bundleSpeed) parts.push(_adlEsc(bundleSpeed));
  const etaStr = rate > 0 && remaining > 0 ? `~${_adlFmtDuration(remaining / rate)} left` : '';

  el.style.display = '';
  el.innerHTML =
    `<span class="adl-batch-summary-main">${parts.join(' · ')}</span>` +
    (etaStr ? `<span class="adl-batch-summary-eta">${etaStr}</span>` : '');
}

function loadActiveDownloadsPage() {
  _verifLoadConfig();
  _adlFetch();
  _adlFetchBatchHistory();
  // Poll downloads every 2 seconds, history every 60 seconds
  if (_adlPoller) clearInterval(_adlPoller);
  _adlPoller = setInterval(() => {
    if (currentPage === 'active-downloads') _adlFetch();
    else {
      clearInterval(_adlPoller);
      _adlPoller = null;
    }
  }, 2000);
  if (_adlBatchHistoryPoller) clearInterval(_adlBatchHistoryPoller);
  _adlBatchHistoryPoller = setInterval(() => {
    if (currentPage === 'active-downloads') _adlFetchBatchHistory();
    else {
      clearInterval(_adlBatchHistoryPoller);
      _adlBatchHistoryPoller = null;
    }
  }, 60000);
}

function adlSetFilter(filter) {
  _adlFilter = filter;
  document
    .querySelectorAll('#adl-filter-pills .adl-pill')
    .forEach((p) => p.classList.toggle('active', p.dataset.filter === filter));
  _adlRender();
}

async function _adlFetch() {
  try {
    const resp = await fetch('/api/downloads/all?limit=300');
    const data = await resp.json();
    if (data.success) {
      _adlData = data.downloads || [];
      _adlBatches = data.batches || [];
      _adlRender();
      _adlRenderBatchPanel();
      // Don't call _adlUpdateBadge() here — it counts the truncated
      // 300-item local array. The WebSocket status push already
      // maintains the badge with the real server-side active count.
    }
  } catch (e) {
    console.error('Downloads page fetch error:', e);
  }
  // Refresh the quarantine panel every ~15 s (every 7 polls × 2 s) so new
  // quarantine entries created during a batch appear without a manual click.
  _adlFetchCount++;
  if (_adlFetchCount % 7 === 0) _verifLoadQuarantine(true);
}

function _adlUpdateBadge() {
  const activeCount = _adlData.filter((d) =>
    ['downloading', 'searching', 'queued', 'pending', 'post_processing'].includes(d.status),
  ).length;
  _updateDlNavBadge(activeCount);
}

function _updateDlNavBadge(count) {
  const badge = document.getElementById('dl-nav-badge');
  if (badge) {
    if (count > 0) {
      badge.textContent = count;
      badge.classList.remove('hidden');
    } else {
      badge.classList.add('hidden');
    }
  }
  const dlBtn = document.querySelector('.nav-button[data-page="active-downloads"]');
  if (dlBtn) {
    dlBtn.classList.toggle('nav-downloads-active', count > 0);
  }
}

function _adlQualityBadge(dl) {
  // Show the real audio quality of a completed download (probed from the
  // file itself — FLAC bit depth, MP3 bitrate, …), so you can see at a
  // glance what was actually fetched.
  if (dl.status !== 'completed' || !dl.quality) return '';
  return ` <span class="adl-quality-chip" title="Audio quality of the downloaded file (read from the file itself)">${_adlEsc(dl.quality)}</span>`;
}

function _adlVerifBadge(dl) {
  // Verification badge for completed downloads — how this file passed
  // verification (status comes from library_history / the live task):
  // verified = clean AcoustID pass; unverified = imported but not
  // hard-confirmed (cross-script/ambiguous/no fingerprint match);
  // force_imported = accepted as best candidate after the retry budget was
  // exhausted (version-mismatch fallback).
  if (dl.status !== 'completed') return '';
  if (dl.verification_status === 'force_imported') {
    return ' <span class="verif-badge verif-force" title="Force-imported: accepted as best available candidate after repeated mismatches (version-mismatch fallback). Library AcoustID scans report these as informational.">⚑</span>';
  }
  if (dl.verification_status === 'unverified') {
    return ' <span class="verif-badge verif-unverified" title="Imported but not hard-verified (AcoustID could not confirm — e.g. cross-script metadata or no fingerprint match).">⚠</span>';
  }
  if (dl.verification_status === 'verified') {
    return ' <span class="verif-badge verif-ok" title="AcoustID verified: audio fingerprint matches the expected track.">✔</span>';
  }
  if (dl.verification_status === 'human_verified') {
    return ' <span class="verif-badge verif-human" title="Human verified: you confirmed this file is the right track. The AcoustID scanner skips it.">🛡✔</span>';
  }
  return '';
}

// ---- Verification review queue (the ⚠ Unverified/Quarantine filter) ----

function verifHistoryId(dl) {
  // Persistent history rows carry task_id 'history-<dbid>'.
  if (dl.is_persistent_history && dl.task_id) {
    const m = String(dl.task_id).match(/^history-(\d+)$/);
    if (m) return m[1];
  }
  // Still-live completed tasks carry the library_history id directly, so the
  // review actions (play/audit/approve/delete) work before the task becomes
  // a persistent-history row — otherwise the buttons "didn't always load".
  if (dl.history_id) return String(dl.history_id);
  return null;
}

function _verifTimeAgo(iso) {
  return typeof formatHistoryTime === 'function' && iso ? formatHistoryTime(iso) : '';
}

function _verifReasonBadge(dl) {
  // Glanceable badge in the style of the library-history quarantine tab.
  if (dl.verification_status === 'force_imported') {
    return '<span class="verif-reason-badge verif-rb-force" title="Accepted as best candidate after the retry budget was exhausted (version-mismatch fallback)">FORCE-IMPORTED</span>';
  }
  if (dl.verification_status === 'unverified') {
    return '<span class="verif-reason-badge verif-rb-unv" title="AcoustID could not hard-confirm this file (ambiguous / cross-script / no fingerprint match)">ACOUSTID UNCONFIRMED</span>';
  }
  return '';
}

function _adlReviewActions(dl) {
  if (_adlFilter !== 'unverified') return '';
  const hid = verifHistoryId(dl);
  if (!hid) return '';
  const timeAgo = _verifTimeAgo(dl.created_at);
  return `<div class="verif-actions" onclick="event.stopPropagation()">
        ${_verifReasonBadge(dl)}
        ${timeAgo ? `<span class="verif-time">${timeAgo}</span>` : ''}
        <button class="verif-act verif-act-play" onclick="verifPlay('${hid}')" title="Play the downloaded file in the media player">▶</button>
        <button class="verif-act" onclick="verifCompare('${hid}', this)" title="Find this track on Soulseek/streaming sources and play it in the media player — compare against your file">⇆</button>
        <button class="verif-act" onclick="verifAudit('${hid}')" title="Open the audit trail for this download (lifecycle, embedded tags, lyrics)">🔍</button>
        <button class="verif-act verif-act-ok" onclick="verifApprove('${hid}', this)" title="Approve: mark as human-verified (tag + DB). The AcoustID scanner will skip it.">✔</button>
        <button class="verif-act verif-act-del" onclick="verifDelete('${hid}', this)" title="Wrong file: delete it from disk and remove this entry">🗑</button>
    </div>`;
}

async function verifPlay(hid) {
  // Plays the LOCAL file through the global media player (same machinery as
  // library playback) — full player UI with seek/stop instead of an
  // invisible Audio element that re-renders wiped.
  const dl = _adlData.find((d) => verifHistoryId(d) === String(hid));
  try {
    if (typeof setTrackInfo === 'function') {
      setTrackInfo({
        title: (dl && dl.title) || 'Review track',
        artist: (dl && dl.artist) || '',
        album: (dl && dl.album) || '',
        is_library: true,
        image_url: (dl && dl.artwork) || null,
      });
    }
    if (typeof showLoadingAnimation === 'function') showLoadingAnimation();
    const r = await fetch(`/api/verification/${hid}/play`, { method: 'POST' });
    const d = await r.json();
    if (!d.success) throw new Error(d.error || 'Playback failed');
    await startAudioPlayback();
  } catch (e) {
    if (typeof hideLoadingAnimation === 'function') hideLoadingAnimation();
    showToast && showToast('Playback failed: ' + e.message, 'error');
  }
}

async function verifCompare(hid, btn) {
  // Same pipeline as the /search page play button — run server-side so the
  // local file's duration guides candidate ranking (avoids e.g. 10-hour
  // YouTube loops winning the match and timing out).
  if (btn) {
    btn.disabled = true;
    btn.textContent = '…';
  }
  showToast && showToast('Searching stream for comparison…', 'info');
  try {
    const res = await fetch(`/api/verification/${hid}/compare-stream`, { method: 'POST' });
    const data = await res.json();
    if (data.success && data.result && typeof startStream === 'function') {
      await startStream(data.result);
    } else {
      showToast && showToast(data.error || 'No stream candidate found for comparison', 'error');
    }
  } catch (e) {
    showToast && showToast('Stream failed: ' + e.message, 'error');
  }
  if (btn) {
    btn.disabled = false;
    btn.textContent = '⇆';
  }
}

async function verifAudit(hid) {
  try {
    const r = await fetch(`/api/verification/${hid}/entry`);
    const d = await r.json();
    if (d.success && d.entry && typeof openDownloadAuditModal === 'function') {
      openDownloadAuditModal(d.entry);
    } else {
      showToast && showToast(d.error || 'Audit data not available', 'error');
    }
  } catch (e) {
    showToast && showToast('Audit load failed', 'error');
  }
}

async function verifApprove(hid, btn) {
  if (btn) btn.disabled = true;
  try {
    const r = await fetch(`/api/verification/${hid}/approve`, { method: 'POST' });
    const d = await r.json();
    if (d.success) {
      showToast && showToast('Marked as human-verified 🛡✔', 'success');
      _adlFetch();
    } else showToast && showToast(d.error || 'Approve failed', 'error');
  } catch (e) {
    showToast && showToast('Approve failed', 'error');
  }
  if (btn) btn.disabled = false;
}

async function verifDelete(hid, btn) {
  if (
    !(await showConfirmDialog({
      title: 'Delete Unverified File',
      message:
        'This permanently deletes the downloaded file from disk and removes the review entry. Cannot be undone.',
      confirmText: 'Delete',
      cancelText: 'Cancel',
      destructive: true,
    }))
  )
    return;
  if (btn) btn.disabled = true;
  try {
    const r = await fetch(`/api/verification/${hid}/delete`, { method: 'POST' });
    const d = await r.json();
    if (d.success) {
      showToast && showToast('File deleted', 'success');
      _adlFetch();
    } else showToast && showToast(d.error || 'Delete failed', 'error');
  } catch (e) {
    showToast && showToast('Delete failed', 'error');
  }
  if (btn) btn.disabled = false;
}

// ---- Unverified review rows (Quarantine-style cards) ----
// The Unverified sub-view used to piggyback on the generic download row and
// open a modal on click. These give it the same card design the Quarantine
// sub-view got — row click expands an inline detail panel in place — minus the
// grouping (each unverified import is its own track, nothing to group).
let _verifUnvData = [];
const _verifUnvOpenDetails = new Set();

function _verifUnvKey(dl) {
  return verifHistoryId(dl) || dl.task_id || '';
}

function _verifUnvDetailRows(dl) {
  const reason =
    dl.verification_status === 'force_imported'
      ? 'Accepted as the best available candidate after the retry budget was exhausted (version-mismatch fallback). A library AcoustID scan reports these as informational.'
      : 'AcoustID could not hard-confirm this file (ambiguous / cross-script metadata / no fingerprint match). Imported, but not verified.';
  const source = dl.batch_source || dl.batch_name || '';
  return [
    `<div><span class="verif-detail-label">Why flagged:</span> ${_adlEsc(reason)}</div>`,
    source
      ? `<div><span class="verif-detail-label">Download source:</span> ${_adlEsc(source)}</div>`
      : '',
    dl.quality
      ? `<div><span class="verif-detail-label">Quality:</span> ${_adlEsc(dl.quality)}</div>`
      : '',
    dl.file_path
      ? `<div><span class="verif-detail-label">File:</span> ${_adlEsc(dl.file_path)}</div>`
      : '',
    dl.created_at
      ? `<div><span class="verif-detail-label">Downloaded:</span> ${_adlEsc(dl.created_at)}</div>`
      : '',
  ]
    .filter(Boolean)
    .join('');
}

function _verifUnverifiedRowHtml(dl, idx) {
  const hid = verifHistoryId(dl);
  const title = _adlEsc(dl.title || 'Unknown Track');
  const meta = [_adlEsc(dl.artist || ''), _adlEsc(dl.album || '')].filter(Boolean).join(' · ');
  const source = _adlEsc(dl.batch_source || dl.batch_name || '');
  const timeAgo = _verifTimeAgo(dl.created_at);
  const artHtml = dl.artwork
    ? `<img class="adl-row-art" src="${_adlEsc(dl.artwork)}" alt="" onerror="this.style.display='none'">`
    : '<div class="adl-row-art adl-row-art-empty"></div>';
  const detailsOpen = _verifUnvOpenDetails.has(_verifUnvKey(dl));
  const actions = hid
    ? `
        <button class="verif-act verif-act-play" onclick="verifPlay('${hid}')" title="Play the downloaded file in the media player">▶</button>
        <button class="verif-act" onclick="verifCompare('${hid}', this)" title="Find this track on Soulseek/streaming sources and play it in the media player — compare against your file">⇆</button>
        <button class="verif-act" onclick="verifAudit('${hid}')" title="Open the full audit trail for this download (lifecycle, embedded tags, lyrics)">🔍</button>
        <button class="verif-act verif-act-ok" onclick="verifApprove('${hid}', this)" title="Approve: mark as human-verified (tag + DB). The AcoustID scanner will skip it.">✔</button>
        <button class="verif-act verif-act-del" onclick="verifDelete('${hid}', this)" title="Wrong file: delete it from disk and remove this entry">🗑</button>`
    : '';
  return `<div class="adl-row adl-row-completed verif-quar-row" data-task-id="${_adlEsc(dl.task_id || '')}" onclick="verifUnvInspect(${idx})" title="Click to show/hide details (why it was flagged, source, quality, file)">
        ${artHtml}
        <div class="adl-row-info">
            <div class="adl-row-title">${title}</div>
            ${meta ? `<div class="adl-row-meta">${meta}</div>` : ''}
            ${source ? `<div class="adl-row-batch">${source}</div>` : ''}
            <div class="verif-quar-details" id="verif-unv-details-${idx}" style="display:${detailsOpen ? '' : 'none'}">${_verifUnvDetailRows(dl)}</div>
        </div>
        <div class="verif-actions" onclick="event.stopPropagation()">
            ${_verifReasonBadge(dl)}
            ${dl.quality ? `<span class="adl-quality-chip" title="Audio quality of the downloaded file (read from the file itself)">${_adlEsc(dl.quality)}</span>` : ''}
            ${timeAgo ? `<span class="verif-time">${timeAgo}</span>` : ''}
            ${actions}
        </div>
    </div>`;
}

function _verifUnverifiedRows(items) {
  _verifUnvData = items;
  if (!items.length) return '';
  return items.map((dl, idx) => _verifUnverifiedRowHtml(dl, idx)).join('');
}

function verifUnvInspect(idx) {
  // Open-state lives in a Set keyed by the row's stable id (not the DOM) so it
  // survives the 2 s polling re-render — same pattern as verifQuarInspect.
  const dl = _verifUnvData[idx];
  if (!dl) return;
  const key = _verifUnvKey(dl);
  if (_verifUnvOpenDetails.has(key)) _verifUnvOpenDetails.delete(key);
  else _verifUnvOpenDetails.add(key);
  const el = document.getElementById(`verif-unv-details-${idx}`);
  if (el) el.style.display = _verifUnvOpenDetails.has(key) ? '' : 'none';
}

// ---- Quarantine sub-view inside the ⚠ filter ----
// The review queue covers BOTH kinds of suspect files: imported-but-
// unconfirmed (unverified/force_imported) and not-imported-at-all
// (quarantined). One place to listen, compare, approve or delete.
let _verifSubView = 'unverified';
let _verifQuarEntries = [];
let _verifQuarLoaded = false;
let _verifQuarLoading = false;
// Expanded 🔍 detail panels, keyed by quarantine entry id — survives the
// polling re-render (which rebuilds the rows every few seconds).
const _verifQuarOpenDetails = new Set();
const _verifQuarOpenGroups = new Set(); // group keys whose alt-members are expanded
// null = not fetched yet (assume enabled). Without an AcoustID API key
// nothing ever gets a verification status, so the review queue collapses
// to quarantine-only.
let _verifAcoustidEnabled = null;
let _verifConfigLoading = false;

async function _verifLoadConfig() {
  if (_verifAcoustidEnabled !== null || _verifConfigLoading) return;
  _verifConfigLoading = true;
  try {
    const r = await fetch('/api/verification/config');
    const d = await r.json();
    _verifAcoustidEnabled = !!(d && d.acoustid_enabled);
    // When require_verified is on, nothing ever lands in the library as
    // "unverified" — unconfirmed tracks go straight to quarantine instead.
    // Collapse the sub-view to quarantine-only just like the no-AcoustID case.
    if (_verifAcoustidEnabled && d && d.require_verified) _verifAcoustidEnabled = false;
  } catch (e) {
    _verifAcoustidEnabled = true;
  }
  _verifConfigLoading = false;
  if (_verifAcoustidEnabled === false) {
    _verifSubView = 'quarantine';
    const pill = document.querySelector('.adl-pill[data-filter="unverified"]');
    if (pill) {
      pill.textContent = '🛡 Quarantine';
      pill.title =
        'Files that failed import checks and were NOT imported. (AcoustID is not configured or require-verified is on, so there is no unverified review queue.)';
    }
    if (_adlFilter === 'unverified') {
      _verifLoadQuarantine(true);
      _adlRender();
    }
  }
}

function verifSetSubView(v) {
  if (_verifAcoustidEnabled === false) v = 'quarantine';
  _verifSubView = v === 'quarantine' ? 'quarantine' : 'unverified';
  if (_verifSubView === 'quarantine') _verifLoadQuarantine(true);
  _adlRender();
}

async function _verifLoadQuarantine(force) {
  if (_verifQuarLoading || (_verifQuarLoaded && !force)) return;
  _verifQuarLoading = true;
  try {
    const r = await fetch('/api/quarantine/list');
    const d = await r.json();
    _verifQuarEntries = d.success && Array.isArray(d.entries) ? d.entries : [];
  } catch (e) {
    _verifQuarEntries = [];
  }
  _verifQuarLoaded = true;
  _verifQuarLoading = false;
  if (_adlFilter === 'unverified') _adlRender();
}

const _VERIF_QUAR_TRIGGERS = {
  integrity: ['DURATION / INTEGRITY', 'verif-rb-int'],
  acoustid: ['ACOUSTID MISMATCH', 'verif-rb-force'],
  acoustid_unverified: ['ACOUSTID UNVERIFIED', 'verif-rb-unv'],
  bit_depth: ['BIT DEPTH FILTER', 'verif-rb-int'],
};

// Streaming sources carry their service name in source_username; a Soulseek
// download carries the uploader's peer name instead — collapse that to
// 'soulseek' so the label matches the Completed view's download-source line.
const _VERIF_QUAR_STREAMING_SOURCES = [
  'youtube',
  'tidal',
  'qobuz',
  'hifi',
  'deezer_dl',
  'lidarr',
  'soundcloud',
  'amazon',
  'torrent',
  'usenet',
];

function _verifQuarSourceLabel(q) {
  const u = String(q.source_username || '').toLowerCase();
  if (_VERIF_QUAR_STREAMING_SOURCES.includes(u)) return _adlSourceLabel(u);
  return q.source_username ? _adlSourceLabel('soulseek') : '';
}

function _verifQuarRowHtml(q, idx, extraAction = '') {
  const title = _adlEsc(q.expected_track || q.original_filename || q.filename || 'Unknown file');
  const meta = [_adlEsc(q.expected_artist || ''), _adlEsc(q.original_filename || '')]
    .filter(Boolean)
    .join(' — ');
  const sourceLabel = _verifQuarSourceLabel(q);
  const [trigLabel, trigClass] = _VERIF_QUAR_TRIGGERS[q.trigger] || ['QUARANTINED', 'verif-rb-unv'];
  const timeAgo = _verifTimeAgo(q.timestamp);
  const approveBtn = q.has_full_context
    ? `<button class="verif-act verif-act-ok" onclick="verifQuarApprove(${idx}, this)" title="Approve: re-import this exact file into the library, marked human-verified">✔</button>`
    : `<button class="verif-act verif-act-ok" onclick="verifQuarRecover(${idx}, this)" title="Recover to Staging for a manual import (legacy entry without embedded context)">⤴</button>`;
  const details = [
    q.reason
      ? `<div><span class="verif-detail-label">Reason:</span> ${_adlEsc(q.reason)}</div>`
      : '',
    q.source_username
      ? `<div><span class="verif-detail-label">Source uploader:</span> ${_adlEsc(q.source_username)}</div>`
      : '',
    q.source_filename
      ? `<div><span class="verif-detail-label">Original Soulseek file:</span> ${_adlEsc(q.source_filename)}</div>`
      : '',
    q.timestamp
      ? `<div><span class="verif-detail-label">Quarantined:</span> ${_adlEsc(q.timestamp)}</div>`
      : '',
  ]
    .filter(Boolean)
    .join('');
  const detailsOpen = _verifQuarOpenDetails.has(q.id);
  const artHtml = q.thumb_url
    ? `<img class="adl-row-art" src="${_adlEsc(q.thumb_url)}" alt="" onerror="this.style.display='none'">`
    : '<div class="adl-row-art adl-row-art-empty"></div>';
  return `<div class="adl-row adl-row-failed verif-quar-row" data-quarantine-id="${_adlEsc(q.id)}" onclick="verifQuarInspect(${idx})" title="Click to show/hide details (reason, source uploader, original filename)">
        ${artHtml}
        <div class="adl-row-info">
            <div class="adl-row-title">${title}</div>
            ${meta ? `<div class="adl-row-meta">${meta}</div>` : ''}
            ${sourceLabel ? `<div class="adl-row-batch">${sourceLabel}</div>` : ''}
            <div class="verif-quar-details" id="verif-quar-details-${idx}" style="display:${detailsOpen ? '' : 'none'}">${details || 'No further details in the sidecar.'}</div>
        </div>
        <div class="verif-actions" onclick="event.stopPropagation()">
            <span class="verif-reason-badge ${trigClass}" title="${_adlEsc(q.reason || '')}">${trigLabel}</span>
            ${q.quality ? `<span class="adl-quality-chip" title="Audio quality of the quarantined file (read from the file itself)">${_adlEsc(q.quality)}</span>` : ''}
            ${timeAgo ? `<span class="verif-time">${timeAgo}</span>` : ''}
            <button class="verif-act verif-act-play" onclick="verifQuarPlay(${idx})" title="Play the quarantined file in the media player">▶</button>
            <button class="verif-act" onclick="verifQuarCompare(${idx}, this)" title="Find the expected track on Soulseek/streaming sources and play it in the media player — compare against the quarantined file">⇆</button>
            <button class="verif-act" onclick="verifQuarAudit(${idx})" title="Open the audit trail for this quarantined file (details, embedded tags, lyrics)">🔍</button>
            ${approveBtn}
            <button class="verif-act verif-act-del" onclick="verifQuarDelete(${idx}, this)" title="Delete the quarantined file permanently">🗑</button>
        </div>
        <div class="verif-quar-alt-slot" onclick="event.stopPropagation()">${extraAction}</div>
    </div>`;
}

function _verifQuarToggleGroup(btn) {
  const key = btn.dataset.groupKey;
  const open = !_verifQuarOpenGroups.has(key);
  if (open) _verifQuarOpenGroups.add(key);
  else _verifQuarOpenGroups.delete(key);
  const wrapper = btn.closest('.verif-quar-alt-wrapper');
  if (wrapper) wrapper.querySelector('.verif-quar-alt-members')?.classList.toggle('vqg-open', open);
  btn.classList.toggle('open', open);
  btn.textContent = open ? `▴ ${btn.dataset.altCount} more` : `▾ ${btn.dataset.altCount} more`;
}

function _verifQuarRows() {
  if (!_verifQuarLoaded) return '<div class="adl-section-header">Loading quarantine…</div>';
  if (!_verifQuarEntries.length) return '';
  const idxById = new Map(_verifQuarEntries.map((q, i) => [q.id, i]));
  const groups =
    typeof _groupQuarantineEntries === 'function'
      ? _groupQuarantineEntries(_verifQuarEntries)
      : _verifQuarEntries.map((q) => ({ key: null, members: [q] }));
  let html = '';
  for (const group of groups) {
    if (group.members.length === 1) {
      html += _verifQuarRowHtml(group.members[0], idxById.get(group.members[0].id));
    } else {
      // First member shown as normal row; rest hidden under a toggle button.
      const first = group.members[0];
      const firstIdx = idxById.get(first.id);
      const altCount = group.members.length - 1;
      const groupKey = group.key || first.id;
      const isOpen = _verifQuarOpenGroups.has(groupKey);
      const altBtn = `<button class="verif-quar-alt-btn${isOpen ? ' open' : ''}" data-group-key="${_adlEsc(groupKey)}" data-alt-count="${altCount}" onclick="_verifQuarToggleGroup(this)" title="Show ${altCount} more alternative candidate${altCount === 1 ? '' : 's'} for this track">${isOpen ? '▴' : '▾'} ${altCount} more</button>`;
      html += `<div class="verif-quar-alt-wrapper">`;
      html += _verifQuarRowHtml(first, firstIdx, altBtn);
      html += `<div class="verif-quar-alt-members${isOpen ? ' vqg-open' : ''}">`;
      for (let i = 1; i < group.members.length; i++) {
        html += _verifQuarRowHtml(group.members[i], idxById.get(group.members[i].id));
      }
      html += `</div></div>`;
    }
  }
  return html;
}

function verifQuarInspect(idx) {
  // Open-state lives in a Set keyed by entry id (not the DOM) — the polling
  // re-render rebuilds the rows every few seconds and would collapse a
  // DOM-only toggle right after the click.
  const q = _verifQuarEntries[idx];
  if (!q) return;
  if (_verifQuarOpenDetails.has(q.id)) _verifQuarOpenDetails.delete(q.id);
  else _verifQuarOpenDetails.add(q.id);
  const el = document.getElementById(`verif-quar-details-${idx}`);
  if (el) el.style.display = _verifQuarOpenDetails.has(q.id) ? '' : 'none';
}

async function verifQuarAudit(idx) {
  // Same Audit Trail modal as the unverified rows — the backend synthesizes
  // a history-shaped entry from the quarantine sidecar (these files were
  // never imported, so no real history row exists).
  const q = _verifQuarEntries[idx];
  if (!q) return;
  try {
    const r = await fetch(`/api/quarantine/${encodeURIComponent(q.id)}/entry`);
    const d = await r.json();
    if (d.success && d.entry && typeof openDownloadAuditModal === 'function') {
      openDownloadAuditModal(d.entry);
    } else {
      showToast && showToast(d.error || 'Audit data not available', 'error');
    }
  } catch (e) {
    showToast && showToast('Audit load failed', 'error');
  }
}

async function verifQuarPlay(idx) {
  const q = _verifQuarEntries[idx];
  if (!q) return;
  try {
    if (typeof setTrackInfo === 'function') {
      setTrackInfo({
        title: `${q.expected_track || q.original_filename || 'Quarantined file'} (quarantined)`,
        artist: q.expected_artist || '',
        album: '',
        is_library: true,
      });
    }
    if (typeof showLoadingAnimation === 'function') showLoadingAnimation();
    const r = await fetch(`/api/quarantine/${encodeURIComponent(q.id)}/play`, { method: 'POST' });
    const d = await r.json();
    if (!d.success) throw new Error(d.error || 'Playback failed');
    await startAudioPlayback();
  } catch (e) {
    if (typeof hideLoadingAnimation === 'function') hideLoadingAnimation();
    showToast && showToast('Playback failed: ' + e.message, 'error');
  }
}

async function verifQuarCompare(idx, btn) {
  const q = _verifQuarEntries[idx];
  if (!q) return;
  if (btn) {
    btn.disabled = true;
    btn.textContent = '…';
  }
  showToast && showToast(`Searching stream for "${q.expected_track || ''}"…`, 'info');
  try {
    const res = await fetch(`/api/quarantine/${encodeURIComponent(q.id)}/compare-stream`, {
      method: 'POST',
    });
    const data = await res.json();
    if (data.success && data.result && typeof startStream === 'function') {
      await startStream(data.result);
    } else {
      showToast && showToast(data.error || 'No stream candidate found for comparison', 'error');
    }
  } catch (e) {
    showToast && showToast('Stream failed: ' + e.message, 'error');
  }
  if (btn) {
    btn.disabled = false;
    btn.textContent = '⇆';
  }
}

async function verifQuarApprove(idx, btn) {
  const q = _verifQuarEntries[idx];
  if (!q) return;
  if (btn) btn.disabled = true;
  try {
    const r = await fetch(`/api/quarantine/${encodeURIComponent(q.id)}/approve`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ remove_siblings: true }),
    });
    const d = await r.json();
    if (d.success) {
      const extra =
        d.removed_siblings && d.removed_siblings.length
          ? ` (${d.removed_siblings.length} duplicate candidate${d.removed_siblings.length > 1 ? 's' : ''} removed)`
          : '';
      showToast &&
        showToast(`Approved — re-importing, will be marked human-verified 🛡✔${extra}`, 'success');
      _verifLoadQuarantine(true);
    } else {
      showToast && showToast(d.error || 'Approve failed', 'error');
    }
  } catch (e) {
    showToast && showToast('Approve failed', 'error');
  }
  if (btn) btn.disabled = false;
}

async function verifQuarRecover(idx, btn) {
  const q = _verifQuarEntries[idx];
  if (!q) return;
  if (btn) btn.disabled = true;
  try {
    const r = await fetch(`/api/quarantine/${encodeURIComponent(q.id)}/recover`, {
      method: 'POST',
    });
    const d = await r.json();
    if (d.success) {
      showToast && showToast('Moved to Staging — finish via the Import page', 'success');
      _verifLoadQuarantine(true);
    } else {
      showToast && showToast(d.error || 'Recover failed', 'error');
    }
  } catch (e) {
    showToast && showToast('Recover failed', 'error');
  }
  if (btn) btn.disabled = false;
}

async function verifQuarDelete(idx, btn) {
  const q = _verifQuarEntries[idx];
  if (!q) return;
  if (
    !(await showConfirmDialog({
      title: 'Delete Quarantined File',
      message: 'This permanently removes the file and its metadata sidecar. Cannot be undone.',
      confirmText: 'Delete',
      cancelText: 'Cancel',
      destructive: true,
    }))
  )
    return;
  if (btn) btn.disabled = true;
  try {
    const r = await fetch(`/api/quarantine/${encodeURIComponent(q.id)}`, { method: 'DELETE' });
    const d = await r.json();
    if (d.success) {
      showToast && showToast('Quarantined file deleted', 'success');
      _verifLoadQuarantine(true);
    } else showToast && showToast(d.error || 'Delete failed', 'error');
  } catch (e) {
    showToast && showToast('Delete failed', 'error');
  }
  if (btn) btn.disabled = false;
}

function _verifUnverifiedIds() {
  const done = ['completed', 'skipped', 'already_owned'];
  return _adlData
    .filter(
      (d) =>
        done.includes(d.status) &&
        (d.verification_status === 'unverified' || d.verification_status === 'force_imported'),
    )
    .map((d) => verifHistoryId(d))
    .filter(Boolean);
}

async function verifApproveAll(btn) {
  const ids = _verifUnverifiedIds();
  if (!ids.length) return;
  if (
    !(await showConfirmDialog({
      title: 'Approve Unverified Files',
      message: `Mark all ${ids.length} unverified entries as human-verified? The AcoustID scanner will skip them from now on.`,
      confirmText: 'Approve All',
      cancelText: 'Cancel',
    }))
  )
    return;
  if (btn) btn.disabled = true;
  let ok = 0;
  for (const id of ids) {
    try {
      const r = await fetch(`/api/verification/${id}/approve`, { method: 'POST' });
      const d = await r.json();
      if (d.success) ok++;
    } catch (e) {}
  }
  showToast &&
    showToast(`Approved ${ok}/${ids.length} entries 🛡✔`, ok === ids.length ? 'success' : 'error');
  if (btn) btn.disabled = false;
  _adlFetch();
}

async function verifDeleteAll(btn) {
  const ids = _verifUnverifiedIds();
  if (!ids.length) return;
  if (
    !(await showConfirmDialog({
      title: 'Delete Unverified Files',
      message: `This permanently deletes all ${ids.length} unverified files from disk and removes their review entries. Cannot be undone.`,
      confirmText: 'Delete All',
      cancelText: 'Cancel',
      destructive: true,
    }))
  )
    return;
  if (btn) btn.disabled = true;
  let ok = 0;
  for (const id of ids) {
    try {
      const r = await fetch(`/api/verification/${id}/delete`, { method: 'POST' });
      const d = await r.json();
      if (d.success) ok++;
    } catch (e) {}
  }
  showToast &&
    showToast(`Deleted ${ok}/${ids.length} files`, ok === ids.length ? 'success' : 'error');
  if (btn) btn.disabled = false;
  _adlFetch();
}

async function verifCleanOrphans(btn) {
  // Removes review entries whose file is GONE (deleted / replaced / re-downloaded
  // elsewhere) — dead log rows that can never be healed. Server-side it does a
  // filesystem check and refuses if the whole library looks offline. Removes log
  // rows only, never a file.
  if (
    !(await showConfirmDialog({
      title: 'Clean orphaned entries',
      message:
        'Remove review entries whose file no longer exists on disk (deleted, replaced, or re-downloaded elsewhere)? This removes only the stale log rows — it never deletes a file. It checks the filesystem first and refuses if your library looks offline.',
      confirmText: 'Clean up',
      cancelText: 'Cancel',
    }))
  )
    return;
  if (btn) btn.disabled = true;
  try {
    const r = await fetch('/api/verification/clean-orphans', { method: 'POST' });
    const d = await r.json();
    if (d.success) {
      showToast &&
        showToast(
          `Removed ${d.removed} orphaned entr${d.removed === 1 ? 'y' : 'ies'} (checked ${d.checked})`,
          'success',
        );
      _adlFetch();
    } else {
      showToast && showToast(d.error || 'Clean-up failed', 'error');
    }
  } catch (e) {
    showToast && showToast('Clean-up failed', 'error');
  }
  if (btn) btn.disabled = false;
}

async function verifQuarApproveAll(btn) {
  const entries = _verifQuarEntries.filter((q) => q.has_full_context);
  if (!entries.length) {
    showToast &&
      showToast('No one-click-approvable entries (legacy sidecars need Recover)', 'info');
    return;
  }
  if (
    !(await showConfirmDialog({
      title: 'Approve Quarantined Files',
      message: `Approve and re-import all ${entries.length} quarantined files? They will be imported into the library marked human-verified.`,
      confirmText: 'Approve & Import All',
      cancelText: 'Cancel',
    }))
  )
    return;
  if (btn) btn.disabled = true;
  let ok = 0;
  for (const q of entries) {
    try {
      const r = await fetch(`/api/quarantine/${encodeURIComponent(q.id)}/approve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ remove_siblings: true }),
      });
      const d = await r.json();
      if (d.success) ok++;
    } catch (e) {}
    // Each approve spawns a server-side re-import thread — stagger them.
    await new Promise((res) => setTimeout(res, 500));
  }
  showToast &&
    showToast(
      `Approved ${ok}/${entries.length} — re-importing as human-verified`,
      ok === entries.length ? 'success' : 'error',
    );
  if (btn) btn.disabled = false;
  _verifLoadQuarantine(true);
}

async function verifQuarClearAll(btn) {
  if (!_verifQuarEntries.length) return;
  if (
    !(await showConfirmDialog({
      title: 'Clear Quarantine',
      message: `This permanently deletes all ${_verifQuarEntries.length} quarantined files and their metadata sidecars. Cannot be undone.`,
      confirmText: 'Delete All',
      cancelText: 'Cancel',
      destructive: true,
    }))
  )
    return;
  if (btn) btn.disabled = true;
  try {
    const r = await fetch('/api/quarantine/clear', { method: 'POST' });
    const d = await r.json();
    if (d.success) showToast && showToast('Quarantine cleared', 'success');
    else showToast && showToast(d.error || 'Clear failed', 'error');
  } catch (e) {
    showToast && showToast('Clear failed', 'error');
  }
  if (btn) btn.disabled = false;
  _verifLoadQuarantine(true);
}

window.verifPlay = verifPlay;
window.verifApprove = verifApprove;
window.verifDelete = verifDelete;
window.verifCompare = verifCompare;
window.verifAudit = verifAudit;
window.verifSetSubView = verifSetSubView;
window.verifApproveAll = verifApproveAll;
window.verifDeleteAll = verifDeleteAll;
window.verifQuarPlay = verifQuarPlay;
window.verifQuarCompare = verifQuarCompare;
window.verifQuarInspect = verifQuarInspect;
window.verifQuarAudit = verifQuarAudit;
window.verifQuarApprove = verifQuarApprove;
window.verifQuarRecover = verifQuarRecover;
window.verifQuarDelete = verifQuarDelete;
window.verifQuarApproveAll = verifQuarApproveAll;
window.verifQuarClearAll = verifQuarClearAll;

function _adlRender() {
  const list = document.getElementById('adl-list');
  const empty = document.getElementById('adl-empty');
  const countEl = document.getElementById('adl-count');
  if (!list) return;

  // Apply filter
  const activeStatuses = ['downloading', 'searching', 'post_processing'];
  const queuedStatuses = ['queued'];
  const completedStatuses = ['completed', 'skipped', 'already_owned'];
  const failedStatuses = ['failed', 'not_found', 'cancelled'];

  let filtered = _adlData;

  // Batch filter: if a batch card is selected, narrow to that batch first
  if (_adlFilterBatchId) {
    filtered = filtered.filter((d) => d.batch_id === _adlFilterBatchId);
  }

  if (_adlFilter === 'active') filtered = filtered.filter((d) => activeStatuses.includes(d.status));
  else if (_adlFilter === 'queued')
    filtered = filtered.filter((d) => queuedStatuses.includes(d.status));
  else if (_adlFilter === 'completed')
    filtered = filtered.filter((d) => completedStatuses.includes(d.status));
  else if (_adlFilter === 'unverified')
    filtered = filtered.filter(
      (d) =>
        completedStatuses.includes(d.status) &&
        (d.verification_status === 'unverified' || d.verification_status === 'force_imported'),
    );
  // (review banner injected below when this filter is active)
  else if (_adlFilter === 'failed')
    filtered = filtered.filter((d) => failedStatuses.includes(d.status));

  // Clear-completed clears live completed tasks AND the persisted download-history
  // tail (including unverified review-queue rows), so count every completed/failed
  // row — otherwise the button vanishes after a restart when the list is all
  // persisted completed rows.
  const completedN = _adlData.filter((d) =>
    [...completedStatuses, ...failedStatuses].includes(d.status),
  ).length;

  if (countEl) {
    const activeN = _adlData.filter((d) => activeStatuses.includes(d.status)).length;
    const queuedN = _adlData.filter((d) => queuedStatuses.includes(d.status)).length;
    const total = _adlData.length;
    const parts = [];
    if (activeN > 0) parts.push(`${activeN} active`);
    if (queuedN > 0) parts.push(`${queuedN} queued`);
    parts.push(`${total} total`);
    countEl.textContent = parts.join(' / ');
  }

  // Show/hide clear button
  const clearBtn = document.getElementById('adl-clear-btn');
  if (clearBtn) clearBtn.style.display = completedN > 0 ? '' : 'none';

  // Show/hide cancel-all button — only visible when there's something to cancel
  const cancelAllBtn = document.getElementById('adl-cancel-all-btn');
  if (cancelAllBtn) {
    const hasRunningWork = _adlData.some((d) =>
      [...activeStatuses, ...queuedStatuses].includes(d.status),
    );
    cancelAllBtn.style.display = hasRunningWork ? '' : 'none';
  }

  // Batch filter indicator banner
  let existingBanner = document.getElementById('adl-batch-filter-banner');
  if (_adlFilterBatchId) {
    const batchInfo = _adlBatches.find((b) => b.batch_id === _adlFilterBatchId);
    const batchName = batchInfo ? batchInfo.batch_name : 'Unknown batch';
    const colorIdx = _getBatchColor(_adlFilterBatchId);
    const colorDot =
      colorIdx >= 0
        ? `<span class="adl-filter-banner-dot" style="background:rgba(var(--batch-color-${colorIdx}),0.7)"></span>`
        : '';
    if (!existingBanner) {
      existingBanner = document.createElement('div');
      existingBanner.id = 'adl-batch-filter-banner';
      existingBanner.className = 'adl-batch-filter-banner';
      list.parentNode.insertBefore(existingBanner, list);
    }
    existingBanner.innerHTML = `${colorDot}Showing: <strong>${_adlEsc(batchName)}</strong> <button class="adl-filter-banner-clear" onclick="_adlFilterByBatch('${_adlFilterBatchId}')">Clear filter</button>`;
    existingBanner.style.display = '';
  } else if (existingBanner) {
    existingBanner.style.display = 'none';
  }

  // Review queue sub-view toggle: unverified imports ⇄ quarantined files.
  let verifBanner = document.getElementById('verif-subview-banner');
  if (_adlFilter === 'unverified') {
    _verifLoadConfig(); // no-op once fetched
    if (!verifBanner) {
      verifBanner = document.createElement('div');
      verifBanner.id = 'verif-subview-banner';
      verifBanner.className = 'adl-batch-filter-banner';
      list.parentNode.insertBefore(verifBanner, list);
    }
    const quarCount = _verifQuarLoaded ? ` (${_verifQuarEntries.length})` : '';
    const bulkBtns =
      _verifSubView === 'quarantine'
        ? `<button class="adl-filter-banner-clear" onclick="verifQuarApproveAll(this)" title="Approve + re-import every quarantined file (marked human-verified)">✔ Approve all</button>
               <button class="adl-filter-banner-clear verif-bulk-danger" onclick="verifQuarClearAll(this)" title="Permanently delete every quarantined file">🗑 Clear all</button>`
        : `<button class="adl-filter-banner-clear" onclick="verifApproveAll(this)" title="Mark every listed entry as human-verified">✔ Approve all</button>
               <button class="adl-filter-banner-clear" onclick="verifCleanOrphans(this)" title="Remove dead entries whose file no longer exists (deleted / replaced). Removes log rows only — never a file.">🧹 Clean orphaned</button>
               <button class="adl-filter-banner-clear verif-bulk-danger" onclick="verifDeleteAll(this)" title="Delete every listed file from disk and remove its entry">🗑 Delete all</button>`;
    // Without an AcoustID key nothing ever gets a verification status —
    // hide the pointless Unverified pill and show quarantine only.
    const unvPill =
      _verifAcoustidEnabled === false
        ? ''
        : `<button class="adl-pill${_verifSubView === 'unverified' ? ' active' : ''}" onclick="verifSetSubView('unverified')" title="Imported files that AcoustID could not hard-confirm">⚠ Unverified (${filtered.length})</button>`;
    verifBanner.innerHTML = `
            ${unvPill}
            <button class="adl-pill${_verifSubView === 'quarantine' ? ' active' : ''}" onclick="verifSetSubView('quarantine')" title="Files that failed verification and were NOT imported">🛡 Quarantine${quarCount}</button>
            <span class="verif-banner-spacer"></span>
            ${bulkBtns}`;
    verifBanner.style.display = '';
    if (!_verifQuarLoaded) _verifLoadQuarantine(false); // count for the pill
  } else if (verifBanner) {
    verifBanner.style.display = 'none';
  }

  if (_adlFilter === 'unverified' && _verifSubView === 'quarantine') {
    const qhtml = _verifQuarRows();
    const qEmptyEl = document.getElementById('adl-empty');
    const qEmptyHtml = qEmptyEl ? qEmptyEl.outerHTML : '';
    list.innerHTML = qEmptyHtml + qhtml;
    const qNewEmpty = document.getElementById('adl-empty');
    if (qNewEmpty) qNewEmpty.style.display = qhtml ? 'none' : '';
    return;
  }

  // Unverified review sub-view: render the Quarantine-style cards (inline
  // expandable details), not the generic download rows.
  if (_adlFilter === 'unverified' && _verifSubView === 'unverified') {
    const uhtml = _verifUnverifiedRows(filtered);
    const uEmptyEl = document.getElementById('adl-empty');
    const uEmptyHtml = uEmptyEl ? uEmptyEl.outerHTML : '';
    list.innerHTML = uEmptyHtml + uhtml;
    const uNewEmpty = document.getElementById('adl-empty');
    if (uNewEmpty) uNewEmpty.style.display = uhtml ? 'none' : '';
    return;
  }

  if (filtered.length === 0) {
    if (empty) empty.style.display = '';
    // Clear any existing rows but keep the empty message
    list.querySelectorAll('.adl-row').forEach((r) => r.remove());
    return;
  }

  if (empty) empty.style.display = 'none';

  // Group by status category for section headers
  const groups = { active: [], queued: [], completed: [], failed: [] };
  for (const dl of filtered) {
    const cls = _adlStatusClass(dl.status);
    if (cls === 'active') groups.active.push(dl);
    else if (cls === 'queued') groups.queued.push(dl);
    else if (cls === 'completed') groups.completed.push(dl);
    else groups.failed.push(dl);
  }

  let html = '';
  const sections = [
    { key: 'active', label: 'Active', items: groups.active },
    { key: 'queued', label: 'Queued', items: groups.queued },
    { key: 'completed', label: 'Completed', items: groups.completed },
    { key: 'failed', label: 'Failed', items: groups.failed },
  ];

  for (const section of sections) {
    if (section.items.length === 0) continue;
    // Only show section headers in "all" filter mode
    if (_adlFilter === 'all') {
      html += `<div class="adl-section-header">${section.label} (${section.items.length})</div>`;
    }
    for (const dl of section.items) {
      const statusClass = _adlStatusClass(dl.status);
      const statusLabel = _adlStatusLabel(dl.status);
      // (verification badge appended next to the label via _adlVerifBadge)
      const title = _adlEsc(dl.title || 'Unknown Track');
      const artist = _adlEsc(dl.artist || '');
      const album = _adlEsc(dl.album || '');
      const batchName = _adlEsc(dl.batch_name || '');
      const error = dl.error ? _adlEsc(dl.error) : '';

      const meta = [artist, album].filter(Boolean).join(' \u00B7 ');
      const artHtml = dl.artwork
        ? `<img class="adl-row-art" src="${_adlEsc(dl.artwork)}" alt="" onerror="this.style.display='none'">`
        : '<div class="adl-row-art adl-row-art-empty"></div>';

      // Track position: "3 of 19"
      const posText = dl.batch_total > 1 ? `${(dl.track_index || 0) + 1} of ${dl.batch_total}` : '';

      const colorIdx = _getBatchColor(dl.batch_id);
      const colorBar =
        colorIdx >= 0
          ? `<div class="adl-row-batch-color" style="background:rgba(var(--batch-color-${colorIdx}),0.6)"></div>`
          : '';

      // Per-row cancel only makes sense for in-flight tasks. Terminal
      // states (completed/failed/cancelled) have nothing to cancel.
      const isCancellable = statusClass === 'active' || statusClass === 'queued';
      const cancelBtnHtml =
        isCancellable && dl.playlist_id && dl.track_index !== undefined
          ? `<button class="adl-row-cancel" onclick="event.stopPropagation(); adlCancelRow(this, '${_adlEsc(dl.playlist_id)}', ${dl.track_index})" title="Cancel this download" aria-label="Cancel download">
                       <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                   </button>`
          : '';

      // In the Unverified review sub-view, make the whole row clickable to
      // open the audit/info modal (same as the 🔍 button) — mirrors the
      // Quarantine rows, which are row-clickable for their details. The
      // action buttons stopPropagation so they don't double-trigger.
      const _unvHid = _adlFilter === 'unverified' ? verifHistoryId(dl) : null;
      const reviewRowClick = _unvHid
        ? ` onclick="verifAudit('${_unvHid}')" style="cursor:pointer" title="Click to show download details (audit trail, embedded tags, lyrics)"`
        : '';

      html += `<div class="adl-row adl-row-${statusClass}" data-task-id="${dl.task_id}" data-batch-id="${dl.batch_id || ''}"${reviewRowClick}>
                ${colorBar}
                ${artHtml}
                <div class="adl-row-info">
                    <div class="adl-row-title">${title}</div>
                    ${meta ? `<div class="adl-row-meta">${meta}</div>` : ''}
                    ${batchName ? `<div class="adl-row-batch">${batchName}${posText ? ' &middot; Track ' + posText : ''}</div>` : ''}
                    ${error ? `<div class="adl-row-error">${error}</div>` : ''}
                </div>
                <div class="adl-row-status ${statusClass}">
                    <span class="adl-status-dot ${statusClass}"></span>
                    ${statusLabel}${_adlVerifBadge(dl)}${_adlQualityBadge(dl)}${dl.retry_info && (statusClass === 'active' || statusClass === 'queued') ? ` <span class="adl-retry-info" title="Retry engine: trying the next-best candidate (attempt ${_adlEsc(String(dl.retry_info))}${dl.retry_trigger ? ' — previous candidate ' + (['acoustid', 'acoustid_unverified'].includes(dl.retry_trigger) ? 'quarantined (AcoustID)' : 'triggered by ' + _adlEsc(dl.retry_trigger)) : ''})">🔁 ${_adlEsc(String(dl.retry_info))}${['acoustid', 'acoustid_unverified'].includes(dl.retry_trigger) ? ' 🛡' : ''}</span>` : ''}
                </div>
                ${_adlReviewActions(dl)}
                ${cancelBtnHtml}
            </div>`;
    }
  }

  // Preserve empty element, inject rows
  const emptyEl = document.getElementById('adl-empty');
  const emptyHtml = emptyEl ? emptyEl.outerHTML : '';
  list.innerHTML = emptyHtml + html;
  const newEmpty = document.getElementById('adl-empty');
  if (newEmpty) newEmpty.style.display = filtered.length > 0 ? 'none' : '';
}

function _adlStatusClass(status) {
  switch (status) {
    case 'downloading':
    case 'searching':
    case 'post_processing':
      return 'active';
    case 'queued':
    case 'pending':
      return 'queued';
    case 'completed':
    case 'skipped':
    case 'already_owned':
      return 'completed';
    case 'failed':
    case 'not_found':
      return 'failed';
    case 'cancelled':
      return 'cancelled';
    default:
      return 'queued';
  }
}

function _adlStatusLabel(status) {
  switch (status) {
    case 'downloading':
      return '<span class="adl-spinner"></span>Downloading';
    case 'searching':
      return '<span class="adl-spinner"></span>Searching';
    case 'post_processing':
      return '<span class="adl-spinner"></span>Processing';
    case 'queued':
    case 'pending':
      return 'Queued';
    case 'completed':
      return 'Completed';
    case 'skipped':
      return 'Skipped';
    case 'already_owned':
      return 'Owned';
    case 'failed':
      return 'Failed';
    case 'not_found':
      return 'Not Found';
    case 'cancelled':
      return 'Cancelled';
    default:
      return status;
  }
}

function _adlEsc(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function _adlBundleProgressPercent(bundle) {
  if (!bundle) return 0;
  const raw = bundle.progress_percent ?? bundle.progress ?? 0;
  let progress = Number(raw);
  if (!Number.isFinite(progress)) progress = 0;
  if (progress <= 1) progress *= 100;
  return Math.max(0, Math.min(100, Math.round(progress)));
}

function _adlFormatBytes(bytes) {
  const value = Number(bytes);
  if (!Number.isFinite(value) || value <= 0) return '';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let size = value;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  const decimals = size >= 10 || unit === 0 ? 0 : 1;
  return `${size.toFixed(decimals)} ${units[unit]}`;
}

function _adlFormatSpeed(bytesPerSecond) {
  const formatted = _adlFormatBytes(bytesPerSecond);
  return formatted ? `${formatted}/s` : '';
}

function _adlSourceLabel(source) {
  const labels = {
    torrent: 'Torrent',
    usenet: 'Usenet',
    soulseek: 'Soulseek',
    youtube: 'YouTube',
    tidal: 'Tidal',
    qobuz: 'Qobuz',
    hifi: 'HiFi',
    deezer_dl: 'Deezer',
    amazon: 'Amazon',
    lidarr: 'Lidarr',
    soundcloud: 'SoundCloud',
  };
  const key = String(source || '').toLowerCase();
  return labels[key] || (source ? String(source) : 'Release');
}

function _adlBundleStateLabel(state) {
  const labels = {
    searching: 'searching for release',
    downloading: 'downloading release',
    staged: 'matching tracks',
    failed: 'release failed',
  };
  const key = String(state || '').toLowerCase();
  return labels[key] || (state ? String(state).replace(/_/g, ' ') : 'downloading release');
}

function _adlBundleProgressText(bundle) {
  const pct = _adlBundleProgressPercent(bundle);
  const source = _adlSourceLabel(bundle && bundle.source);
  const state = _adlBundleStateLabel(bundle && bundle.state);
  const release = bundle && bundle.release ? ` - ${bundle.release}` : '';
  const speed = _adlFormatSpeed(bundle && bundle.speed);
  const size = _adlFormatBytes(bundle && bundle.size);
  const detail = speed || size ? ` (${[speed, size].filter(Boolean).join(' of ')})` : '';
  return `${source} ${state} ${pct}%${release}${detail}`;
}

async function adlClearCompleted() {
  // This now also deletes the persisted completed-download history, so confirm.
  if (typeof showConfirmDialog === 'function') {
    const ok = await showConfirmDialog({
      title: 'Clear Completed',
      message:
        'Remove ALL completed and failed downloads from the list and history? ' +
        'This also clears unverified items from the verification queue. ' +
        'Your files stay in the library — only the download-history rows are removed.',
      confirmText: 'Clear',
      destructive: true,
    });
    if (!ok) return;
  }
  try {
    const resp = await fetch('/api/downloads/clear-completed', { method: 'POST' });
    const data = await resp.json();
    if (data.success) {
      const n = data.total_cleared != null ? data.total_cleared : data.cleared;
      if (typeof showToast === 'function') showToast(`Cleared ${n} downloads`, 'success');
      _adlFetch();
    }
  } catch (e) {
    console.error('Error clearing completed downloads:', e);
  }
}

// ---- Batch Context Panel ----

const _BATCH_FADE_SECONDS = 15; // Remove completed batches after this many seconds

function _adlRenderBatchPanel() {
  const container = document.getElementById('adl-batch-active');
  const headerTitle = document.querySelector('.adl-batch-panel-title');
  if (!container) return;

  const now = Date.now();

  // Filter out batches that completed more than FADE seconds ago
  const visibleBatches = _adlBatches.filter((batch) => {
    const isTerminal =
      batch.phase === 'complete' || batch.phase === 'cancelled' || batch.phase === 'error';
    if (!isTerminal) {
      delete _batchCompletedAt[batch.batch_id]; // Reset if it came back to life
      return true;
    }
    if (!_batchCompletedAt[batch.batch_id]) {
      _batchCompletedAt[batch.batch_id] = now;
    }
    const elapsed = (now - _batchCompletedAt[batch.batch_id]) / 1000;
    return elapsed < _BATCH_FADE_SECONDS;
  });

  const activeBatches = visibleBatches.filter(
    (b) => b.phase !== 'complete' && b.phase !== 'cancelled' && b.phase !== 'error',
  );

  // Update header with count
  if (headerTitle) {
    headerTitle.textContent =
      activeBatches.length > 0 ? `Batches (${activeBatches.length})` : 'Batches';
  }

  _adlRenderBatchSummary(activeBatches);

  if (visibleBatches.length === 0) {
    container.innerHTML = `<div class="adl-batch-empty">
            <div class="adl-batch-empty-icon">
                <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.3"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
            </div>
            <div class="adl-batch-empty-title">Nothing downloading</div>
            <div class="adl-batch-empty-sub">Batches show up here as they run.</div>
            <div class="adl-batch-empty-links">
                <a href="#" onclick="event.preventDefault(); navigateToPage('search')">Search</a>
                <a href="#" onclick="event.preventDefault(); navigateToPage('sync')">Sync</a>
                <a href="#" onclick="event.preventDefault(); navigateToPage('wishlist')">Wishlist</a>
            </div>
        </div>`;
    return;
  }

  let html = '';
  for (const batch of visibleBatches) {
    const colorIdx = _getBatchColor(batch.batch_id);
    const colorStyle =
      colorIdx >= 0 ? `border-left-color: rgba(var(--batch-color-${colorIdx}), 0.6)` : '';
    const isExpanded = _adlExpandedBatches.has(batch.batch_id);
    const isFiltered = _adlFilterBatchId === batch.batch_id;
    const albumBundle = batch.album_bundle || null;
    const bundleProgress = _adlBundleProgressPercent(albumBundle);
    const total = batch.total || 1;
    const done = batch.completed + batch.failed;
    const pct =
      batch.phase === 'album_downloading' ? bundleProgress : Math.round((done / total) * 100);
    const hasFailed = batch.failed > 0;
    const isTerminal =
      batch.phase === 'complete' || batch.phase === 'cancelled' || batch.phase === 'error';
    const isActive =
      (batch.phase === 'downloading' && batch.active > 0) || batch.phase === 'album_downloading';

    // Fade progress for completing batches
    let fadeStyle = '';
    if (isTerminal && _batchCompletedAt[batch.batch_id]) {
      const elapsed = (now - _batchCompletedAt[batch.batch_id]) / 1000;
      const fadeStart = _BATCH_FADE_SECONDS * 0.6;
      if (elapsed > fadeStart) {
        const fadeProgress = Math.min(1, (elapsed - fadeStart) / (_BATCH_FADE_SECONDS - fadeStart));
        fadeStyle = `opacity: ${1 - fadeProgress};`;
      }
    }

    const sourceBadge = batch.source_page
      ? `<span class="adl-batch-card-source">${_adlEsc(batch.source_page)}</span>`
      : '';

    // Phase label with icon
    let phaseText = '';
    let phaseIcon = '';
    if (batch.phase === 'queued') {
      // Batch is in the executor queue waiting for a worker slot.
      // ``missing_download_executor`` has max_workers=3 by default,
      // so wishlist runs with >3 sub-batches park the rest at this
      // state until a worker frees up. Pre-fix this status rendered
      // as "Analyzing..." which misled users into thinking 26
      // batches were all working when really only 3 were running.
      phaseText = 'Queued';
      phaseIcon = '<span style="margin-right:4px;opacity:0.6">⏳</span>';
    } else if (batch.phase === 'analysis') {
      phaseText = 'Analyzing...';
      phaseIcon = '<span class="adl-spinner" style="margin-right:4px"></span>';
    } else if (batch.phase === 'album_downloading') {
      phaseText = _adlBundleProgressText(albumBundle);
      phaseIcon = '<span class="adl-spinner" style="margin-right:4px"></span>';
    } else if (batch.phase === 'downloading') {
      phaseText = `${batch.completed}/${total} tracks`;
      if (batch.active > 0)
        phaseIcon = '<span class="adl-spinner" style="margin-right:4px"></span>';
    } else if (batch.phase === 'complete') {
      phaseText = `Done \u2014 ${batch.completed} tracks`;
      phaseIcon = '<span style="color:#22c55e;margin-right:4px">\u2713</span>';
    } else if (batch.phase === 'cancelled') {
      phaseText = 'Cancelled';
    } else if (batch.phase === 'error') {
      phaseText = 'Error';
    } else {
      phaseText = batch.phase;
    }

    // Get first track artwork for batch thumbnail, fallback to initial
    const batchTracks = _adlData.filter((d) => d.batch_id === batch.batch_id);
    const artworkTrack = batchTracks.find((t) => t.artwork);
    let thumbHtml;
    if (artworkTrack) {
      thumbHtml = `<img class="adl-batch-card-thumb" src="${_adlEsc(artworkTrack.artwork)}" alt="" onerror="this.outerHTML='<div class=\\'adl-batch-card-thumb adl-batch-card-thumb-fallback\\'>${_adlEsc((batch.batch_name || 'D')[0])}</div>'">`;
    } else {
      const initial = (batch.batch_name || 'D')[0].toUpperCase();
      const bgColor =
        colorIdx >= 0 ? `rgba(var(--batch-color-${colorIdx}), 0.15)` : 'rgba(255,255,255,0.05)';
      const fgColor =
        colorIdx >= 0 ? `rgba(var(--batch-color-${colorIdx}), 0.7)` : 'rgba(255,255,255,0.4)';
      thumbHtml = `<div class="adl-batch-card-thumb adl-batch-card-thumb-fallback" style="background:${bgColor};color:${fgColor}">${initial}</div>`;
    }

    // Build expanded tracks list with per-track progress
    let tracksHtml = '';
    if (isExpanded) {
      if (batchTracks.length > 0) {
        tracksHtml = batchTracks
          .map((t, i) => {
            const cls = _adlStatusClass(t.status);
            const progress = t.progress || 0;
            const idx = t.track_index != null ? t.track_index + 1 : i + 1;

            // Right-aligned state: % / spinner / \u2713 / \u2717 / \u00B7 \u2014 color via row class.
            let stateHtml = '';
            if (t.status === 'downloading' && progress > 0) {
              stateHtml = `<span class="adl-batch-track-state">${Math.round(progress)}%</span>`;
            } else if (t.status === 'searching') {
              stateHtml = `<span class="adl-batch-track-state"><span class="adl-spinner" style="width:9px;height:9px"></span></span>`;
            } else if (t.status === 'post_processing') {
              stateHtml = `<span class="adl-batch-track-state" title="Processing">proc</span>`;
            } else if (cls === 'completed') {
              stateHtml = `<span class="adl-batch-track-state">\u2713</span>`;
            } else if (cls === 'failed') {
              stateHtml = `<span class="adl-batch-track-state" title="${_adlEsc(t.error || 'Failed')}">\u2717</span>`;
            } else {
              stateHtml = `<span class="adl-batch-track-state">\u00B7</span>`;
            }

            const isDownloading = t.status === 'downloading' && progress > 0;
            const miniBar = isDownloading
              ? `<div class="adl-batch-track-progress"><div class="adl-batch-track-progress-fill" style="width:${progress}%"></div></div>`
              : '';
            const sub = t.artist
              ? `<span class="adl-batch-track-sub">${_adlEsc(t.artist)}</span>`
              : '';

            return `<div class="adl-batch-track-row ${cls}${isDownloading ? ' downloading' : ''}">
                        <span class="adl-batch-track-idx">${idx}</span>
                        <span class="adl-batch-track-text"><span class="adl-batch-track-title">${_adlEsc(t.title || 'Unknown')}</span>${sub}</span>
                        ${stateHtml}
                        ${miniBar}
                    </div>`;
          })
          .join('');
      } else {
        tracksHtml =
          batch.phase === 'album_downloading'
            ? '<div class="adl-batch-release-note">Downloading one release first. Track matching starts after staging.</div>'
            : '<div style="font-size:0.7rem;color:rgba(255,255,255,0.3);padding:4px 0">No tracks loaded</div>';
      }
    }

    const cardClasses = ['adl-batch-card', `phase-${batch.phase}`];
    if (isExpanded) cardClasses.push('expanded');
    if (isActive) cardClasses.push('active-glow');
    if (isFiltered) cardClasses.push('filtered');

    const playlistId = _adlEsc(batch.playlist_id || '');

    // Segmented progress: done (green) / failed (red) / active (accent) of
    // total; the remaining width is the dim bar background (queued).
    let segDone = 0,
      segFail = 0,
      segActive = 0;
    if (batch.phase === 'album_downloading') {
      segActive = bundleProgress; // one release downloading
    } else {
      segDone = Math.max(0, Math.min(100, (batch.completed / total) * 100));
      segFail = Math.max(0, Math.min(100 - segDone, (batch.failed / total) * 100));
      segActive = Math.max(
        0,
        Math.min(100 - segDone - segFail, ((batch.active || 0) / total) * 100),
      );
    }

    // "Now downloading" — the live track on active batches.
    const nowTrack = isActive
      ? batchTracks.find((t) => t.status === 'downloading') ||
        batchTracks.find((t) => t.status === 'searching')
      : null;
    const nowHtml =
      nowTrack && nowTrack.title
        ? `<div class="adl-batch-card-now"><span class="adl-batch-now-icon">↓</span> ${_adlEsc(nowTrack.title)}</div>`
        : '';

    // Stat chips + ETA line.
    const chips = [];
    if (batch.completed)
      chips.push(`<span class="adl-chip adl-chip-done">✓ ${batch.completed}</span>`);
    if (batch.failed) chips.push(`<span class="adl-chip adl-chip-fail">✗ ${batch.failed}</span>`);
    if (batch.active) chips.push(`<span class="adl-chip adl-chip-active">↓ ${batch.active}</span>`);
    if (batch.queued)
      chips.push(`<span class="adl-chip adl-chip-queued">${batch.queued} queued</span>`);
    const etaText = _adlBatchEta(batch);
    const statLine =
      chips.length || etaText
        ? `<div class="adl-batch-statline"><div class="adl-batch-chips">${chips.join('')}</div>${etaText ? `<span class="adl-batch-eta">${_adlEsc(etaText)}</span>` : ''}</div>`
        : '';

    html += `<div class="${cardClasses.join(' ')}" style="${colorStyle}${fadeStyle}" data-batch-id="${batch.batch_id}" onclick="_adlToggleBatch('${batch.batch_id}')">
            <div class="adl-batch-card-top">
                ${thumbHtml}
                <div class="adl-batch-card-info">
                    <div class="adl-batch-card-name adl-batch-card-link" onclick="event.stopPropagation(); _adlOpenBatchModal('${batch.batch_id}', '${playlistId}', '${_adlEsc(batch.batch_name || 'Download')}')" title="Open download modal">${_adlEsc(batch.batch_name || 'Download')}</div>
                    <div class="adl-batch-card-meta">${phaseIcon}${phaseText}</div>
                    ${nowHtml}
                </div>
                ${sourceBadge}
                <div class="adl-batch-card-actions">
                    <button class="adl-batch-card-filter ${isFiltered ? 'active' : ''}" onclick="event.stopPropagation(); _adlFilterByBatch('${batch.batch_id}')" title="${isFiltered ? 'Show all downloads' : 'Filter to this batch'}">
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/></svg>
                    </button>
                    ${
                      !isTerminal
                        ? `<button class="adl-batch-card-cancel" onclick="event.stopPropagation(); _adlCancelBatch('${batch.batch_id}')" title="Cancel batch">
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                    </button>`
                        : ''
                    }
                    <span class="adl-batch-card-chevron" aria-hidden="true">
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg>
                    </span>
                </div>
            </div>
            <div class="adl-batch-segbar">
                <div class="adl-batch-seg seg-done" style="width:${segDone}%"></div>
                <div class="adl-batch-seg seg-fail" style="width:${segFail}%"></div>
                <div class="adl-batch-seg seg-active${isActive ? ' shimmer' : ''}" style="width:${segActive}%"></div>
            </div>
            ${statLine}
            <div class="adl-batch-tracks">${tracksHtml}</div>
        </div>`;
  }

  container.innerHTML = html;
}

function _adlToggleBatch(batchId) {
  if (_adlExpandedBatches.has(batchId)) {
    _adlExpandedBatches.delete(batchId);
  } else {
    _adlExpandedBatches.add(batchId);
  }
  _adlRenderBatchPanel();
}

function _adlOpenBatchModal(batchId, playlistId, batchName) {
  // For wishlist batches, navigate to wishlist and show modal
  if (playlistId === 'wishlist') {
    const clientProcess = activeDownloadProcesses['wishlist'];
    if (
      clientProcess &&
      clientProcess.modalElement &&
      document.body.contains(clientProcess.modalElement)
    ) {
      clientProcess.modalElement.style.display = 'flex';
      if (typeof WishlistModalState !== 'undefined') WishlistModalState.setVisible();
    } else {
      rehydrateModal(
        { playlist_id: playlistId, playlist_name: batchName, batch_id: batchId },
        true,
      );
    }
    return;
  }

  // For other batches, try to show existing modal or rehydrate
  for (const [pid, process] of Object.entries(activeDownloadProcesses)) {
    if (
      process.batchId === batchId &&
      process.modalElement &&
      document.body.contains(process.modalElement)
    ) {
      process.modalElement.style.display = 'flex';
      return;
    }
  }
  // Rehydrate from server
  rehydrateModal({ playlist_id: playlistId, playlist_name: batchName, batch_id: batchId }, true);
}

function _adlFilterByBatch(batchId) {
  if (_adlFilterBatchId === batchId) {
    _adlFilterBatchId = null; // Toggle off
  } else {
    _adlFilterBatchId = batchId;
  }
  _adlRender();
  _adlRenderBatchPanel();
}

async function adlCancelRow(btnEl, playlistId, trackIndex) {
  // Per-row cancel on the Downloads page. Uses the same atomic cancel
  // endpoint the modal cancel buttons use, so worker slots free properly.
  if (!playlistId || trackIndex === undefined || trackIndex === null) {
    showToast('Cannot cancel — missing task coordinates', 'error');
    return;
  }
  // Lock the button so rapid clicks don't fire duplicate requests
  if (btnEl) {
    if (btnEl.dataset.cancelling === '1') return;
    btnEl.dataset.cancelling = '1';
    btnEl.classList.add('adl-row-cancel-pending');
  }
  try {
    const resp = await fetch('/api/downloads/cancel_task_v2', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        playlist_id: playlistId,
        track_index: trackIndex,
      }),
    });
    const data = await resp.json();
    if (data.success) {
      const name =
        data.task_info && data.task_info.track_name ? data.task_info.track_name : 'Track';
      showToast(`Cancelled "${name}"`, 'info');
      _adlFetch();
    } else {
      showToast(data.error || 'Cancel failed', 'error');
      if (btnEl) {
        btnEl.dataset.cancelling = '0';
        btnEl.classList.remove('adl-row-cancel-pending');
      }
    }
  } catch (e) {
    console.error('ADL row cancel error:', e);
    showToast('Cancel request failed', 'error');
    if (btnEl) {
      btnEl.dataset.cancelling = '0';
      btnEl.classList.remove('adl-row-cancel-pending');
    }
  }
}

async function _adlCancelBatch(batchId) {
  const batch = _adlBatches.find((b) => b.batch_id === batchId);
  const batchName = batch ? batch.batch_name : 'this batch';
  const confirmed = await showConfirmDialog({
    title: 'Cancel Batch',
    message: `Cancel "${batchName}"? All active and queued downloads in this batch will be stopped.`,
    confirmText: 'Cancel Batch',
    destructive: true,
  });
  if (!confirmed) return;
  try {
    const resp = await fetch(`/api/playlists/${batchId}/cancel_batch`, { method: 'POST' });
    const data = await resp.json();
    if (data.success) {
      showToast(`Cancelled ${data.cancelled_tasks} downloads`, 'info');
      _adlFetch();
    } else {
      showToast(data.error || 'Failed to cancel batch', 'error');
    }
  } catch (e) {
    showToast('Failed to cancel batch', 'error');
  }
}

async function adlCancelAll() {
  // Cancel every batch with active/queued work — equivalent to clicking
  // "Cancel All" inside each running download modal. Uses the same
  // /api/playlists/<batch_id>/cancel_batch endpoint the per-batch card
  // cancel uses, so worker slots free atomically.
  const runningBatches = _adlBatches.filter((b) => (b.active || 0) > 0 || (b.queued || 0) > 0);
  if (runningBatches.length === 0) {
    showToast('No active batches to cancel', 'info');
    return;
  }

  const totalTasks = runningBatches.reduce((sum, b) => sum + (b.active || 0) + (b.queued || 0), 0);
  const batchWord = runningBatches.length === 1 ? 'batch' : 'batches';
  const taskWord = totalTasks === 1 ? 'task' : 'tasks';
  const confirmed = await showConfirmDialog({
    title: 'Cancel All Downloads',
    message: `Cancel ${totalTasks} ${taskWord} across ${runningBatches.length} ${batchWord}? Active and queued downloads will be stopped and added to the wishlist.`,
    confirmText: 'Cancel All',
    destructive: true,
  });
  if (!confirmed) return;

  const btn = document.getElementById('adl-cancel-all-btn');
  if (btn) {
    btn.disabled = true;
    btn.classList.add('adl-cancel-all-pending');
  }

  let cancelled = 0;
  let failed = 0;
  // Sequential so we don't hammer the backend — cancel_batch takes a lock
  // internally and parallel calls would mostly serialize anyway.
  for (const batch of runningBatches) {
    try {
      const resp = await fetch(`/api/playlists/${batch.batch_id}/cancel_batch`, { method: 'POST' });
      const data = await resp.json();
      if (data.success) {
        cancelled += data.cancelled_tasks || 0;
      } else {
        failed += 1;
        console.warn(`cancel_batch failed for ${batch.batch_id}:`, data.error);
      }
    } catch (e) {
      failed += 1;
      console.warn(`cancel_batch exception for ${batch.batch_id}:`, e);
    }
  }

  if (btn) {
    btn.disabled = false;
    btn.classList.remove('adl-cancel-all-pending');
  }

  if (cancelled > 0 && failed === 0) {
    showToast(`Cancelled ${cancelled} downloads`, 'success');
  } else if (cancelled > 0 && failed > 0) {
    showToast(`Cancelled ${cancelled} downloads (${failed} batches failed)`, 'info');
  } else {
    showToast('Failed to cancel any downloads', 'error');
  }

  _adlFetch();
}

// ---- Batch History ----

async function _adlFetchBatchHistory() {
  try {
    const resp = await fetch('/api/downloads/batch-history?days=7&limit=50');
    const data = await resp.json();
    if (data.success) {
      _adlBatchHistory = data.history || [];
      _adlRenderBatchHistory();
    }
  } catch (e) {
    console.debug('Batch history fetch error:', e);
  }
}

function _adlRenderBatchHistory() {
  const section = document.getElementById('adl-batch-history-section');
  const list = document.getElementById('adl-batch-history-list');
  if (!section || !list) return;

  if (_adlBatchHistory.length === 0) {
    section.style.display = 'none';
    return;
  }

  section.style.display = '';

  list.innerHTML = _adlBatchHistory
    .map((h) => {
      const name = _adlEsc(h.playlist_name || 'Unknown');
      const downloaded = h.tracks_downloaded || 0;
      const failed = h.tracks_failed || 0;
      const total = h.total_tracks || 0;
      const statsParts = [`${downloaded}/${total}`];
      if (failed > 0) statsParts.push(`<span style="color:#ef4444">${failed} failed</span>`);

      let dateText = '';
      if (h.completed_at) {
        try {
          const d = new Date(h.completed_at);
          const now = new Date();
          const diffMs = now - d;
          const diffH = Math.floor(diffMs / 3600000);
          if (diffH < 1) dateText = 'just now';
          else if (diffH < 24) dateText = `${diffH}h ago`;
          else dateText = `${Math.floor(diffH / 24)}d ago`;
        } catch (e) {
          dateText = '';
        }
      }

      const sourceLabel = h.source_page
        ? `<span class="adl-batch-card-source" style="font-size:0.6rem;padding:0 4px">${_adlEsc(h.source_page)}</span>`
        : '';

      // Source type color dot
      const sourceColors = {
        wishlist: '168, 85, 247',
        sync: '59, 130, 246',
        album: '16, 185, 129',
      };
      const dotColor = sourceColors[h.source_page] || '255, 255, 255';
      const histDot = `<span class="adl-batch-history-dot" style="background:rgba(${dotColor}, 0.6)"></span>`;

      return `<div class="adl-batch-history-item">
            ${histDot}
            <div class="adl-batch-history-name">${name} ${sourceLabel}</div>
            <div class="adl-batch-history-stats">${statsParts.join(' ')}</div>
            <div class="adl-batch-history-date">${dateText}</div>
        </div>`;
    })
    .join('');
}

function adlToggleBatchHistory() {
  const section = document.getElementById('adl-batch-history-section');
  if (section) section.classList.toggle('expanded');
}

function adlToggleBatchPanel() {
  const panel = document.getElementById('adl-batch-panel');
  if (panel) panel.classList.toggle('collapsed');
}

window.adlSetFilter = adlSetFilter;
window.adlClearCompleted = adlClearCompleted;
window._adlToggleBatch = _adlToggleBatch;
window._adlOpenBatchModal = _adlOpenBatchModal;
window._adlFilterByBatch = _adlFilterByBatch;
window._adlCancelBatch = _adlCancelBatch;
window.adlCancelRow = adlCancelRow;
window.adlCancelAll = adlCancelAll;
window.adlToggleBatchHistory = adlToggleBatchHistory;
window.adlToggleBatchPanel = adlToggleBatchPanel;
