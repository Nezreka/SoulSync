/* Track Detail modal — opens from any track row in the download modal and
 * shows a rich, status-aware view: cover, title/artist/album, play/listen,
 * source, quality, AcoustID verdict, file location, expected-vs-downloaded,
 * and the right actions for the row's state (Accept/Search for quarantined,
 * Search for failed). Backed by /api/downloads/task/<id>/detail.
 *
 * Globals it leans on (defined in downloads.js, loaded first): showToast,
 * showConfirmDialog, showCandidatesModal, escapeHtml.
 */

const _TD_STATUS = {
    completed:   { label: 'Completed',   cls: 'td-badge-ok' },
    quarantined: { label: 'Quarantined', cls: 'td-badge-warn' },
    failed:      { label: 'Failed',      cls: 'td-badge-bad' },
    not_found:   { label: 'Not Found',   cls: 'td-badge-muted' },
    in_progress: { label: 'In Progress', cls: 'td-badge-info' },
};

const _TD_ACOUSTID = {
    pass:     { label: 'Verified',  cls: 'td-aid-ok' },
    fail:     { label: 'Failed',    cls: 'td-aid-bad' },
    error:    { label: 'Error',     cls: 'td-aid-bad' },
    skip:     { label: 'No match',  cls: 'td-aid-muted' },
    disabled: { label: 'Off',       cls: 'td-aid-muted' },
};

