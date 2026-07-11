/*
 * SoulSync — Video Library page (isolated).
 *
 * Reuses the music library's look + paging model: server-side search / A–Z /
 * sort / owned-wanted filter / pagination (75 per page) via /api/video/library,
 * rendering one page of music-style .library-artist-card cards (with a poster
 * proxy + resolution badge). Cards are NOT clickable yet. IIFE, no globals.
 */
(function () {
    'use strict';

    var PAGE_ID = 'video-library';
    var LIBRARY_URL = '/api/video/library';
    var LETTERS = 'abcdefghijklmnopqrstuvwxyz'.split('');

    var state = { tab: 'movies', search: '', letter: 'all', sort: 'title',
                  status: 'all', page: 1, limit: 75, loaded: false,
                  // Bulk select mode: id→true map (persists across pages), the
                  // open action popover, and the running-job poll timer.
                  selecting: false, selected: {}, bulkPop: null, bulkTimer: null };
    var searchTimer = null;

    function toast(msg, type) { if (typeof showToast === 'function') showToast(msg, type); }
    function cardKind() {
        return state.tab === 'movies' ? 'movie' : state.tab === 'shows' ? 'show' : 'channel';
    }
    function selCount() { return Object.keys(state.selected).length; }

    function $(sel) { return document.querySelector(sel); }

    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function resLabel(res) {
        if (!res) return '';
        res = String(res).toLowerCase();
        if (res.indexOf('2160') > -1 || res === '4k') return '4K';
        if (res.indexOf('1080') > -1) return '1080p';
        if (res.indexOf('720') > -1) return '720p';
        if (res.indexOf('480') > -1 || res.indexOf('576') > -1) return 'SD';
        return res.toUpperCase();
    }

    function buildAlphabet() {
        var host = $('[data-video-lib-alphabet]');
        if (!host || host.childNodes.length) return;
        var defs = [['all', 'All']].concat(
            LETTERS.map(function (l) { return [l, l.toUpperCase()]; }), [['#', '#']]);
        for (var i = 0; i < defs.length; i++) {
            var b = document.createElement('button');
            b.className = 'alphabet-btn' + (defs[i][0] === 'all' ? ' active' : '');
            b.setAttribute('data-letter', defs[i][0]);
            b.textContent = defs[i][1];
            (function (letter, btn) {
                btn.addEventListener('click', function () {
                    state.letter = letter;
                    var all = host.querySelectorAll('.alphabet-btn');
                    for (var j = 0; j < all.length; j++) all[j].classList.toggle('active', all[j] === btn);
                    reload();
                });
            })(defs[i][0], b);
            host.appendChild(b);
        }
    }

    function cardHTML(it, kind) {
        // Channels: avatar poster (proxied), downloaded/known counts, and the
        // same card chrome — clicking opens the in-app channel page.
        if (kind === 'channel') {
            var av = (it.poster_url && window.VideoYoutube) ? VideoYoutube.img(it.poster_url)
                : (it.poster_url || '');
            var cimg = av
                ? '<div class="library-artist-image"><img src="' + esc(av) +
                  '" alt="" loading="lazy" onerror="this.parentNode.innerHTML=\'<div class=&quot;library-artist-image-fallback&quot;>📺</div>\'"></div>'
                : '<div class="library-artist-image"><div class="library-artist-image-fallback">📺</div></div>';
            var cstats = (it.owned_count || 0) + ' downloaded' +
                (it.video_count ? ' · ' + it.video_count + ' known' : '');
            return '<a class="library-artist-card video-card--clickable" href="#" ' +
                'data-video-card-open="channel" data-video-card-id="' + esc(it.id) + '">' + cimg +
                '<div class="library-artist-info">' +
                '<h3 class="library-artist-name" title="' + esc(it.title) + '">' + esc(it.title) + '</h3>' +
                '<div class="library-artist-stats"><span class="library-artist-stat">' +
                esc(cstats) + '</span></div></div></a>';
        }
        var fallback = kind === 'movie' ? '🎬' : '📺';
        var img = it.has_poster
            ? '<div class="library-artist-image"><img src="/api/video/poster/' + kind + '/' + it.id +
              '" alt="" loading="lazy" onerror="this.parentNode.innerHTML=\'<div class=&quot;library-artist-image-fallback&quot;>' +
              fallback + '</div>\'"></div>'
            : '<div class="library-artist-image"><div class="library-artist-image-fallback">' + fallback + '</div></div>';

        // Overlay control group (eye/get), injected inside the positioned poster
        // box: airing show -> eye + get; ended show / movie -> get; (people elsewhere).
        var ctrl = window.VideoGet ? VideoGet.cardButton({
            kind: kind, tmdbId: it.tmdb_id, libraryId: it.id, title: it.title,
            poster: it.has_poster ? ('/api/video/poster/' + kind + '/' + it.id) : '',
            status: it.status, source: 'library'
        }) : '';
        if (ctrl) img = img.replace(/<\/div>$/, ctrl + '</div>');

        var badge = '';
        if (kind === 'movie') {
            var rl = resLabel(it.resolution);
            if (rl) badge = '<div class="video-card-badge">' + rl + '</div>';
        }

        var meta = [];
        if (it.year) meta.push(String(it.year));
        if (kind === 'movie') meta.push(it.has_file ? 'Owned' : 'Wanted');
        else meta.push((it.owned_count || 0) + '/' + (it.episode_count || 0) + ' eps');

        // A REAL link (like the music artist cards) so reload / new-tab / Back all
        // work; the click handler intercepts plain left-clicks into SPA nav.
        var href = '/video-detail/library/' + kind + '/' + it.id;
        var hook = ' href="' + href + '" data-video-card-open="' + kind + '" data-video-card-id="' + it.id + '"';
        return '<a class="library-artist-card video-card--clickable"' + hook + '>' + img + badge +
            '<div class="library-artist-info">' +
            '<h3 class="library-artist-name" title="' + esc(it.title) + '">' + esc(it.title) + '</h3>' +
            '<div class="library-artist-stats"><span class="library-artist-stat">' +
            esc(meta.join(' · ')) + '</span></div></div></a>';
    }

    function showLoading(on) {
        var l = $('[data-video-lib-loading]');
        if (l) l.classList.toggle('hidden', !on);
    }

    function renderItems(items, kind) {
        var grid = $('[data-video-lib-grid]');
        var empty = $('[data-video-lib-empty]');
        if (grid) grid.innerHTML = items.map(function (it) { return cardHTML(it, kind); }).join('');
        if (empty) empty.classList.toggle('hidden', items.length > 0);
        // #watchlist: paint the follow eye on shows already on the watchlist.
        if (grid && kind === 'show' && window.VideoWatchlist) VideoWatchlist.hydrate(grid);
        paintPicked();
    }

    // Selection survives paging/filtering: re-apply the picked state to whatever
    // cards are currently on screen.
    function paintPicked() {
        var grid = $('[data-video-lib-grid]');
        if (!grid) return;
        grid.classList.toggle('video-lib--selecting', state.selecting);
        var cards = grid.querySelectorAll('[data-video-card-id]');
        for (var i = 0; i < cards.length; i++) {
            cards[i].classList.toggle('video-card--picked',
                !!state.selected[cards[i].getAttribute('data-video-card-id')]);
        }
    }

    function updatePagination(p) {
        var box = $('[data-video-lib-pagination]');
        var prev = $('[data-video-lib-prev]');
        var next = $('[data-video-lib-next]');
        var info = $('[data-video-lib-pageinfo]');
        if (!box) return;
        if (!p) { box.classList.add('hidden'); return; }
        if (prev) prev.disabled = !p.has_prev;
        if (next) next.disabled = !p.has_next;
        if (info) info.textContent = 'Page ' + p.page + ' of ' + p.total_pages;
        box.classList.toggle('hidden', p.total_pages <= 1);
    }

    function setCount(n) {
        var c = $('[data-video-lib-count]');
        var lbl = $('[data-video-lib-count-label]');
        if (c) c.textContent = n;
        if (lbl) {
            lbl.textContent = state.tab === 'movies' ? 'Movies'
                : state.tab === 'shows' ? 'Shows' : 'Channels';
        }
    }

    function load() {
        state.loaded = true;
        showLoading(true);
        var apiKind = state.tab;                                     // query param (plural)
        var cardKind = apiKind === 'movies' ? 'movie' : apiKind === 'shows' ? 'show' : 'channel';
        var params = new URLSearchParams({
            kind: apiKind, search: state.search, letter: state.letter, sort: state.sort,
            status: state.status, page: state.page, limit: state.limit });
        fetch(LIBRARY_URL + '?' + params.toString(), { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                showLoading(false);
                if (!d || d.error) { renderItems([], cardKind); updatePagination(null); setCount(0); return; }
                renderItems(d.items || [], cardKind);
                updatePagination(d.pagination);
                setCount(d.pagination ? d.pagination.total_count : 0);
            })
            .catch(function () { showLoading(false); renderItems([], cardKind); updatePagination(null); setCount(0); });
    }

    function reload() { state.page = 1; load(); }

    // ── Bulk select mode + action bar ────────────────────────────────────────
    // Select N cards → a floating bar of bulk actions. Every action runs through
    // /api/video/bulk (the same edit-and-lock engine as the Manage sidebar):
    // rating/genre edits push to the server AND lock against scans, per item.
    var RATING_HINTS = { movie: ['G', 'PG', 'PG-13', 'R', 'NC-17', 'NR'],
                         show: ['TV-Y', 'TV-Y7', 'TV-G', 'TV-PG', 'TV-14', 'TV-MA'] };

    function setSelecting(on) {
        state.selecting = on;
        if (!on) { state.selected = {}; state.bulkPop = null; }
        var btn = $('[data-video-lib-select]');
        if (btn) { btn.textContent = on ? 'Done' : 'Select'; btn.classList.toggle('active', on); }
        paintPicked();
        paintBulkBar();
    }

    function ensureBulkBar() {
        var page = document.querySelector('#video-library-page .library-content');
        if (!page) return null;
        var bar = page.querySelector('[data-video-bulkbar]');
        if (!bar) {
            bar = document.createElement('div');
            bar.setAttribute('data-video-bulkbar', '');
            bar.className = 'video-bulkbar hidden';
            page.appendChild(bar);
            bar.addEventListener('click', onBulkBarClick);
            bar.addEventListener('keydown', function (e) {
                if (e.key === 'Enter' && e.target.closest('[data-vbb-input]')) {
                    e.preventDefault();
                    var apply = bar.querySelector('[data-vbb-apply]');
                    if (apply) apply.click();
                }
            });
        }
        return bar;
    }

    function bulkRunning() {
        var bar = ensureBulkBar();
        return !!(bar && bar.getAttribute('data-vbb-running') === '1');
    }

    function paintBulkBar() {
        var bar = ensureBulkBar();
        if (!bar) return;
        if (!state.selecting) { bar.classList.add('hidden'); bar.innerHTML = ''; return; }
        if (bulkRunning()) return;   // progress view owns the bar until the job ends
        var n = selCount();
        bar.classList.remove('hidden');
        var acts = [['rating', 'Rating'], ['genre', 'Genre'], ['state', 'State'],
                    ['collection', 'Collection'], ['art', 'Artwork']];
        var btns = acts.map(function (a) {
            return '<button class="vbb-act' + (state.bulkPop === a[0] ? ' vbb-act--on' : '') +
                '" type="button" data-vbb-act="' + a[0] + '"' + (n ? '' : ' disabled') + '>' +
                a[1] + '</button>';
        }).join('');
        bar.innerHTML =
            (state.bulkPop ? '<div class="vbb-pop" data-vbb-pop>' + popHTML(state.bulkPop) + '</div>' : '') +
            '<div class="vbb-row">' +
                '<span class="vbb-count">' + n + ' selected</span>' +
                '<button class="vbb-link" type="button" data-vbb-all>Page</button>' +
                '<button class="vbb-link" type="button" data-vbb-none' + (n ? '' : ' disabled') + '>None</button>' +
                '<span class="vbb-sep"></span>' + btns +
                '<span class="vbb-spacer"></span>' +
                '<button class="vbb-close" type="button" data-vbb-done title="Exit select mode">×</button>' +
            '</div>';
        if (state.bulkPop === 'genre') loadGenreHints();
        if (state.bulkPop === 'collection') loadCollections();
        var inp = bar.querySelector('[data-vbb-input]');
        if (inp) inp.focus();
    }

    function popHTML(which) {
        var kind = cardKind();
        if (which === 'rating') {
            var dl = '<datalist id="vbb-ratings">' + (RATING_HINTS[kind] || []).map(function (r) {
                return '<option value="' + r + '">';
            }).join('') + '</datalist>';
            return '<span class="vbb-pop-label">Set content rating</span>' +
                '<input class="vbb-input" data-vbb-input list="vbb-ratings" placeholder="PG-13…">' + dl +
                '<button class="vbb-apply" type="button" data-vbb-apply data-vbb-do="content_rating">Apply</button>';
        }
        if (which === 'genre') {
            return '<span class="vbb-pop-label">Genre</span>' +
                '<input class="vbb-input" data-vbb-input list="vbb-genres" placeholder="Comfort Films…">' +
                '<datalist id="vbb-genres"></datalist>' +
                '<button class="vbb-apply" type="button" data-vbb-apply data-vbb-do="genre_add">Add to all</button>' +
                '<button class="vbb-apply vbb-apply--ghost" type="button" data-vbb-do="genre_remove">Remove from all</button>';
        }
        if (which === 'state') {
            return '<span class="vbb-pop-label">Mark all as</span>' +
                '<button class="vbb-apply" type="button" data-vbb-do="watched" data-vbb-val="1">Watched</button>' +
                '<button class="vbb-apply vbb-apply--ghost" type="button" data-vbb-do="watched" data-vbb-val="0">Unwatched</button>' +
                '<span class="vbb-sep"></span>' +
                '<button class="vbb-apply" type="button" data-vbb-do="monitored" data-vbb-val="1">Monitored</button>' +
                '<button class="vbb-apply vbb-apply--ghost" type="button" data-vbb-do="monitored" data-vbb-val="0">Unmonitored</button>';
        }
        if (which === 'collection') {
            return '<span class="vbb-pop-label">Pin to collection</span>' +
                '<select class="vbb-input vbb-select" data-vbb-input data-vbb-collection>' +
                '<option value="">Loading…</option></select>' +
                '<button class="vbb-apply" type="button" data-vbb-apply data-vbb-do="collection_add">Add</button>';
        }
        if (which === 'art') {
            return '<span class="vbb-pop-label">Re-pull posters, backdrops &amp; credits from TMDB</span>' +
                '<button class="vbb-apply" type="button" data-vbb-do="refresh_art">Refresh artwork</button>';
        }
        return '';
    }

    function loadGenreHints() {
        fetch('/api/video/collections/fields?media_type=' + cardKind())
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                var dl = document.getElementById('vbb-genres');
                var names = (d && d.suggestions && d.suggestions.genre) || [];
                if (dl) dl.innerHTML = names.map(function (g) {
                    return '<option value="' + esc(g) + '">';
                }).join('');
            }).catch(function () { /* hints are a nicety */ });
    }

    function loadCollections() {
        fetch('/api/video/collections')
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                var sel = document.querySelector('[data-vbb-collection]');
                if (!sel) return;
                var kind = cardKind();
                var cols = ((d && d.collections) || []).filter(function (c) {
                    return (c.media_type || 'movie') === kind;
                });
                sel.innerHTML = cols.length
                    ? cols.map(function (c) {
                        return '<option value="' + c.id + '">' + esc(c.name) + '</option>';
                    }).join('')
                    : '<option value="">No ' + (kind === 'movie' ? 'movie' : 'show') + ' collections yet</option>';
            })
            .catch(function () { /* admin-gated for non-admin profiles */ });
    }

    function onBulkBarClick(e) {
        if (e.target.closest('[data-vbb-done]')) { setSelecting(false); return; }
        if (e.target.closest('[data-vbb-none]')) { state.selected = {}; paintPicked(); paintBulkBar(); return; }
        if (e.target.closest('[data-vbb-all]')) {
            var cards = document.querySelectorAll('[data-video-lib-grid] [data-video-card-id]');
            for (var i = 0; i < cards.length; i++) state.selected[cards[i].getAttribute('data-video-card-id')] = true;
            paintPicked(); paintBulkBar(); return;
        }
        var act = e.target.closest('[data-vbb-act]');
        if (act) {
            var which = act.getAttribute('data-vbb-act');
            state.bulkPop = (state.bulkPop === which) ? null : which;
            paintBulkBar(); return;
        }
        var run = e.target.closest('[data-vbb-do]');
        if (run) runBulk(run);
    }

    function runBulk(btn) {
        var action = btn.getAttribute('data-vbb-do');
        var ids = Object.keys(state.selected).map(function (i) { return parseInt(i, 10); });
        if (!ids.length) return;
        var params = {};
        if (action === 'content_rating') {
            var rv = document.querySelector('[data-video-bulkbar] [data-vbb-input]');
            params.value = rv ? rv.value.trim() : '';
            if (!params.value) { toast('Enter a rating first', 'warning'); return; }
        } else if (action === 'genre_add' || action === 'genre_remove') {
            var gv = document.querySelector('[data-video-bulkbar] [data-vbb-input]');
            params.genre = gv ? gv.value.trim() : '';
            if (!params.genre) { toast('Enter a genre first', 'warning'); return; }
        } else if (action === 'watched' || action === 'monitored') {
            params.value = btn.getAttribute('data-vbb-val') === '1';
        } else if (action === 'collection_add') {
            var cs = document.querySelector('[data-vbb-collection]');
            params.collection_id = cs && cs.value ? parseInt(cs.value, 10) : null;
            if (!params.collection_id) { toast('Pick a collection first', 'warning'); return; }
        }
        fetch('/api/video/bulk/start', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ kind: cardKind(), ids: ids, action: action, params: params }),
        }).then(function (r) { return r.json().then(function (b) { return { ok: r.ok, body: b }; }); })
        .then(function (res) {
            if (!res.ok || !res.body.ok) {
                toast((res.body && res.body.error) || 'Bulk action failed', 'error'); return;
            }
            if (action === 'collection_add') {
                var b = res.body;
                toast('Pinned ' + b.added + ' to ' + (b.name || 'collection') +
                    (b.skipped ? ' (' + b.skipped + ' already there)' : ''), 'success');
                state.bulkPop = null; paintBulkBar();
                return;
            }
            showBulkProgress(res.body.label || 'Working…', res.body.total || ids.length);
        })
        .catch(function () { toast('Bulk action failed', 'error'); });
    }

    function showBulkProgress(label, total) {
        var bar = ensureBulkBar();
        if (!bar) return;
        bar.setAttribute('data-vbb-running', '1');
        bar.classList.remove('hidden');
        bar.innerHTML =
            '<div class="vbb-row vbb-row--progress">' +
                '<span class="vbb-pop-label" data-vbb-plabel>' + esc(label) + '</span>' +
                '<div class="vbb-track"><div class="vbb-fill" data-vbb-fill style="width:0%"></div></div>' +
                '<span class="vbb-count" data-vbb-pct>0/' + total + '</span>' +
            '</div>';
        if (state.bulkTimer) clearInterval(state.bulkTimer);
        state.bulkTimer = setInterval(pollBulk, 1000);
    }

    function pollBulk() {
        fetch('/api/video/bulk/status')
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (j) {
                if (!j) return;
                var bar = ensureBulkBar();
                if (!bar || !bar.getAttribute('data-vbb-running')) return;
                var fill = bar.querySelector('[data-vbb-fill]');
                var pct = bar.querySelector('[data-vbb-pct]');
                if (fill && j.total) fill.style.width = Math.round(100 * j.done / j.total) + '%';
                if (pct) pct.textContent = j.done + '/' + j.total;
                if (!j.running) {
                    clearInterval(state.bulkTimer); state.bulkTimer = null;
                    bar.removeAttribute('data-vbb-running');
                    if (j.phase === 'error') toast(j.error || 'Bulk run failed', 'error');
                    else toast((j.label || 'Bulk edit') + ' — ' + j.ok + ' done' +
                        (j.failed ? ', ' + j.failed + ' failed' : ''), j.failed ? 'warning' : 'success');
                    state.bulkPop = null;
                    load();          // repaint the page (values changed); selection survives
                    paintBulkBar();
                }
            })
            .catch(function () { /* next tick */ });
    }

    function wire() {
        // Card click → drill into the detail page (event-delegated on the grid).
        var grid = $('[data-video-lib-grid]');
        if (grid) {
            grid.addEventListener('click', function (e) {
                // Let modified clicks (new tab / window) use the real href.
                if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
                var card = e.target.closest('[data-video-card-open]');
                if (!card || !grid.contains(card)) return;
                e.preventDefault();
                // Select mode: cards toggle membership instead of opening.
                if (state.selecting) {
                    var sid = card.getAttribute('data-video-card-id');
                    if (state.selected[sid]) delete state.selected[sid];
                    else state.selected[sid] = true;
                    card.classList.toggle('video-card--picked', !!state.selected[sid]);
                    paintBulkBar();
                    return;
                }
                var openKind = card.getAttribute('data-video-card-open');
                document.dispatchEvent(new CustomEvent('soulsync:video-open-detail', {
                    detail: openKind === 'channel'
                        ? { kind: 'channel', id: card.getAttribute('data-video-card-id'),
                            source: 'youtube' }
                        : { kind: openKind,
                            id: parseInt(card.getAttribute('data-video-card-id'), 10),
                            source: 'library' },
                }));
            });
        }
        var tabs = document.querySelectorAll('[data-video-lib-tab]');
        for (var i = 0; i < tabs.length; i++) {
            (function (tab) {
                tab.addEventListener('click', function () {
                    if (state.tab !== tab.getAttribute('data-video-lib-tab') && state.selecting) {
                        setSelecting(false);   // ids are per-kind — a tab switch clears the pick
                    }
                    state.tab = tab.getAttribute('data-video-lib-tab');
                    var all = document.querySelectorAll('[data-video-lib-tab]');
                    for (var j = 0; j < all.length; j++) all[j].classList.toggle('active', all[j] === tab);
                    var s = $('[data-video-lib-search]');
                    if (s) s.placeholder = 'Search ' + state.tab + '...';
                    // Channels have no bulk metadata ops or owned/wanted filter.
                    var isCh = state.tab === 'channels';
                    var selBtn = $('[data-video-lib-select]');
                    if (selBtn) selBtn.style.display = isCh ? 'none' : '';
                    var status = $('[data-video-lib-status]');
                    if (status) status.style.display = isCh ? 'none' : '';
                    reload();
                });
            })(tabs[i]);
        }
        var search = $('[data-video-lib-search]');
        if (search) {
            search.addEventListener('input', function () {
                state.search = search.value || '';
                if (searchTimer) clearTimeout(searchTimer);
                searchTimer = setTimeout(reload, 300);
            });
        }
        var sort = $('[data-video-lib-sort]');
        if (sort) sort.addEventListener('change', function () { state.sort = sort.value; reload(); });
        var status = $('[data-video-lib-status]');
        if (status) status.addEventListener('change', function () { state.status = status.value; reload(); });
        var prev = $('[data-video-lib-prev]');
        if (prev) prev.addEventListener('click', function () {
            if (state.page > 1) { state.page--; load(); }
        });
        var next = $('[data-video-lib-next]');
        if (next) next.addEventListener('click', function () { state.page++; load(); });
        var selBtn = $('[data-video-lib-select]');
        if (selBtn) selBtn.addEventListener('click', function () { setSelecting(!state.selecting); });
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && state.selecting && !bulkRunning() &&
                    document.body.getAttribute('data-video-page') === PAGE_ID) {
                setSelecting(false);
            }
        });
    }

    function onScanDone() { if (state.loaded) load(); }

    // Gate the library/scan when no video server (Plex/Jellyfin) is connected —
    // nothing breaks, the user is just told what to do.
    function ensureServerBanner() {
        var content = document.querySelector('#video-library-page .library-content');
        if (!content) return null;
        var b = content.querySelector('[data-video-noserver]');
        if (!b) {
            b = document.createElement('div');
            b.setAttribute('data-video-noserver', '');
            b.className = 'video-noserver hidden';
            b.innerHTML = 'No video server connected. Go to <strong>Settings &rarr; Video Source</strong> ' +
                'and connect Plex or Jellyfin to scan and browse your video library.';
            content.insertBefore(b, content.firstChild);
        }
        return b;
    }
    function checkServer() {
        fetch('/api/video/server', { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                var ok = !!(d && d.server), banner = ensureServerBanner();
                if (banner) banner.classList.toggle('hidden', ok);
                var scan = document.querySelector('[data-video-scan-mode]');
                if (scan) {
                    scan.disabled = !ok;
                    scan.style.opacity = ok ? '' : '0.45';
                    scan.title = ok ? 'Scan the media server' : 'Connect Plex or Jellyfin in Settings first';
                }
            })
            .catch(function () { /* ignore */ });
    }

    function onPageShown(e) {
        if (!e || e.detail !== PAGE_ID) return;
        checkServer();
        if (!state.loaded) load();
    }

    function init() {
        buildAlphabet();
        wire();
        document.addEventListener('soulsync:video-page-shown', onPageShown);
        document.addEventListener('soulsync:video-scan-done', onScanDone);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
