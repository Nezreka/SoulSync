// ===== ENHANCE QUALITY MODAL =====

let _enhanceQualityData = null;
let _enhanceArtistId = null;

const ENHANCE_TIER_MAP = {
    'lossless': { num: 1, label: 'Lossless', cssClass: 'lossless' },
    'high_lossy': { num: 2, label: 'High Lossy', cssClass: 'high-lossy' },
    'standard_lossy': { num: 3, label: 'Standard Lossy', cssClass: 'standard-lossy' },
    'low_lossy': { num: 4, label: 'Low Lossy', cssClass: 'low-lossy' },
    'unknown': { num: 999, label: 'Unknown', cssClass: 'unknown' },
};

async function checkArtistEnhanceEligibility(artistId) {
    const btn = document.getElementById('library-artist-enhance-btn');
    if (!btn) return;
    btn.classList.add('hidden');
    _enhanceArtistId = artistId;

    try {
        const resp = await fetch(`/api/library/artist/${artistId}/quality-analysis`);
        if (!resp.ok) return;
        const data = await resp.json();
        if (!data.success || !data.tracks || data.tracks.length === 0) return;

        _enhanceQualityData = data;

        // Show button if any tracks are below the user's min acceptable tier
        const minTier = data.min_acceptable_tier || 1;
        const belowCount = data.tracks.filter(t => t.tier_num > minTier).length;
        if (belowCount > 0) {
            btn.classList.remove('hidden');
            btn.querySelector('.enhance-text').textContent = `Enhance Quality (${belowCount})`;
        }
    } catch (e) {
        console.debug('Enhance eligibility check failed:', e);
    }
}

function openEnhanceQualityModal() {
    if (!_enhanceQualityData) return;
    const data = _enhanceQualityData;

    // Remove existing modal if any
    const existing = document.getElementById('enhance-quality-overlay');
    if (existing) existing.remove();

    const overlay = document.createElement('div');
    overlay.id = 'enhance-quality-overlay';
    overlay.className = 'enhance-modal-overlay';
    overlay.onclick = (e) => { if (e.target === overlay) closeEnhanceQualityModal(); };

    const minTier = data.min_acceptable_tier || 1;
    const summary = data.quality_summary || {};

    overlay.innerHTML = `
        <div class="enhance-modal" onclick="event.stopPropagation()">
            <div class="enhance-modal-header">
                <h3>⚡ Enhance Quality — ${_esc(data.artist_name)}</h3>
                <button class="enhance-modal-close" onclick="closeEnhanceQualityModal()">&times;</button>
            </div>
            <div class="enhance-summary-bar">
                ${_buildEnhanceSummaryChips(summary)}
            </div>
            <div class="enhance-controls">
                <div class="enhance-tier-selector">
                    <label>Upgrade tracks below:</label>
                    <select id="enhance-tier-dropdown" onchange="updateEnhanceThreshold(parseInt(this.value))">
                        <option value="1" ${minTier <= 1 ? 'selected' : ''}>Lossless (FLAC/WAV)</option>
                        <option value="2" ${minTier === 2 ? 'selected' : ''}>High Lossy (OGG/Opus)</option>
                        <option value="3" ${minTier === 3 ? 'selected' : ''}>Standard Lossy (M4A/AAC)</option>
                        <option value="4" ${minTier >= 4 ? 'selected' : ''}>Low Lossy (MP3/WMA)</option>
                    </select>
                </div>
                <div class="enhance-select-controls">
                    <button class="enhance-select-btn" onclick="enhanceSelectAll(true)">Select All Below</button>
                    <button class="enhance-select-btn" onclick="enhanceSelectAll(false)">Deselect All</button>
                    <span class="enhance-selected-count" id="enhance-selected-count">0 selected</span>
                </div>
            </div>
            <div class="enhance-modal-body">
                <table class="enhance-track-table">
                    <thead>
                        <tr>
                            <th></th>
                            <th>#</th>
                            <th>Title</th>
                            <th>Album</th>
                            <th>Format</th>
                            <th>Bitrate</th>
                        </tr>
                    </thead>
                    <tbody id="enhance-track-tbody">
                    </tbody>
                </table>
            </div>
            <div class="enhance-modal-footer">
                <div class="enhance-footer-info" id="enhance-footer-info"></div>
                <div class="enhance-footer-actions">
                    <button class="enhance-btn secondary" onclick="closeEnhanceQualityModal()">Cancel</button>
                    <button class="enhance-btn primary" id="enhance-submit-btn" onclick="submitEnhanceQuality()" disabled>
                        ⚡ Enhance 0 Tracks
                    </button>
                </div>
            </div>
        </div>
    `;

    document.body.appendChild(overlay);
    renderEnhanceTrackRows(minTier);
}