function _tdEsc(s) {
    if (typeof escapeHtml === 'function') return escapeHtml(s == null ? '' : s);
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function _tdSetText(id, value, fallback = '—') {
    const el = document.getElementById(id);
    if (el) el.textContent = (value && String(value).trim()) ? value : fallback;
}

// Release the preview <audio> so the OS file handle is freed before any move
// (Windows locks an open file — same reason the quarantine chooser does this).
function _tdReleaseAudio() {
    const a = document.getElementById('td-audio');
    if (!a) return Promise.resolve();
    try { a.pause(); a.removeAttribute('src'); a.load(); } catch (e) { /* ignore */ }
    return new Promise((r) => setTimeout(r, 400));
}

function closeTrackDetail() {
    const o = document.getElementById('track-detail-overlay');
    if (!o) return;
    const a = document.getElementById('td-audio');
    if (a) { try { a.pause(); a.removeAttribute('src'); a.load(); } catch (e) { /* ignore */ } }
    o.classList.remove('visible');
    o.setAttribute('aria-hidden', 'true');
}

async function openTrackDetail(taskId) {
    if (!taskId) return;
    const overlay = document.getElementById('track-detail-overlay');
    if (!overlay) { console.warn('track-detail modal not present'); return; }

    let detail;
    try {
        const resp = await fetch(`/api/downloads/task/${encodeURIComponent(taskId)}/detail`);
        const data = await resp.json();
        if (!data.success) { showToast(data.error || 'Could not load track detail', 'error'); return; }
        detail = data.detail;
    } catch (err) {
        showToast(`Could not load track detail: ${err.message}`, 'error');
        return;
    }

    _tdRender(detail, taskId);
    overlay.classList.add('visible');
    overlay.setAttribute('aria-hidden', 'false');
}

function _tdRender(d, taskId) {
    const kind = d.status_kind || 'in_progress';

    // Header
    _tdSetText('td-title', d.title, 'Unknown Track');
    _tdSetText('td-artist', d.artist, '');
    _tdSetText('td-album', d.album, '');

    const thumb = document.getElementById('td-thumb');
    const thumbPh = document.getElementById('td-thumb-ph');
    if (thumb && thumbPh) {
        if (d.thumb_url && /^https?:\/\//.test(d.thumb_url)) {
            thumb.src = d.thumb_url; thumb.hidden = false; thumbPh.hidden = true;
            thumb.onerror = () => { thumb.hidden = true; thumbPh.hidden = false; };
        } else {
            thumb.hidden = true; thumbPh.hidden = false;
        }
    }

    const badge = document.getElementById('td-status-badge');
    if (badge) {
        const s = _TD_STATUS[kind] || _TD_STATUS.in_progress;
        badge.textContent = s.label;
        badge.className = `td-status-badge ${s.cls}`;
    }

    // Info grid
    _tdSetText('td-f-source', d.source);
    _tdSetText('td-f-quality', d.quality);
    _tdSetText('td-f-location', d.file_path);
    const aidEl = document.getElementById('td-f-acoustid');
    if (aidEl) {
        const a = _TD_ACOUSTID[d.acoustid_result];
        aidEl.textContent = a ? a.label : '—';
        aidEl.className = `td-value ${a ? a.cls : ''}`;
    }

    // Expected vs downloaded (only when we have provenance)
    const prov = document.getElementById('td-provenance');
    const exp = (d.expected && (d.expected.title || d.expected.artist));
    const dl = (d.downloaded && (d.downloaded.title || d.downloaded.artist));
    if (prov) {
        if (exp || dl) {
            prov.hidden = false;
            _tdSetText('td-exp', exp ? `${d.expected.title}${d.expected.artist ? ' — ' + d.expected.artist : ''}` : '', '—');
            _tdSetText('td-dl', dl ? `${d.downloaded.title}${d.downloaded.artist ? ' — ' + d.downloaded.artist : ''}` : '', '—');
        } else {
            prov.hidden = true;
        }
    }

    // Reason banner (quarantined / failed)
    const reason = document.getElementById('td-reason');
    if (reason) {
        if ((kind === 'quarantined' || kind === 'failed') && d.reason) {
            reason.hidden = false;
            reason.innerHTML = `<strong>${kind === 'quarantined' ? 'Why it was quarantined' : 'Why it failed'}:</strong><br>${_tdEsc(d.reason)}`;
        } else {
            reason.hidden = true;
        }
    }

    // Audio: completed -> library stream; quarantined -> quarantine stream.
    const audio = document.getElementById('td-audio');
    if (audio) {
        let src = '';
        if (kind === 'completed' && d.file_path) {
            src = `/stream/library-audio?path=${encodeURIComponent(d.file_path)}`;
        } else if (kind === 'quarantined' && d.quarantine_entry_id) {
            src = `/api/quarantine/${encodeURIComponent(d.quarantine_entry_id)}/stream`;
        }
        if (src) { audio.src = src; audio.hidden = false; } else { audio.removeAttribute('src'); audio.hidden = true; }
    }

    _tdRenderActions(d, taskId, kind);
}

function _tdRenderActions(d, taskId, kind) {
    const el = document.getElementById('td-actions');
    if (!el) return;
    el.innerHTML = '';
    const add = (label, cls, onClick) => {
        const b = document.createElement('button');
        b.className = `td-action-btn ${cls}`;
        b.textContent = label;
        b.addEventListener('click', onClick);
        el.appendChild(b);
        return b;
    };

    if (kind === 'quarantined') {
        add('✓ Accept & Import', 'td-action-primary', (e) => _tdAccept(e.currentTarget, d.quarantine_entry_id, taskId));
        add('🔍 Search for a different result', 'td-action-secondary', () => { closeTrackDetail(); if (taskId) showCandidatesModal(taskId); });
    } else if (kind === 'failed' || kind === 'not_found') {
        add('🔍 Search for a different result', 'td-action-secondary', () => { closeTrackDetail(); if (taskId) showCandidatesModal(taskId); });
    }
    // completed / in_progress: no destructive actions — the player + info is it.
}

async function _tdAccept(button, entryId, taskId) {
    if (!entryId) { showToast('Cannot accept — missing quarantine id.', 'error'); return; }
    const confirmed = await showConfirmDialog({
        title: 'Accept Quarantined File',
        message: 'Import this file and skip the quarantine checks for this approved pass?',
        confirmText: 'Accept & Import',
        cancelText: 'Cancel',
    });
    if (!confirmed) return;
    const original = button.textContent;
    button.disabled = true;
    button.textContent = 'Importing…';
    await _tdReleaseAudio();  // free the file handle before the move (Windows lock)
    try {
        const resp = await fetch(`/api/quarantine/${encodeURIComponent(entryId)}/approve`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ task_id: taskId || '' }),
        });
        const data = await resp.json();
        if (data.success) {
            showToast('Accepted. Re-running post-processing.', 'success');
            closeTrackDetail();
            return;
        }
        const needsRecover = /thin sidecar|recover to staging|embedded context|missing file or sidecar/i.test(data.error || '');
        if (needsRecover) {
            button.textContent = 'Recovering…';
            const rec = await fetch(`/api/quarantine/${encodeURIComponent(entryId)}/recover`, { method: 'POST' });
            const recData = await rec.json();
            if (recData.success) {
                showToast('Older entry — moved to Staging. Finish it from the Import page.', 'success');
                closeTrackDetail();
                return;
            }
            showToast(`Recover failed: ${recData.error || 'Unknown error'}`, 'error');
        } else {
            showToast(`Accept failed: ${data.error || 'Unknown error'}`, 'error');
        }
    } catch (err) {
        showToast(`Accept failed: ${err.message}`, 'error');
    }
    button.disabled = false;
    button.textContent = original;
}

// Close on backdrop click + Escape.
document.addEventListener('DOMContentLoaded', () => {
    const overlay = document.getElementById('track-detail-overlay');
    if (overlay) {
        overlay.addEventListener('mousedown', (e) => { if (e.target === overlay) closeTrackDetail(); });
    }
});
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        const o = document.getElementById('track-detail-overlay');
        if (o && o.classList.contains('visible')) closeTrackDetail();
    }
});
