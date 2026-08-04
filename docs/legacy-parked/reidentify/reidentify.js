
// ==================== Re-identify Track Modal (#889) ====================
// Lets an admin re-file an already-imported track under a different release
// (single / EP / album). Searches any configured metadata source (tabs, default
// active), and on confirm stages the file + writes a single-use hint the
// auto-import worker consumes (see core/imports/rematch_*.py).

const reidState = { trackId: null, source: null, sources: [], rows: [], selected: null };

function openReidentifyModal(trackId, title, artist, albumTitle, imageUrl) {
    reidState.trackId = trackId;
    reidState.source = null;
    reidState.rows = [];
    reidState.selected = null;

    const overlay = document.getElementById('reid-modal-overlay');
    if (!overlay) return;

    // Hero
    document.getElementById('reid-hero-title').textContent = title || 'Track';
    const sub = document.getElementById('reid-hero-sub');
    sub.textContent = (artist || '') + (albumTitle ? ` · currently in “${albumTitle}”` : '');
    const art = document.getElementById('reid-hero-art');
    const bg = document.getElementById('reid-hero-bg');
    if (imageUrl) {
        art.style.backgroundImage = `url('${imageUrl}')`;
        art.classList.remove('empty');
        bg.style.backgroundImage = `url('${imageUrl}')`;
    } else {
        art.style.backgroundImage = '';
        art.classList.add('empty');
        bg.style.backgroundImage = '';
    }

    document.getElementById('reid-search-input').value = `${title || ''} ${artist || ''}`.trim();
    document.getElementById('reid-replace').checked = true;
    _reidUpdateConfirm();
    _reidRenderState('idle');

    overlay.classList.remove('hidden');
    _reidLoadTabs();
}

function closeReidentifyModal() {
    const overlay = document.getElementById('reid-modal-overlay');
    if (overlay) overlay.classList.add('hidden');
}

async function _reidLoadTabs() {
    const tabsEl = document.getElementById('reid-tabs');
    tabsEl.innerHTML = '';
    try {
        const resp = await fetch('/api/reidentify/sources');
        const data = await resp.json();
        reidState.sources = (data && data.sources) || [];
    } catch (_) {
        reidState.sources = [];
    }
    if (!reidState.sources.length) {
        tabsEl.innerHTML = '<span class="reid-tab active">No metadata sources available</span>';
        _reidRenderState('empty', 'No configured metadata source to search.');
        return;
    }
    const active = reidState.sources.find(s => s.active) || reidState.sources[0];
    reidState.source = active.source;
    reidState.sources.forEach(s => {
        const tab = document.createElement('div');
        tab.className = 'reid-tab' + (s.source === reidState.source ? ' active' : '');
        tab.textContent = s.label || s.source;
        tab.onclick = () => _reidSelectTab(s.source);
        tabsEl.appendChild(tab);
    });
    runReidentifySearch();   // auto-search the active source on open
}

function _reidSelectTab(source) {
    if (source === reidState.source) return;
    reidState.source = source;
    document.querySelectorAll('#reid-tabs .reid-tab').forEach(t => {
        t.classList.toggle('active', t.textContent ===
            (reidState.sources.find(s => s.source === source) || {}).label);
    });
    runReidentifySearch();
}

async function runReidentifySearch() {
    const query = (document.getElementById('reid-search-input').value || '').trim();
    if (!query || !reidState.source) return;
    reidState.selected = null;
    _reidUpdateConfirm();
    _reidRenderState('loading');
    try {
        const url = `/api/reidentify/search?source=${encodeURIComponent(reidState.source)}&q=${encodeURIComponent(query)}`;
        const resp = await fetch(url);
        const data = await resp.json();
        reidState.rows = (data && data.results) || [];
        _reidRenderResults();
    } catch (e) {
        _reidRenderState('empty', 'Search failed. Try another source.');
    }
}