function _buildEnhanceSummaryChips(summary) {
    const chips = [
        { key: 'lossless', label: 'FLAC', cssClass: 'lossless' },
        { key: 'high_lossy', label: 'OGG/Opus', cssClass: 'high-lossy' },
        { key: 'standard_lossy', label: 'M4A/AAC', cssClass: 'standard-lossy' },
        { key: 'low_lossy', label: 'MP3/WMA', cssClass: 'low-lossy' },
    ];
    return chips
        .filter(c => (summary[c.key] || 0) > 0)
        .map(c => `
            <div class="enhance-summary-chip ${c.cssClass}">
                <span class="chip-count">${summary[c.key]}</span>
                <span class="chip-label">${c.label}</span>
            </div>
        `).join('');
}

function renderEnhanceTrackRows(thresholdTier) {
    const tbody = document.getElementById('enhance-track-tbody');
    if (!tbody || !_enhanceQualityData) return;

    const tracks = _enhanceQualityData.tracks;
    // Sort: below-threshold first, then by album + track number
    const sorted = [...tracks].sort((a, b) => {
        const aBt = a.tier_num > thresholdTier ? 0 : 1;
        const bBt = b.tier_num > thresholdTier ? 0 : 1;
        if (aBt !== bBt) return aBt - bBt;
        const albumCmp = (a.album_title || '').localeCompare(b.album_title || '');
        if (albumCmp !== 0) return albumCmp;
        return (a.disc_number || 1) * 1000 + (a.track_number || 0) - ((b.disc_number || 1) * 1000 + (b.track_number || 0));
    });

    tbody.innerHTML = sorted.map(track => {
        const isBelow = track.tier_num > thresholdTier;
        const tierInfo = ENHANCE_TIER_MAP[track.tier_name] || ENHANCE_TIER_MAP['unknown'];
        const bitrateStr = track.bitrate ? `${track.bitrate} kbps` : '-';
        return `
            <tr class="enhance-track-row ${isBelow ? 'below-threshold' : 'above-threshold'}"
                data-track-id="${_escAttr(track.track_id)}" data-tier="${track.tier_num}">
                <td><input type="checkbox" class="enhance-track-check"
                    ${isBelow ? 'checked' : ''} onchange="updateEnhanceSelectedCount()"></td>
                <td>${track.track_number || '-'}</td>
                <td>${_esc(track.title)}</td>
                <td>${_esc(track.album_title)}</td>
                <td><span class="enhance-format-badge ${tierInfo.cssClass}">${_esc(track.format)}</span></td>
                <td><span class="enhance-bitrate">${bitrateStr}</span></td>
            </tr>
        `;
    }).join('');

    updateEnhanceSelectedCount();
}

function updateEnhanceThreshold(tierNum) {
    const rows = document.querySelectorAll('.enhance-track-row');
    rows.forEach(row => {
        const trackTier = parseInt(row.dataset.tier);
        const isBelow = trackTier > tierNum;
        const cb = row.querySelector('.enhance-track-check');

        row.classList.toggle('below-threshold', isBelow);
        row.classList.toggle('above-threshold', !isBelow);
        if (cb) cb.checked = isBelow;
    });
    updateEnhanceSelectedCount();
}

