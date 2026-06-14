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

    var WORKERS = [
        { id: 'tmdb', name: 'TMDB', color: '#38bdf8', rgb: '56, 189, 248' },
        { id: 'tvdb', name: 'TVDB', color: '#a855f7', rgb: '168, 85, 247' },
    ];
    var LOGOS = {
        tmdb: 'https://www.themoviedb.org/assets/2/v4/logos/v2/blue_square_2-d537fb228cf3ded904ef09b136fe3fec72548ebc1fea3fbbd1ad9e36364db38b.svg',
        tvdb: 'https://www.svgrepo.com/show/443500/brand-tvdb.svg',
    };
    var GLYPH = { movie: '🎬', show: '📺' };
    var KIND_LABEL = { movie: 'Movies', show: 'Shows' };

    var state = {
        open: false, selected: 'tmdb', statuses: {}, breakdown: null,
        unmatched: null, kind: 'movie', page: 0, pageSize: 50,
        statusFilter: 'unmatched', pollTimer: null,
    };

    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
    function byId(id) { return document.getElementById(id); }

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
    function railSub(s) {
        if (!s || !s.enabled) return 'Not configured';
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
        return getJSON('/api/video/enrichment/' + id + '/unmatched?' + params).then(function (d) {
            state.unmatched = d || { total: 0, items: [] };
        });
    }

    // ── render ─────────────────────────────────────────────────────────────────
    function renderRail() {
        var rail = byId('vem-rail');
        if (!rail) return;
        rail.innerHTML = WORKERS.map(function (w) {
            var s = state.statuses[w.id];
            var info = statusInfo(s);
            var pct = overallPct(s);
            var cov = pct == null ? '' :
                '<span class="em-rail-cov"><span class="em-rail-cov-fill" style="width:' + pct + '%"></span></span>';
            return '<button class="em-worker-row" data-em-select="' + w.id + '" style="--row-accent: ' + w.rgb + '">' +
                '<span class="em-worker-icon"><img class="vem-logo vem-logo--' + w.id + '" src="' + LOGOS[w.id] + '" alt=""></span>' +
                '<span class="em-worker-meta"><span class="em-worker-name">' + esc(w.name) + '</span>' +
                '<span class="em-worker-sub">' + esc(railSub(s)) + '</span>' + cov + '</span>' +
                '<span class="em-dot em-dot--' + info.cls + '" title="' + info.label + '"></span></button>';
        }).join('');
        WORKERS.forEach(function (w) {
            var row = rail.querySelector('[data-em-select="' + w.id + '"]');
            if (row) row.classList.toggle('active', w.id === state.selected);
        });
    }

    function renderPanel() {
        var panel = byId('vem-panel');
        if (!panel) return;
        // Theme the panel to the selected worker's accent (like the music modal).
        var w = WORKERS.find(function (x) { return x.id === state.selected; }) || WORKERS[0];
        panel.style.setProperty('--em-accent', w.color);
        panel.style.setProperty('--em-accent-rgb', w.rgb);
        panel.innerHTML =
            '<div class="em-panel-header" id="vem-panel-header"></div>' +
            '<div class="em-section-label em-section-label--row"><span>Coverage</span>' +
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

    function renderHeader() {
        var host = byId('vem-panel-header');
        if (!host) return;
        var s = state.statuses[state.selected] || {};
        var info = statusInfo(s);
        var w = WORKERS.find(function (x) { return x.id === state.selected; }) || {};
        var pauseLabel = s.paused ? '▶ Resume' : '⏸ Pause';
        var current = (s.current_item && s.current_item.name)
            ? esc((s.current_item.type || '') + ': ' + s.current_item.name) : '';
        host.innerHTML =
            '<div class="em-ph-main"><span class="em-dot em-dot--' + info.cls + '"></span>' +
            '<strong>' + esc(w.name) + '</strong>' +
            '<span class="em-ph-status">' + info.label + '</span>' +
            (current ? '<span class="em-ph-current">' + current + '</span>' : '') + '</div>' +
            '<button class="em-pause-btn" data-em-pause' + (s.enabled ? '' : ' disabled') + '>' + pauseLabel + '</button>';
    }

    function renderCards() {
        var host = byId('vem-cards');
        if (!host) return;
        var bd = state.breakdown;
        if (!bd) { host.innerHTML = ''; return; }
        var kinds = Object.keys(bd);
        host.innerHTML = kinds.map(function (e) {
            var d = bd[e] || {};
            var total = (d.matched || 0) + (d.not_found || 0) + (d.pending || 0);
            var matched = d.matched || 0, nf = d.not_found || 0, pend = d.pending || 0;
            var pct = total ? Math.round(matched / total * 100) : 0;
            var seg = function (n) { return total ? (n / total) * 100 : 0; };
            var active = e === state.kind ? ' em-card--current' : '';
            return '<button class="em-card' + active + '" data-em-kind="' + e + '">' +
                '<div class="em-card-top"><span class="em-card-glyph">' + (GLYPH[e] || '•') + '</span>' +
                '<span class="em-card-title">' + (KIND_LABEL[e] || e) + '</span>' +
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
                t += (d.matched || 0) + (d.not_found || 0) + (d.pending || 0);
            });
            overall.innerHTML = t ? '<strong>' + (t ? Math.round(m / t * 100) : 0) + '%</strong> matched · '
                + m + ' of ' + t : '';
        }
    }

    function renderControls() {
        var host = byId('vem-unmatched-controls');
        if (!host) return;
        host.innerHTML =
            '<span class="em-section-sub">' + (KIND_LABEL[state.kind] || state.kind) +
            ' not yet matched</span>' +
            '<button class="em-retry-all-btn" data-em-retry-all>Retry failed</button>';
    }

    function renderList() {
        var host = byId('vem-unmatched-list');
        if (!host) return;
        var data = state.unmatched || { items: [], total: 0 };
        if (!data.items.length) {
            host.innerHTML = '<div class="em-empty">Nothing unmatched here 🎉</div>';
        } else {
            host.innerHTML = data.items.map(function (it) {
                var poster = it.has_poster
                    ? '<img class="em-item-img" src="/api/video/poster/' + state.kind + '/' + it.id + '" alt="" loading="lazy">'
                    : '<span class="em-item-img em-item-img--ph">' + (GLYPH[state.kind] || '•') + '</span>';
                return '<div class="em-item">' + poster +
                    '<span class="em-item-meta"><span class="em-item-name">' + esc(it.title) + '</span>' +
                    '<span class="em-item-sub">' + (it.year || '') + '</span></span>' +
                    '<button class="em-item-retry" data-em-retry-item="' + it.id + '">Retry</button></div>';
            }).join('');
        }
        var pager = byId('vem-pager');
        if (pager) {
            var pages = Math.max(1, Math.ceil((data.total || 0) / state.pageSize));
            pager.innerHTML = (data.total || 0) > state.pageSize
                ? '<button class="em-pg" data-em-page="prev"' + (state.page <= 0 ? ' disabled' : '') + '>‹</button>' +
                  '<span class="em-pg-info">' + (state.page + 1) + ' / ' + pages + '</span>' +
                  '<button class="em-pg" data-em-page="next"' + (state.page + 1 >= pages ? ' disabled' : '') + '>›</button>'
                : '';
        }
    }

    // ── selection / actions ────────────────────────────────────────────────────
    function selectWorker(id) {
        state.selected = id; state.breakdown = null; state.unmatched = null;
        state.kind = 'movie'; state.page = 0;
        renderRail(); renderPanel();
        Promise.all([loadBreakdown(id), loadUnmatched()]).then(function () { renderPanel(); });
    }
    function switchKind(kind) {
        state.kind = kind; state.page = 0;
        renderCards();
        loadUnmatched().then(function () { renderControls(); renderList(); });
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
            '<div class="em-topbar-actions"><button class="em-icon-btn" data-em-refresh title="Refresh">⟳</button>' +
            '<button class="em-icon-btn em-icon-btn--close" data-em-close title="Close">&times;</button></div></div>' +
            '<div class="em-body"><div class="em-rail" id="vem-rail"></div><div class="em-panel" id="vem-panel"></div></div></div>';
        overlay.addEventListener('click', onOverlayClick);
        document.body.appendChild(overlay);
        return overlay;
    }

    function onOverlayClick(e) {
        var overlay = byId('vem-overlay');
        if (e.target === overlay) { close(); return; }
        var t = e.target.closest('[data-em-select],[data-em-pause],[data-em-kind],[data-em-retry-all],' +
            '[data-em-retry-item],[data-em-page],[data-em-refresh],[data-em-close]');
        if (!t) return;
        if (t.hasAttribute('data-em-close')) close();
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
        overlay.classList.remove('hidden');
        document.body.classList.add('em-scroll-lock');
        state.open = true;
        refreshAll().then(function () {
            renderRail();
            selectWorker(state.selected);
        });
        if (state.pollTimer) clearInterval(state.pollTimer);
        state.pollTimer = setInterval(function () {
            if (!state.open) return;
            getJSON('/api/video/enrichment/' + state.selected + '/status').then(function (d) {
                if (d) { state.statuses[state.selected] = d; renderHeader(); renderRail(); }
            });
        }, 3000);
    }
    function close() {
        var overlay = byId('vem-overlay');
        state.open = false;
        if (state.pollTimer) { clearInterval(state.pollTimer); state.pollTimer = null; }
        if (overlay) overlay.classList.add('hidden');
        document.body.classList.remove('em-scroll-lock');
    }

    document.addEventListener('soulsync:video-open-workers', open);
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && state.open) close();
    });
})();
