/*
 * SoulSync — Video "Manage Workers" modal (isolated).
 *
 * Reuses the music modal's CSS (.enrichment-manager-modal / .em-* — shared
 * design) but is entirely its own JS: it never calls music functions, targets
 * /api/video/enrichment, shows only the video workers (TMDB/TVDB), and uses
 * movie/show as the entity kinds. Opened by the dashboard "Manage Workers"
 * button via the 'soulsync:video-open-workers' event. Event-delegated (no inline
 * handlers); self-contained IIFE, no globals.
 */
(function () {
    'use strict';

    // `kinds` = the entity kinds each service actually enriches (must match the
    // backend's _ENRICH map). TVDB is shows-only, so it must NOT default to the
    // Movies view — it would query tvdb+movie (always empty) and look broken.
    var WORKERS = [
        { id: 'tmdb', name: 'TMDB', color: '#38bdf8', rgb: '56, 189, 248', kinds: ['movie', 'show'] },
        { id: 'tvdb', name: 'TVDB', color: '#a855f7', rgb: '168, 85, 247', kinds: ['show'] },
        { id: 'omdb', name: 'OMDb', color: '#f5c518', rgb: '245, 197, 24', kinds: ['movie', 'show'] },
        // The YouTube date enricher — no per-kind match queue; its own simple panel.
        { id: 'youtube', name: 'YouTube Dates', color: '#ff3b3b', rgb: '255, 59, 59', kinds: [], glyph: '▶' },
        // Backfill workers — enrich already-identified items by id.
        { id: 'fanart', name: 'fanart.tv', color: '#e4509a', rgb: '228, 80, 154', kinds: ['movie', 'show'], glyph: '🎨' },
        { id: 'opensubtitles', name: 'OpenSubtitles', color: '#22a079', rgb: '34, 160, 121', kinds: ['movie', 'show'], glyph: '💬' },
        { id: 'ryd', name: 'YouTube Votes', color: '#ef4444', rgb: '239, 68, 68', kinds: ['video'], glyph: '👍' },
        { id: 'sponsorblock', name: 'SponsorBlock', color: '#00b4a0', rgb: '0, 180, 160', kinds: ['video'], glyph: '⏭' },
    ];

    function workerDef(id) {
        for (var i = 0; i < WORKERS.length; i++) { if (WORKERS[i].id === id) return WORKERS[i]; }
        return null;
    }
    function defaultKind(id) {
        var w = workerDef(id);
        return (w && w.kinds && w.kinds[0]) || 'movie';
    }
    var LOGOS = {
        tmdb: 'https://www.themoviedb.org/assets/2/v4/logos/v2/blue_square_2-d537fb228cf3ded904ef09b136fe3fec72548ebc1fea3fbbd1ad9e36364db38b.svg',
        tvdb: 'https://www.svgrepo.com/show/443500/brand-tvdb.svg',
    };
    var GLYPH = { movie: '🎬', show: '📺', episode: '🎞️', video: '▶' };
    var KIND_LABEL = { movie: 'Movies', show: 'Shows', episode: 'Episodes', video: 'Videos' };

    var state = {
        open: false, selected: 'tmdb', statuses: {}, breakdown: null,
        unmatched: null, kind: 'movie', page: 0, pageSize: 50,
        statusFilter: 'unmatched', search: '', priority: '', pollTimer: null, searchTimer: null,
    };

    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
    function byId(id) { return document.getElementById(id); }

    // The music modal's circular accent icon chip (.em-icon), reused verbatim so
    // the video rail/hero match it. Logo → <img>, otherwise a colored glyph/letter.
    function workerIcon(w, lg) {
        var cls = 'em-icon' + (lg ? ' em-icon--lg' : '');
        var inner;
        if (LOGOS[w.id]) {
            var filt = w.id === 'tvdb' ? ' style="filter:invert(1)"' : '';
            inner = '<img class="em-icon-img" src="' + LOGOS[w.id] + '" alt=""' + filt + '>';
        } else {
            inner = '<span class="em-icon-letter">' + esc(w.glyph || w.name.charAt(0)) + '</span>';
        }
        return '<span class="' + cls + '" style="--em-accent: ' + w.color + '">' + inner + '</span>';
    }

    // "3d ago" for a SQLite UTC timestamp; '' when never attempted (mirrors music).
    function relTime(value) {
        if (!value) return '';
        var raw = String(value);
        var ts = Date.parse(raw.replace(' ', 'T') + (raw.indexOf('Z') >= 0 ? '' : 'Z'));
        if (isNaN(ts)) return '';
        var secs = Math.max(0, (Date.now() - ts) / 1000);
        if (secs < 60) return 'just now';
        var mins = secs / 60; if (mins < 60) return Math.floor(mins) + 'm ago';
        var hrs = mins / 60; if (hrs < 24) return Math.floor(hrs) + 'h ago';
        var days = hrs / 24; if (days < 30) return Math.floor(days) + 'd ago';
        var months = days / 30; if (months < 12) return Math.floor(months) + 'mo ago';
        return Math.floor(months / 12) + 'y ago';
    }

    function statusInfo(s) {
        if (!s || !s.enabled) return { cls: 'disabled', label: 'Not configured' };
        if (s.running && !s.paused && !s.idle) return { cls: 'running', label: 'Running' };
        if (s.paused) return { cls: 'paused', label: 'Paused' };
        if (s.idle) return { cls: 'idle', label: 'Complete' };
        return { cls: 'idle', label: 'Idle' };
    }
    function overallPct(s) {
        if (!s || !s.progress) return null;
        var m = 0, t = 0;
        for (var k in s.progress) {
            if (Object.prototype.hasOwnProperty.call(s.progress, k)) {
                m += s.progress[k].matched || 0; t += s.progress[k].total || 0;
            }
        }
        return t ? Math.round(m / t * 100) : 0;
    }
    function railSub(s, id) {
        if (!s || !s.enabled) return 'Not configured';
        if (id === 'youtube') {
            if (s.running && s.current_item && s.current_item.name) return s.current_item.name;
            if (s.queued) return s.queued + ' queued';
            return (s.dates_cached || 0) + ' dates cached';
        }
        if (s.idle) return 'All matched';
        if (s.running && !s.paused && s.current_item && s.current_item.name) return s.current_item.name;
        return (s.stats ? (s.stats.pending || 0) : 0) + ' pending';
    }

    // ── data ─────────────────────────────────────────────────────────────────
    function getJSON(url) {
        return fetch(url, { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .catch(function () { return null; });
    }
    function refreshAll() {
        return Promise.all(WORKERS.map(function (w) {
            return getJSON('/api/video/enrichment/' + w.id + '/status').then(function (d) {
                state.statuses[w.id] = d || { enabled: false };
            });
        }));
    }
    function loadBreakdown(id) {
        return getJSON('/api/video/enrichment/' + id + '/breakdown').then(function (d) {
            state.breakdown = d ? d.breakdown : null;
        });
    }
    function loadUnmatched() {
        var id = state.selected;
        var params = new URLSearchParams({
            kind: state.kind, status: state.statusFilter,
            limit: String(state.pageSize), offset: String(state.page * state.pageSize),
        });
        if (state.search) params.set('q', state.search);
        return getJSON('/api/video/enrichment/' + id + '/unmatched?' + params).then(function (d) {
            state.unmatched = d || { total: 0, items: [] };
        });
    }
    function loadPriority() {
        return getJSON('/api/video/enrichment/priority').then(function (d) {
            state.priority = (d && d.priority) || '';
            renderGlobalTabs();
        });
    }

    // ── render ─────────────────────────────────────────────────────────────────
    function renderRail() {
        var rail = byId('vem-rail');
        if (!rail) return;
        rail.innerHTML = WORKERS.map(function (w, i) {
            var s = state.statuses[w.id];
            var info = statusInfo(s);
            var pct = overallPct(s);
            var cov = pct == null ? '' :
                '<span class="em-rail-cov"><span class="em-rail-cov-fill" style="width:' + pct + '%"></span></span>';
            return '<button class="em-worker-row" data-em-select="' + w.id + '" style="--i:' + i + ';--row-accent: ' + w.rgb + '">' +
                workerIcon(w) +
                '<span class="em-worker-meta"><span class="em-worker-name">' + esc(w.name) + '</span>' +
                '<span class="em-worker-sub">' + esc(railSub(s, w.id)) + '</span>' + cov + '</span>' +
                '<span class="em-dot em-dot--' + info.cls + '" title="' + info.label + '"></span></button>';
        }).join('');
        WORKERS.forEach(function (w) {
            var row = rail.querySelector('[data-em-select="' + w.id + '"]');
            if (row) row.classList.toggle('active', w.id === state.selected);
        });
    }

    function renderYoutubePanel(panel) {
        var w = workerDef('youtube') || {};
        var s = state.statuses.youtube || {};
        var prog = (s.progress && s.progress.channels) || {};
        panel.innerHTML =
            '<div class="em-panel-header" id="vem-panel-header">' + heroHtml(w, s) + '</div>' +
            '<div class="vem-yt-about">Fetches real upload dates for the YouTube channels you follow, so the channel page can group videos into year-seasons. Runs in the background when you follow or open a channel — bulk via YouTube\'s own InnerTube API, falling back to per-video; cached so it\'s a one-time pass per channel.</div>' +
            '<div class="vem-yt-stats">' +
                '<div class="vem-yt-stat"><span class="vem-yt-num">' + (prog.matched || 0) + '</span><span class="vem-yt-lbl">channels enriched</span></div>' +
                '<div class="vem-yt-stat"><span class="vem-yt-num">' + (s.dates_cached || 0) + '</span><span class="vem-yt-lbl">dates cached</span></div>' +
                '<div class="vem-yt-stat"><span class="vem-yt-num">' + (s.queued || 0) + '</span><span class="vem-yt-lbl">queued</span></div>' +
            '</div>';
    }

    function renderPanel() {
        var panel = byId('vem-panel');
        if (!panel) return;
        // Theme the panel to the selected worker's accent (like the music modal).
        // Music's shared .em-btn/.em-tab/.em-seg rules key off --accent-rgb, so set
        // it here too (scoped to the panel) — that's what makes them recolor.
        var w = WORKERS.find(function (x) { return x.id === state.selected; }) || WORKERS[0];
        panel.style.setProperty('--em-accent', w.color);
        panel.style.setProperty('--em-accent-rgb', w.rgb);
        panel.style.setProperty('--accent-rgb', w.rgb);
        panel.style.setProperty('--accent-light-rgb', w.rgb);
        if (state.selected === 'youtube') { renderYoutubePanel(panel); return; }
        panel.innerHTML =
            '<div class="em-panel-header" id="vem-panel-header"></div>' +
            '<div class="em-section-label em-section-label--row"><span>Coverage &amp; processing order ' +
            '<span class="em-section-sub">— click a group to enrich it first</span></span>' +
            '<span class="em-coverage-overall" id="vem-coverage-overall"></span></div>' +
            '<div class="em-cards" id="vem-cards"></div>' +
            '<div class="em-unmatched">' +
            '<div class="em-unmatched-controls" id="vem-unmatched-controls"></div>' +
            '<div class="em-unmatched-list" id="vem-unmatched-list"></div>' +
            '<div class="em-pager" id="vem-pager"></div></div>';
        renderHeader();
        renderCards();
        renderControls();
        renderList();
    }

    function heroHtml(w, s) {
        var info = statusInfo(s);
        var live = info.cls === 'running' ? ' em-hero--live' : '';
        var item = s && s.current_item;
        var sub = (item && item.name)
            ? 'Now enriching: <strong>' + esc(item.name) + '</strong>' +
              (item.type ? ' <span class="em-muted">(' + esc(item.type) + ')</span>' : '')
            : '<span class="em-muted">No item processing</span>';
        var pauseLabel = s && s.paused ? '▶ Resume' : '⏸ Pause';
        var go = s && s.paused ? ' em-btn--go' : '';
        return '<div class="em-hero' + live + '">' +
            '<div class="em-hero-glow"></div>' +
            workerIcon(w, true) +
            '<div class="em-ph-titles"><div class="em-ph-nameline">' +
            '<span class="em-ph-name">' + esc(w.name) + ' <span class="em-ph-name-sub">enrichment</span></span>' +
            '<span class="em-pill em-pill--' + info.cls + '">' + info.label + '</span></div>' +
            '<div class="em-ph-sub">' + sub + '</div></div>' +
            '<div class="em-ph-actions">' +
            '<button class="em-btn' + go + '" data-em-pause' + (s && s.enabled ? '' : ' disabled') + '>' + pauseLabel + '</button>' +
            '</div></div>';
    }

    function renderHeader() {
        var host = byId('vem-panel-header');
        if (!host) return;
        var s = state.statuses[state.selected] || {};
        var w = WORKERS.find(function (x) { return x.id === state.selected; }) || {};
        host.innerHTML = heroHtml(w, s);
    }

    function renderCards() {
        var host = byId('vem-cards');
        if (!host) return;
        var bd = state.breakdown;
        if (!bd) { host.innerHTML = ''; return; }
        var kinds = Object.keys(bd);
        host.innerHTML = kinds.map(function (e) {
            var d = bd[e] || {};
            // Errored items are outstanding (retried later) — show them with the
            // pending bucket so the bar/total stay honest.
            var matched = d.matched || 0, nf = d.not_found || 0;
            var pend = (d.pending || 0) + (d.errors || 0);
            var total = matched + nf + pend;
            var pct = total ? Math.round(matched / total * 100) : 0;
            var seg = function (n) { return total ? (n / total) * 100 : 0; };
            var left = nf + pend;
            var isPinned = state.priority === e && (e === 'movie' || e === 'show');
            var isDone = total > 0 && left === 0;
            var cls = 'em-card' +
                (e === state.kind ? ' em-card--current' : '') +
                (isPinned ? ' em-card--pinned' : '') +
                (isDone ? ' em-card--done' : '');
            var badge = isPinned
                ? '<span class="em-card-badge em-card-badge--pin">📌 First</span>'
                : isDone
                    ? '<span class="em-card-badge em-card-badge--done">✓ Done</span>'
                    : '<span class="em-card-badge">' + left.toLocaleString() + ' left</span>';
            return '<button class="' + cls + '" data-em-kind="' + e + '">' +
                '<div class="em-card-top"><span class="em-card-glyph">' + (GLYPH[e] || '•') + '</span>' +
                '<span class="em-card-title">' + (KIND_LABEL[e] || e) + '</span>' + badge +
                '<span class="em-card-pct">' + pct + '<span class="em-stat-pct-sym">%</span></span></div>' +
                '<div class="em-seg"><div class="em-seg-fill em-seg--matched" style="width:' + seg(matched) + '%"></div>' +
                '<div class="em-seg-fill em-seg--nf" style="width:' + seg(nf) + '%"></div>' +
                '<div class="em-seg-fill em-seg--pend" style="width:' + seg(pend) + '%"></div></div>' +
                '<div class="em-stat-legend"><span class="em-leg em-leg--matched"><i></i>' + matched + '</span>' +
                '<span class="em-leg em-leg--nf"><i></i>' + nf + '</span>' +
                '<span class="em-leg em-leg--pend"><i></i>' + pend + '</span></div></button>';
        }).join('');
        var overall = byId('vem-coverage-overall');
        if (overall) {
            var m = 0, t = 0;
            kinds.forEach(function (e) {
                var d = bd[e] || {}; m += d.matched || 0;
                t += (d.matched || 0) + (d.not_found || 0) + (d.pending || 0) + (d.errors || 0);
            });
            overall.innerHTML = t ? '<strong>' + (t ? Math.round(m / t * 100) : 0) + '%</strong> matched · '
                + m + ' of ' + t : '';
        }
    }

    function renderGlobalTabs() {
        var host = byId('vem-global-tabs');
        if (!host) return;
        var btns = host.querySelectorAll('[data-em-priority]');
        for (var i = 0; i < btns.length; i++) {
            btns[i].classList.toggle('active', btns[i].getAttribute('data-em-priority') === state.priority);
        }
    }

    function renderControls() {
        var host = byId('vem-unmatched-controls');
        if (!host) return;
        var total = state.unmatched ? state.unmatched.total : null;
        var opt = function (v, label) {
            return '<option value="' + v + '"' + (state.statusFilter === v ? ' selected' : '') + '>' + label + '</option>';
        };
        var isEpisode = state.kind === 'episode';
        host.innerHTML =
            '<div class="em-unmatched-bar">' +
            '<div class="em-section-label em-section-label--inline">' +
            (isEpisode ? 'Episodes missing art' : (KIND_LABEL[state.kind] || state.kind) + ' not yet matched') +
            (total != null ? '<span class="em-count">' + total.toLocaleString() + '</span>' : '') +
            (isEpisode ? '' : '<button class="em-btn em-btn--sm em-btn--ghost" data-em-retry-all>↻ Retry all failed</button>') +
            '</div>' +
            '<div class="em-filter-row">' +
            (isEpisode ? '' :
                '<select class="em-select" data-em-status>' + opt('unmatched', 'All unmatched') +
                opt('not_found', 'Not found') + opt('pending', 'Pending') + '</select>') +
            '<div class="em-search-wrap"><span class="em-search-ico">⌕</span>' +
            '<input class="em-search" type="text" placeholder="Search…" value="' + esc(state.search) + '" data-em-search></div>' +
            '</div></div>' +
            '<div class="em-hint">' + (isEpisode
                ? 'Episode stills backfill automatically once their show is matched.'
                : 'Failed lookups auto-retry later · “Retry” re-queues immediately.') + '</div>';
    }

    function renderList() {
        var host = byId('vem-unmatched-list');
        if (!host) return;
        var data = state.unmatched || { items: [], total: 0 };
        var isEpisode = state.kind === 'episode';
        if (!data.items.length) {
            host.innerHTML = '<div class="em-empty"><div class="em-empty-emoji">' +
                (state.statusFilter === 'unmatched' ? '🎉' : '🔍') + '</div>' +
                '<div>' + (state.statusFilter === 'unmatched'
                    ? 'Everything here is matched for this source.'
                    : 'Nothing matches this filter.') + '</div></div>';
        } else {
            // No per-item status flag, so colour the stripe from the active filter.
            var rowCls = state.statusFilter === 'not_found' ? 'em-row em-row--nf' : 'em-row em-row--pend';
            host.innerHTML = data.items.map(function (it) {
                var glyph = GLYPH[state.kind] || '•';
                var pic = it.has_poster
                    ? '<img class="em-row-img-pic" src="/api/video/poster/' + state.kind + '/' + it.id + '" alt="" loading="lazy" onerror="this.remove()">'
                    : '';
                var img = '<div class="em-row-img em-row-img--ph">' + glyph + pic + '</div>';
                var rel = relTime(it.last_attempted);
                var meta = [];
                if (it.year) meta.push('<span class="em-muted">' + esc(String(it.year)) + '</span>');
                meta.push('<span class="em-muted">' + (rel ? 'tried ' + rel : 'never tried') + '</span>');
                var action = isEpisode ? ''
                    : '<button class="em-btn em-btn--sm em-btn--ghost" data-em-retry-item="' + it.id + '">Retry</button>';
                return '<div class="' + rowCls + '">' + img +
                    '<div class="em-row-info"><div class="em-row-name" title="' + esc(it.title) + '">' + esc(it.title) + '</div>' +
                    '<div class="em-row-meta">' + meta.join(' ') + '</div></div>' +
                    '<div class="em-row-actions">' + action + '</div></div>';
            }).join('');
        }
        var pager = byId('vem-pager');
        if (pager) {
            var total = data.total || 0;
            var from = total ? state.page * state.pageSize + 1 : 0;
            var to = Math.min((state.page + 1) * state.pageSize, total);
            pager.innerHTML = total > state.pageSize
                ? '<button class="em-btn em-btn--sm" data-em-page="prev"' + (state.page <= 0 ? ' disabled' : '') + '>‹ Prev</button>' +
                  '<span class="em-muted">' + from + '–' + to + ' of ' + total.toLocaleString() + '</span>' +
                  '<button class="em-btn em-btn--sm" data-em-page="next"' + (to >= total ? ' disabled' : '') + '>Next ›</button>'
                : '';
        }
    }

    // ── selection / actions ────────────────────────────────────────────────────
    function selectWorker(id) {
        state.selected = id; state.breakdown = null; state.unmatched = null;
        state.kind = defaultKind(id); state.page = 0; state.search = '';
        renderRail(); renderPanel();
        if (id === 'youtube') return;   // date worker has no breakdown/unmatched
        Promise.all([loadBreakdown(id), loadUnmatched()]).then(function () { renderPanel(); });
    }
    function switchKind(kind) {
        state.kind = kind; state.page = 0;
        renderCards();
        // Like the music worker manager: clicking a coverage group also "pins" it
        // and RE-QUEUES its previously-failed items (not_found/error -> pending) so
        // the worker sweeps ALL unmatched, not just never-tried ones — otherwise
        // failed items sit forever (in the retry cooldown) and the worker reports
        // "all matched". (Episodes are a sync cascade, not a match queue.)
        if (kind === 'movie' || kind === 'show') {
            setPriority(kind);
            requeueFailed(kind);
        } else {
            loadUnmatched().then(function () { renderControls(); renderList(); });
        }
    }
    function requeueFailed(kind) {
        fetch('/api/video/enrichment/' + state.selected + '/retry', {
            method: 'POST', headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify({ kind: kind, scope: 'failed' }),
        }).then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                var n = (d && d.reset) || 0;
                if (n && typeof showToast === 'function') {
                    showToast('Re-queued ' + n + ' previously-failed ' +
                        (KIND_LABEL[kind] || kind).toLowerCase(), 'success');
                }
                return Promise.all([loadBreakdown(state.selected), loadUnmatched()]);
            })
            .then(function () { renderCards(); renderControls(); renderList(); })
            .catch(function () { loadUnmatched().then(function () { renderControls(); renderList(); }); });
    }
    function togglePause() {
        var s = state.statuses[state.selected] || {};
        if (!s.enabled) return;
        var action = s.paused ? 'resume' : 'pause';
        fetch('/api/video/enrichment/' + state.selected + '/' + action,
            { method: 'POST', headers: { 'Accept': 'application/json' } })
            .then(function () { return refreshAll(); })
            .then(function () { renderRail(); renderHeader(); });
    }
    function retry(scope, itemId) {
        fetch('/api/video/enrichment/' + state.selected + '/retry', {
            method: 'POST', headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify({ kind: state.kind, scope: scope, item_id: itemId }),
        }).then(function () {
            return Promise.all([loadBreakdown(state.selected), loadUnmatched()]);
        }).then(function () { renderCards(); renderList(); });
    }
    function setPriority(kind) {
        state.priority = kind;
        renderGlobalTabs();
        fetch('/api/video/enrichment/priority', {
            method: 'POST', headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify({ priority: kind }),
        }).catch(function () { /* ignore */ });
    }
    function onSearchInput(value) {
        state.search = value; state.page = 0;
        if (state.searchTimer) clearTimeout(state.searchTimer);
        state.searchTimer = setTimeout(function () {
            loadUnmatched().then(function () { renderControls(); renderList(); restoreSearchFocus(); });
        }, 300);
    }
    function restoreSearchFocus() {
        var inp = document.querySelector('#vem-overlay [data-em-search]');
        if (inp) { inp.focus(); var v = inp.value; inp.value = ''; inp.value = v; }
    }
    function setStatusFilter(value) {
        state.statusFilter = value; state.page = 0;
        loadUnmatched().then(function () { renderControls(); renderList(); });
    }

    // ── open/close + delegation ────────────────────────────────────────────────
    function ensureOverlay() {
        var overlay = byId('vem-overlay');
        if (overlay) return overlay;
        overlay = document.createElement('div');
        overlay.id = 'vem-overlay';
        overlay.className = 'modal-overlay em-overlay hidden';
        overlay.innerHTML =
            '<div class="enrichment-manager-modal" role="dialog" aria-modal="true" aria-label="Manage Video Enrichment Workers">' +
            '<div class="em-topbar"><div class="em-topbar-icon"><img src="/static/trans2.png" alt="" class="em-topbar-logo"></div>' +
            '<div class="em-topbar-titles"><h3 class="em-topbar-title">Video Enrichment Workers</h3>' +
            '<div class="em-topbar-sub">Match your library to TMDB &amp; TVDB</div></div>' +
            '<div class="em-global"><span class="em-global-label">Process first<br><span>everywhere</span></span>' +
            '<div class="em-global-tabs" id="vem-global-tabs">' +
            '<button data-em-priority="movie">Movies</button>' +
            '<button data-em-priority="show">Shows</button>' +
            '<button data-em-priority="" class="em-global-auto">Auto</button></div></div>' +
            '<div class="em-topbar-actions"><button class="em-icon-btn" data-em-refresh title="Refresh">⟳</button>' +
            '<button class="em-icon-btn em-icon-btn--close" data-em-close title="Close">&times;</button></div></div>' +
            '<div class="em-body"><div class="em-rail" id="vem-rail"></div><div class="em-panel" id="vem-panel"></div></div></div>';
        overlay.addEventListener('click', onOverlayClick);
        overlay.addEventListener('input', function (e) {
            if (e.target.hasAttribute('data-em-search')) onSearchInput(e.target.value);
        });
        overlay.addEventListener('change', function (e) {
            if (e.target.hasAttribute('data-em-status')) setStatusFilter(e.target.value);
        });
        document.body.appendChild(overlay);
        return overlay;
    }

    function onOverlayClick(e) {
        var overlay = byId('vem-overlay');
        if (e.target === overlay) { close(); return; }
        var t = e.target.closest('[data-em-select],[data-em-pause],[data-em-kind],[data-em-retry-all],' +
            '[data-em-retry-item],[data-em-page],[data-em-refresh],[data-em-close],[data-em-priority]');
        if (!t) return;
        if (t.hasAttribute('data-em-close')) close();
        else if (t.hasAttribute('data-em-priority')) setPriority(t.getAttribute('data-em-priority'));
        else if (t.hasAttribute('data-em-refresh')) { refreshAll().then(renderRail); selectWorker(state.selected); }
        else if (t.hasAttribute('data-em-select')) selectWorker(t.getAttribute('data-em-select'));
        else if (t.hasAttribute('data-em-pause')) togglePause();
        else if (t.hasAttribute('data-em-kind')) switchKind(t.getAttribute('data-em-kind'));
        else if (t.hasAttribute('data-em-retry-all')) retry('failed', null);
        else if (t.hasAttribute('data-em-retry-item')) retry('item', Number(t.getAttribute('data-em-retry-item')));
        else if (t.hasAttribute('data-em-page')) {
            state.page += (t.getAttribute('data-em-page') === 'next') ? 1 : -1;
            if (state.page < 0) state.page = 0;
            loadUnmatched().then(renderList);
        }
    }

    function open() {
        var overlay = ensureOverlay();
        overlay.classList.remove('hidden', 'em-closing');
        // Re-trigger the music modal's entrance animation even on reuse. Drop the
        // class once it's played so the 3s rail re-render doesn't replay the
        // per-row stagger (.em-in .em-worker-row) on every poll.
        var modal = overlay.querySelector('.enrichment-manager-modal');
        if (modal) {
            modal.classList.remove('em-in'); void modal.offsetWidth; modal.classList.add('em-in');
            setTimeout(function () { modal.classList.remove('em-in'); }, 700);
        }
        document.body.classList.add('em-scroll-lock');
        state.open = true;
        refreshAll().then(function () {
            renderRail();
            loadPriority();
            selectWorker(state.selected);
        });
        if (state.pollTimer) clearInterval(state.pollTimer);
        state.pollTimer = setInterval(function () {
            if (!state.open) return;
            getJSON('/api/video/enrichment/' + state.selected + '/status').then(function (d) {
                if (d) {
                    state.statuses[state.selected] = d;
                    if (state.selected === 'youtube') renderPanel(); else renderHeader();
                    renderRail();
                }
            });
        }, 3000);
    }
    function close() {
        var overlay = byId('vem-overlay');
        state.open = false;
        if (state.pollTimer) { clearInterval(state.pollTimer); state.pollTimer = null; }
        document.body.classList.remove('em-scroll-lock');
        if (!overlay) return;
        // Brief fade/scale-out (music's .em-closing), then hide.
        overlay.classList.add('em-closing');
        setTimeout(function () { overlay.classList.add('hidden'); overlay.classList.remove('em-closing'); }, 170);
    }

    document.addEventListener('soulsync:video-open-workers', open);
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && state.open) close();
    });
})();
