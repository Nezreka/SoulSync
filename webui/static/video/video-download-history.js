/*
 * SoulSync — Video Download History modal (isolated).
 *
 * A permanent, beautiful timeline of every grab SoulSync completed (movies +
 * episodes), backed by /api/video/downloads/history. Self-contained: builds its
 * own overlay DOM, no shell in index.html. Opened by [data-vdh-open]; rows expand
 * in place to reveal the full rich detail (poster, release, source, path, codecs).
 */
(function () {
    'use strict';

    var LIMIT = 40;
    var state = { open: false, tab: 'all', search: '', page: 1, loading: false,
                  counts: { movie: 0, show: 0, total: 0 }, items: [], pages: 1 };
    var el = null, searchTimer = null;

    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
    function fmtSize(b) {
        b = Number(b) || 0;
        if (b <= 0) return '';
        var u = ['B', 'KB', 'MB', 'GB', 'TB'], i = Math.floor(Math.log(b) / Math.log(1024));
        return (b / Math.pow(1024, i)).toFixed(i >= 3 ? 1 : 0) + ' ' + u[i];
    }
    var MO = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    function fmtWhen(iso) {
        if (!iso) return '';
        var p = String(iso).replace('T', ' ').split(/[- :]/);
        if (p.length < 3) return esc(iso);
        var mo = MO[(+p[1] || 1) - 1], d = +p[2], y = +p[0];
        var t = (p.length >= 5) ? (' · ' + p[3] + ':' + p[4]) : '';
        return mo + ' ' + d + ', ' + y + t;
    }
    function relDay(iso) {
        if (!iso) return '';
        var p = String(iso).replace('T', ' ').split(/[- :]/);
        if (p.length < 3) return '';
        return p[0] + '-' + p[1] + '-' + p[2];
    }
    function dayLabel(iso) {
        var p = String(iso || '').replace('T', ' ').split(/[- :]/);
        if (p.length < 3) return 'Earlier';
        return MO[(+p[1] || 1) - 1] + ' ' + (+p[2]) + ', ' + p[0];
    }

    var OUTCOME = {
        completed: ['Imported', 'vdh-oc--ok'], import_failed: ['Needs import', 'vdh-oc--warn'],
        failed: ['Failed', 'vdh-oc--fail'], cancelled: ['Cancelled', 'vdh-oc--muted'],
    };

    function ensureDom() {
        if (el) return el;
        el = document.createElement('div');
        el.className = 'vdh-overlay';
        el.innerHTML =
            '<div class="vdh-modal" role="dialog" aria-modal="true" aria-label="Download history">' +
                '<div class="vdh-head">' +
                    '<div class="vdh-head-titles">' +
                        '<h2 class="vdh-title">Download History</h2>' +
                        '<p class="vdh-sub" data-vdh-sub></p>' +
                    '</div>' +
                    '<button class="vdh-close" type="button" data-vdh-close aria-label="Close">&times;</button>' +
                '</div>' +
                '<div class="vdh-toolbar">' +
                    '<div class="vdh-tabs" role="tablist">' +
                        '<button class="vdh-tab vdh-tab--on" type="button" data-vdh-tab="all">All <span class="vdh-tab-n" data-vdh-c-all>0</span></button>' +
                        '<button class="vdh-tab" type="button" data-vdh-tab="movie">Movies <span class="vdh-tab-n" data-vdh-c-movie>0</span></button>' +
                        '<button class="vdh-tab" type="button" data-vdh-tab="show">TV <span class="vdh-tab-n" data-vdh-c-show>0</span></button>' +
                    '</div>' +
                    '<div class="vdh-search">' +
                        '<svg class="vdh-search-ic" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>' +
                        '<input type="text" class="vdh-search-input" data-vdh-search placeholder="Search history…" autocomplete="off" spellcheck="false">' +
                    '</div>' +
                    '<button class="vdh-clear" type="button" data-vdh-clear title="Clear the whole download history">Clear</button>' +
                '</div>' +
                '<div class="vdh-body" data-vdh-body></div>' +
                '<div class="vdh-foot" data-vdh-foot hidden>' +
                    '<button class="vdh-more" type="button" data-vdh-more>Load more</button>' +
                '</div>' +
            '</div>';
        document.body.appendChild(el);

        el.addEventListener('click', function (e) {
            if (e.target === el || e.target.closest('[data-vdh-close]')) { close(); return; }
            var tab = e.target.closest('[data-vdh-tab]');
            if (tab) { setTab(tab.getAttribute('data-vdh-tab')); return; }
            if (e.target.closest('[data-vdh-more]')) { state.page++; load(true); return; }
            // Re-download: forget this grab so the scans re-add + re-grab it.
            var redl = e.target.closest('[data-vdh-redl]');
            if (redl) {
                e.stopPropagation();
                redl.disabled = true;
                fetch('/api/video/downloads/history/' + encodeURIComponent(redl.getAttribute('data-vdh-redl')),
                    { method: 'DELETE', headers: { Accept: 'application/json' } })
                    .then(function (r) { return r.json(); })
                    .then(function (d) {
                        if (d && d.success) {
                            if (typeof showToast === 'function') showToast("Forgotten — it'll re-download on the next scan", 'success');
                            var r2 = redl.closest('[data-vdh-row]'); if (r2) r2.remove();
                        } else { redl.disabled = false; }
                    }).catch(function () { redl.disabled = false; });
                return;
            }
            // Clear all history (guarded — it's permanent).
            if (e.target.closest('[data-vdh-clear]')) {
                var go = function () {
                    fetch('/api/video/downloads/history/clear',
                        { method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' }, body: '{}' })
                        .then(function (r) { return r.json(); })
                        .then(function (d) {
                            if (typeof showToast === 'function') showToast('Cleared ' + ((d && d.removed) || 0) + ' from history', 'info');
                            state.page = 1; load();
                        });
                };
                if (typeof showConfirmDialog === 'function') {
                    showConfirmDialog({ title: 'Clear download history', message: 'Forget the entire download history? Items still wanted may re-download.', confirmText: 'Clear', destructive: true })
                        .then(function (ok) { if (ok) go(); });
                } else if (window.confirm('Clear the entire download history?')) { go(); }
                return;
            }
            var row = e.target.closest('[data-vdh-row]');
            if (row) { row.classList.toggle('vdh-row--open'); }
        });
        var si = el.querySelector('[data-vdh-search]');
        si.addEventListener('input', function () {
            if (searchTimer) clearTimeout(searchTimer);
            searchTimer = setTimeout(function () {
                state.search = si.value.trim(); state.page = 1; load(false);
            }, 250);
        });
        return el;
    }

    function setTab(tab) {
        if (tab === state.tab) return;
        state.tab = tab; state.page = 1;
        var tabs = el.querySelectorAll('[data-vdh-tab]');
        for (var i = 0; i < tabs.length; i++)
            tabs[i].classList.toggle('vdh-tab--on', tabs[i].getAttribute('data-vdh-tab') === tab);
        load(false);
    }

    function rowHtml(it) {
        var isShow = it.kind === 'show';
        var oc = OUTCOME[it.outcome] || OUTCOME.completed;
        var sxe = (it.season_number != null && it.episode_number != null)
            ? 'S' + String(it.season_number).padStart(2, '0') + 'E' + String(it.episode_number).padStart(2, '0') : '';
        var sub = [sxe, it.year, it.quality_label, it.resolution, it.video_codec, fmtSize(it.size_bytes)]
            .filter(Boolean).join(' · ');
        var poster = it.poster_url
            ? '<img class="vdh-poster" src="' + esc(it.poster_url) + '" alt="" loading="lazy" onerror="this.classList.add(\'vdh-poster--none\')">'
            : '<span class="vdh-poster vdh-poster--none">' + (isShow ? '📺' : '🎬') + '</span>';

        function dl(label, val) {
            return val ? '<div class="vdh-d"><span class="vdh-d-k">' + label + '</span><span class="vdh-d-v">' + esc(val) + '</span></div>' : '';
        }
        var detail =
            '<div class="vdh-detail">' +
                dl('Release', it.release_title) +
                dl('Source', [it.source, it.username].filter(Boolean).join(' · ')) +
                dl('Codec', [it.resolution, it.video_codec].filter(Boolean).join(' · ')) +
                dl('Size', fmtSize(it.size_bytes)) +
                dl('Grabbed', fmtWhen(it.grabbed_at)) +
                dl('Finished', fmtWhen(it.completed_at)) +
                dl('Path', it.dest_path) +
                (it.error ? '<div class="vdh-d vdh-d--err"><span class="vdh-d-k">Error</span><span class="vdh-d-v">' + esc(it.error) + '</span></div>' : '') +
                '<div class="vdh-detail-act">' +
                    '<button class="vdh-redl" type="button" data-vdh-redl="' + esc(it.id) +
                        '" title="Forget this grab so it re-downloads on the next scan">&#8635; Re-download</button>' +
                '</div>' +
            '</div>';

        return '<div class="vdh-row" data-vdh-row data-id="' + esc(it.id) + '">' +
            '<div class="vdh-row-main">' +
                poster +
                '<div class="vdh-row-info">' +
                    '<div class="vdh-row-title">' + esc(it.title || it.release_title || 'Unknown') +
                        (sxe ? ' <span class="vdh-sxe">' + esc(sxe) + '</span>' : '') + '</div>' +
                    '<div class="vdh-row-sub">' + esc(sub) + '</div>' +
                '</div>' +
                '<div class="vdh-row-right">' +
                    '<span class="vdh-oc ' + oc[1] + '">' + oc[0] + '</span>' +
                    '<span class="vdh-when">' + esc(fmtWhen(it.completed_at)) + '</span>' +
                    '<span class="vdh-chev" aria-hidden="true">›</span>' +
                '</div>' +
            '</div>' + detail +
        '</div>';
    }

    function render() {
        var body = el.querySelector('[data-vdh-body]');
        if (!state.items.length) {
            body.innerHTML = '<div class="vdh-empty">' +
                '<div class="vdh-empty-ic">📦</div>' +
                '<div class="vdh-empty-t">' + (state.search ? 'No matches' : 'Nothing here yet') + '</div>' +
                '<div class="vdh-empty-s">' + (state.search ? 'Try a different search.' : 'Grabs you complete will be recorded here forever.') + '</div></div>';
            return;
        }
        // Group rows under day headers (timeline feel).
        var html = '', lastDay = null;
        state.items.forEach(function (it) {
            var d = relDay(it.completed_at || it.grabbed_at);
            if (d !== lastDay) {
                lastDay = d;
                html += '<div class="vdh-day">' + esc(dayLabel(it.completed_at || it.grabbed_at)) + '</div>';
            }
            html += rowHtml(it);
        });
        body.innerHTML = html;
    }

    function setCounts(c) {
        state.counts = c || state.counts;
        var q = function (s) { return el.querySelector(s); };
        q('[data-vdh-c-all]').textContent = state.counts.total || 0;
        q('[data-vdh-c-movie]').textContent = state.counts.movie || 0;
        q('[data-vdh-c-show]').textContent = state.counts.show || 0;
        var sub = q('[data-vdh-sub]');
        sub.textContent = (state.counts.total || 0) + ' grab' + (state.counts.total === 1 ? '' : 's') +
            ' · ' + (state.counts.movie || 0) + ' movies · ' + (state.counts.show || 0) + ' episodes';
        updateBadge(state.counts.total || 0);
    }

    function updateBadge(n) {
        document.querySelectorAll('[data-vdh-count]').forEach(function (b) {
            b.textContent = n; b.hidden = !n;
        });
    }

    function load(append) {
        if (state.loading) return;
        state.loading = true;
        var body = el.querySelector('[data-vdh-body]');
        if (!append) body.classList.add('vdh-body--loading');
        var params = new URLSearchParams({ page: state.page, limit: LIMIT, search: state.search });
        if (state.tab !== 'all') params.set('kind', state.tab);
        fetch('/api/video/downloads/history?' + params.toString(), { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                state.loading = false;
                body.classList.remove('vdh-body--loading');
                if (!d || !d.success) { if (!append) { state.items = []; render(); } return; }
                setCounts(d.counts);
                state.pages = (d.pagination && d.pagination.total_pages) || 1;
                state.items = append ? state.items.concat(d.items || []) : (d.items || []);
                render();
                var foot = el.querySelector('[data-vdh-foot]');
                foot.hidden = state.page >= state.pages;
            })
            .catch(function () {
                state.loading = false; body.classList.remove('vdh-body--loading');
                if (!append) { state.items = []; render(); }
            });
    }

    function open() {
        ensureDom();
        state.open = true; state.page = 1; state.search = '';
        var si = el.querySelector('[data-vdh-search]'); if (si) si.value = '';
        el.classList.add('vdh-overlay--on');
        document.body.classList.add('vdh-locked');
        load(false);
    }
    function close() {
        if (!el) return;
        state.open = false;
        el.classList.remove('vdh-overlay--on');
        document.body.classList.remove('vdh-locked');
    }

    // Keep the header badge fresh (cheap counts call) without opening the modal.
    function refreshCount() {
        fetch('/api/video/downloads/history?limit=1', { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) { if (d && d.counts) updateBadge(d.counts.total || 0); })
            .catch(function () {});
    }

    document.addEventListener('click', function (e) {
        if (e.target.closest('[data-vdh-open]')) { e.preventDefault(); open(); }
    });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && state.open) close(); });
    // Refresh the badge when the downloads page is shown.
    document.addEventListener('soulsync:video-page-shown', function (e) {
        if (e.detail === 'video-downloads') refreshCount();
    });

    window.VideoDownloadHistory = { open: open, close: close, refreshCount: refreshCount };
})();