function _reidRenderResults() {
    const el = document.getElementById('reid-results');
    if (!reidState.rows.length) {
        _reidRenderState('empty', 'No releases found. Try refining the search or another source tab.');
        return;
    }
    // ISRC-bearing rows first (provably the same recording), then the rest.
    const ranked = reidState.rows
        .map((r, i) => ({ r, i }))
        .sort((a, b) => (b.r.isrc ? 1 : 0) - (a.r.isrc ? 1 : 0));

    el.innerHTML = '';
    ranked.forEach(({ r }, n) => {
        const badge = (r.album_type || 'album').toLowerCase();
        const bits = [];
        if (r.year) bits.push(r.year);
        if (r.total_tracks) bits.push(`${r.total_tracks} track${r.total_tracks === 1 ? '' : 's'}`);
        const row = document.createElement('div');
        row.className = 'reid-result';
        row.style.animationDelay = `${Math.min(n * 0.03, 0.3)}s`;
        row.onclick = () => _reidSelectResult(r, row);
        row.innerHTML = `
            <div class="reid-result-art" ${r.image_url ? `style="background-image:url('${encodeURI(r.image_url)}')"` : ''}>
                ${r.image_url ? '' : '<span>♪</span>'}
            </div>
            <div class="reid-result-info">
                <div class="reid-result-title">${escapeHtml(r.track_title || '')}</div>
                <div class="reid-result-release">${escapeHtml(r.album_name || 'Unknown release')}${r.artist_name ? ' · ' + escapeHtml(r.artist_name) : ''}</div>
            </div>
            <div class="reid-result-meta">
                <span class="reid-badge ${badge}">${escapeHtml(badge)}</span>
                ${bits.length ? `<span class="reid-result-detail">${escapeHtml(bits.join(' · '))}</span>` : ''}
                <span class="reid-result-check"></span>
            </div>`;
        el.appendChild(row);
    });
}

function _reidSelectResult(r, rowEl) {
    reidState.selected = r;
    document.querySelectorAll('#reid-results .reid-result').forEach(x => x.classList.remove('selected'));
    rowEl.classList.add('selected');
    _reidUpdateConfirm();
}

function _reidUpdateConfirm() {
    const btn = document.getElementById('reid-confirm-btn');
    if (btn) btn.disabled = !reidState.selected;
}

function _reidRenderState(kind, msg) {
    const el = document.getElementById('reid-results');
    if (!el) return;
    if (kind === 'loading') {
        el.innerHTML = '<div class="reid-state"><div class="reid-spinner"></div><p>Searching…</p></div>'
            + '<div class="reid-skel"></div><div class="reid-skel"></div><div class="reid-skel"></div>';
    } else if (kind === 'empty') {
        el.innerHTML = `<div class="reid-state"><div class="reid-state-icon">🔍</div><p>${escapeHtml(msg || 'No results.')}</p></div>`;
    } else { // idle
        el.innerHTML = '<div class="reid-state"><div class="reid-state-icon">💿</div>'
            + '<p>Pick the release this track should be filed under — the same song may appear on a single, an EP, and an album.</p></div>';
    }
}

async function confirmReidentify() {
    if (!reidState.selected || !reidState.trackId) return;
    const btn = document.getElementById('reid-confirm-btn');
    const replace = document.getElementById('reid-replace').checked;
    btn.disabled = true;
    const prev = btn.textContent;
    btn.textContent = 'Staging…';
    try {
        const resp = await fetch('/api/reidentify/apply', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                library_track_id: reidState.trackId,
                source: reidState.selected.source,
                track_id: reidState.selected.track_id,
                replace: replace,
            }),
        });
        const data = await resp.json();
        if (!resp.ok || !data.success) throw new Error(data.error || 'Re-identify failed');
        showToast(`Re-filing under “${data.album_name || 'the chosen release'}” — it'll update after the next import pass.`, 'success');
        closeReidentifyModal();
    } catch (e) {
        showToast(e.message || 'Re-identify failed', 'error');
        btn.disabled = false;
        btn.textContent = prev;
    }
}