function enhanceSelectAll(select) {
    const thresholdTier = parseInt(document.getElementById('enhance-tier-dropdown')?.value || '1');
    const checks = document.querySelectorAll('.enhance-track-check');
    checks.forEach(cb => {
        const row = cb.closest('.enhance-track-row');
        const trackTier = parseInt(row?.dataset.tier || '999');
        if (select) {
            cb.checked = trackTier > thresholdTier;
        } else {
            cb.checked = false;
        }
    });
    updateEnhanceSelectedCount();
}

function updateEnhanceSelectedCount() {
    const checks = document.querySelectorAll('.enhance-track-check:checked');
    const count = checks.length;
    const countEl = document.getElementById('enhance-selected-count');
    const submitBtn = document.getElementById('enhance-submit-btn');

    if (countEl) countEl.textContent = `${count} selected`;
    if (submitBtn) {
        submitBtn.textContent = `⚡ Enhance ${count} Track${count !== 1 ? 's' : ''}`;
        submitBtn.disabled = count === 0;
    }
}

async function submitEnhanceQuality() {
    const checks = document.querySelectorAll('.enhance-track-check:checked');
    const trackIds = [];
    checks.forEach(cb => {
        const row = cb.closest('.enhance-track-row');
        if (row?.dataset.trackId) trackIds.push(row.dataset.trackId);
    });

    if (trackIds.length === 0) return;

    const submitBtn = document.getElementById('enhance-submit-btn');
    const footerInfo = document.getElementById('enhance-footer-info');
    if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.innerHTML = '<span class="enhance-spinner"></span>Processing...';
    }
    if (footerInfo) footerInfo.textContent = 'Matching tracks across metadata sources and adding to wishlist...';

    try {
        const resp = await fetch(`/api/library/artist/${_enhanceArtistId}/enhance`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ track_ids: trackIds })
        });

        const result = await resp.json();

        if (result.success) {
            const msg = `${result.enhanced_count} track${result.enhanced_count !== 1 ? 's' : ''} queued for enhancement`;
            if (footerInfo) footerInfo.textContent = msg;

            showToast(msg + (result.failed_count > 0 ? ` (${result.failed_count} failed)` : ''), 'success');

            // Update button count
            const enhBtn = document.getElementById('library-artist-enhance-btn');
            if (enhBtn && result.enhanced_count > 0) {
                const remaining = trackIds.length - result.enhanced_count;
                if (remaining <= 0) {
                    enhBtn.classList.add('hidden');
                }
            }

            if (submitBtn) {
                submitBtn.textContent = '✅ Done';
                submitBtn.disabled = true;
            }

            // Auto-close after short delay
            setTimeout(() => closeEnhanceQualityModal(), 1500);
        } else {
            throw new Error(result.error || 'Enhancement failed');
        }
    } catch (e) {
        console.error('Enhance quality error:', e);
        showToast(`Enhancement failed: ${e.message}`, 'error');
        if (submitBtn) {
            submitBtn.textContent = `⚡ Enhance ${trackIds.length} Tracks`;
            submitBtn.disabled = false;
        }
        if (footerInfo) footerInfo.textContent = '';
    }
}

function closeEnhanceQualityModal() {
    const overlay = document.getElementById('enhance-quality-overlay');
    if (overlay) {
        overlay.classList.add('hidden');
        setTimeout(() => overlay.remove(), 300);
    }
}

// Global exports
window.openEnhanceQualityModal = openEnhanceQualityModal;
window.closeEnhanceQualityModal = closeEnhanceQualityModal;
window.updateEnhanceThreshold = updateEnhanceThreshold;
window.enhanceSelectAll = enhanceSelectAll;
window.updateEnhanceSelectedCount = updateEnhanceSelectedCount;
window.submitEnhanceQuality = submitEnhanceQuality;

// ===== END ENHANCE QUALITY MODAL =====
