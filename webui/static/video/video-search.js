/*
 * SoulSync — Video Search page (isolated, in-app).
 *
 * Debounced multi-search via /api/video/search (movies / shows / people from
 * TMDB). Movie/show results link to the OWNED library detail when we already
 * have them (library_id), otherwise to the TMDB-backed detail. People open the
 * in-app person page. Everything stays inside SoulSync — no external links.
 *
 * Reuses the library card classes (.library-artist-card). Self-contained IIFE,
 * no globals, event-delegated, no inline handlers. Talks only to /api/video/*.
 */
(function () {
    'use strict';

    var PAGE_ID = 'video-search';
    var SEARCH_URL = '/api/video/search';
    var STUDIO_URL = '/api/video/search/studios';
    var FRESH_URL = '/api/video/downloads/fresh-releases';

    var lastQuery = '';
    var reqSeq = 0;            // guards against out-of-order responses
    var timer = null;
    var wired = false;
    var trendingCache = null;  // null = not fetched; [] = fetched/empty
    var lastChannel = null;    // resolved YouTube channel awaiting a Follow
    var lastPlaylist = null;   // resolved YouTube playlist awaiting Add-to-watchlist
    var mode = 'enhanced';
    var queryContext = null;

    function $(sel) { return document.querySelector(sel); }
    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }
    function show(sel, on) { var n = $(sel); if (n) n.classList.toggle('hidden', !on); }
    function renderQueryContext() {
        var el = $('[data-video-search-context]');
        if (!el) return;
        if (!queryContext || !queryContext.q) { el.hidden = true; el.innerHTML = ''; return; }
        var kind = queryContext.kind === 'show' ? 'Shows' : 'Movies';
        var label = queryContext.source === 'keyword' ? 'Keyword' : 'Search';
        el.hidden = false;
        el.innerHTML = '<span>' + esc(label) + '</span><strong>' + esc(queryContext.q) + '</strong>' +
            '<em>' + esc(kind) + '</em>' +
            '<button type="button" data-video-search-context-clear aria-label="Clear search context">&times;</button>';
    }
    function clearQueryContext() { queryContext = null; renderQueryContext(); }
    var BASIC_SEARCH_SOURCES = {
        soulseek: { label: 'slskd', kind: 'Soulseek', source: 'soulseek' },
        thepiratebay: { label: 'The Pirate Bay', kind: 'Prowlarr torrent indexer', source: 'torrent', indexer: 'thepiratebay' },
        extto: { label: 'EXT.to', kind: 'FlareSolverr torrent scraper', source: 'extto' },
        '1337x': { label: '1337x', kind: 'Prowlarr torrent indexer', source: 'torrent', indexer: '1337x' },
        usenet: { label: 'Usenet', kind: 'Prowlarr usenet indexers', source: 'usenet' }
    };
    var BASIC_TORRENT_SOURCES = ['thepiratebay', 'extto', '1337x'];
    var basicSeq = 0;
    var basicConfiguredSources = null;
    var basicConfigLoading = false;
    var basicActiveSource = null;
    var basicRowsBySource = {};   // source id -> the rows currently rendered for it
    var basicOpen = {};           // hit key -> expanded
    var basicDetail = {};         // ext.to detail url -> matched facts (or false = looked, nothing)
    var freshSeq = 0;
    var freshCache = null;
    var freshRefreshing = false;   // a manual Refresh is in flight
    var freshExpanded = {};        // detail url -> open, so a re-render keeps it open
    var freshLoading = false;
    var freshPeriod = 'day';
    var freshIdentify = null;
    var freshIdentifyTimer = null;
    var freshIdentifySeq = 0;

    function basicSources() {
        var nodes = document.querySelectorAll('[data-vsr-basic-source]:checked');
        var out = [];
        for (var i = 0; i < nodes.length; i++) if (BASIC_SEARCH_SOURCES[nodes[i].value]) out.push(nodes[i].value);
        return out;
    }

    function sourceLabel(s) {
        return BASIC_SEARCH_SOURCES[s] ? BASIC_SEARCH_SOURCES[s].label : s;
    }
    function basicIdsFromDownloadConfig(c) {
        c = c || {};
        var modes = (c.download_mode === 'hybrid' && Array.isArray(c.hybrid_order) && c.hybrid_order.length)
            ? c.hybrid_order : [c.download_mode || 'soulseek'];
        var out = [];
        modes.forEach(function (m) {
            if (m === 'soulseek') out.push('soulseek');
            else if (m === 'torrent') out = out.concat(BASIC_TORRENT_SOURCES);
            else if (m === 'usenet') out.push('usenet');
        });
        return out.filter(function (id, i) { return BASIC_SEARCH_SOURCES[id] && out.indexOf(id) === i; });
    }

    function renderBasicSourceControls(ids) {
        var host = $('[data-vsr-basic-sources]'); if (!host) return;
        if (ids === null) {
            host.innerHTML = '<span class="vsr-basic-source-note">Loading configured sources...</span>';
            return;
        }
        if (!ids.length) {
            host.innerHTML = '<span class="vsr-basic-source-note">No video download source is configured.</span>';
            return;
        }
        var checked = {};
        document.querySelectorAll('[data-vsr-basic-source]:checked').forEach(function (n) { checked[n.value] = true; });
        var hadChecks = Object.keys(checked).length > 0;
        host.innerHTML = ids.map(function (id) {
            var src = BASIC_SEARCH_SOURCES[id];
            var on = hadChecks ? !!checked[id] : true;
            return '<label><input type="checkbox" value="' + esc(id) + '" ' + (on ? 'checked ' : '') +
                'data-vsr-basic-source><span>' + esc(src.label) + '</span></label>';
        }).join('');
    }

    function ensureBasicSourceConfig() {
        if (basicConfiguredSources !== null || basicConfigLoading) return;
        basicConfigLoading = true;
        renderBasicSourceControls(null);
        fetch('/api/video/downloads/config', { headers: { 'Accept': 'application/json' } }).then(_json).then(function (c) {
            basicConfigLoading = false;
            basicConfiguredSources = basicIdsFromDownloadConfig(c);
            renderBasicSourceControls(basicConfiguredSources);
            if (mode === 'basic') { ensureBasicSourceConfig(); renderBasicPreview(); }
        }).catch(function () {
            basicConfigLoading = false;
            basicConfiguredSources = ['soulseek'];
            renderBasicSourceControls(basicConfiguredSources);
            if (mode === 'basic') { ensureBasicSourceConfig(); renderBasicPreview(); }
        });
    }

    function basicSourceRows(q, sources) {
        if (sources === null) {
            return '<div class="vsr-basic-empty">' +
                '<div class="vsr-basic-empty-mark">⌕</div>' +
                '<div><strong>Loading configured sources</strong>' +
                '<p>SoulSync is checking your Video Downloads configuration.</p></div>' +
            '</div>';
        }
        if (!q) {
            return '<div class="vsr-basic-empty">' +
                '<div class="vsr-basic-empty-mark">⌕</div>' +
                '<div><strong>Enter a query to search inside SoulSync</strong>' +
                '<p>The available sources follow your Video Downloads configuration.</p></div>' +
            '</div>';
        }
        if (!sources.length) {
            return '<div class="vsr-basic-empty">' +
                '<div class="vsr-basic-empty-mark">!</div>' +
                '<div><strong>No sources selected</strong>' +
                '<p>Choose at least one source so SoulSync can search it.</p></div>' +
            '</div>';
        }
        if (!basicActiveSource || sources.indexOf(basicActiveSource) === -1) basicActiveSource = sources[0];
        var tabs = sources.map(function (id) {
            var source = BASIC_SEARCH_SOURCES[id];
            var active = id === basicActiveSource;
            return '<button class="vsr-basic-source-tab ' + (active ? 'active' : '') + '" type="button" role="tab" ' +
                'aria-selected="' + (active ? 'true' : 'false') + '" data-vsr-basic-source-tab="' + esc(id) + '">' +
                '<span>' + esc(source.label) + '</span><em data-vsr-basic-tab-count>Queued</em><i class="vsr-basic-tab-loader" aria-hidden="true"></i></button>';
        }).join('');
        var panels = sources.map(function (id) {
            var source = BASIC_SEARCH_SOURCES[id];
            var active = id === basicActiveSource;
            return '<section class="vsr-basic-source-row ' + (active ? 'active' : '') + '" data-vsr-basic-card="' + esc(id) + '" ' +
                (active ? '' : 'hidden ') + 'role="tabpanel">' +
                '<div class="vsr-basic-source-top"><div class="vsr-basic-source-main"><strong>' + esc(source.label) + '</strong>' +
                '<em>' + esc(source.kind) + '</em></div>' +
                '<div class="vsr-basic-source-meta"><span class="vsr-basic-source-query">' + esc(q) + '</span>' +
                '<span class="vsr-basic-source-action" data-vsr-basic-state>Queued</span></div></div>' +
                '<div class="vsr-basic-source-counts" data-vsr-basic-counts aria-live="polite"></div>' +
                '<div class="vsr-basic-query-list" data-vsr-basic-queries></div>' +
                '<div class="vsr-basic-hits" data-vsr-basic-hits></div>' +
            '</section>';
        }).join('');
        return '<div class="vsr-basic-source-list"><div class="vsr-basic-source-tabs" role="tablist" aria-label="Basic search result sources">' +
            tabs + '</div>' + panels + '</div>';
    }

    function basicSearchBody(q, sourceId) {
        var source = BASIC_SEARCH_SOURCES[sourceId] || {};
        return {
            scope: 'movie', title: q, source: source.source || 'torrent', indexer: source.indexer || null,
            year: null, season: null, episode: null
        };
    }

    function basicSizeLabel(r) {
        var gb = Number(r && r.size_gb);
        if (isFinite(gb) && gb > 0) return gb.toFixed(gb >= 10 ? 0 : 1) + ' GB';
        var bytes = Number(r && (r.size_bytes || r.folder_size_bytes));
        if (!isFinite(bytes) || bytes <= 0) return 'Size unknown';
        var units = ['B', 'KB', 'MB', 'GB', 'TB'];
        var i = 0;
        while (bytes >= 1024 && i < units.length - 1) { bytes = bytes / 1024; i++; }
        return (bytes >= 10 || i < 2 ? Math.round(bytes) : bytes.toFixed(1)) + ' ' + units[i];
    }

    function basicHealthLabel(r) {
        var seeds = Number(r && r.seeders);
        var peers = Number(r && r.peers);
        var parts = [];
        if (isFinite(seeds) && seeds > 0) parts.push(seeds + ' seed' + (seeds === 1 ? '' : 's'));
        if (isFinite(peers) && peers > 0) parts.push(peers + ' peer' + (peers === 1 ? '' : 's'));
        if (parts.length) return parts.join(' / ');
        var avail = Number(r && r.availability);
        if (isFinite(avail) && avail > 0) return 'Availability ' + avail;
        return 'Health unknown';
    }

    // Whether a release can actually be fetched, by DOWNLOAD SOURCE. Soulseek needs
    // the peer + file; most sources need a magnet/.torrent/NZB link; EXT.to lists
    // releases without magnets (each costs its own Cloudflare-challenged detail
    // fetch) so its detail page IS the link — the server resolves the one you pick
    // at grab time.
    //
    // ONE rule, because this is asked in two places — on the result card, to decide
    // whether to offer Identify, and in the modal, to enable Start download. When
    // they were two functions they disagreed: EXT.to hits got an Identify button
    // that opened a modal whose Start download could never enable.
    function canFetchRelease(src, o) {
        if (!o) return false;
        if (src === 'soulseek') return !!(o.username && o.filename);
        if (src === 'extto') return !!(o.download_url || o.magnet_uri || o.info_url);
        return !!(o.download_url || o.magnet_uri);
    }

    function basicHitGrabbable(r, sourceId) {
        return canFetchRelease((BASIC_SEARCH_SOURCES[sourceId] || {}).source || 'torrent', r);
    }

    function basicHitKey(r, sourceId) {
        return sourceId + '|' + (r.info_url || r.guid || r.download_url || r.filename || r.title || '');
    }

    function basicSourceCounts(rows, sourceId) {
        var counts = { usable: 0, review: 0, noLink: 0 };
        (rows || []).forEach(function (r) {
            r = r || {};
            if (r.accepted === false || r.rejected) counts.review += 1;
            if (!basicHitGrabbable(r, sourceId)) counts.noLink += 1;
            if (r.accepted !== false && basicHitGrabbable(r, sourceId)) counts.usable += 1;
        });
        return counts;
    }

    function basicSourceCountHTML(rows, sourceId, done) {
        if (!done) return '';
        var c = basicSourceCounts(rows, sourceId);
        return '<span class="vsr-basic-count vsr-basic-count--usable">' + c.usable + ' usable</span>' +
            '<span class="vsr-basic-count vsr-basic-count--review">' + c.review + ' review</span>' +
            '<span class="vsr-basic-count vsr-basic-count--nolink">' + c.noLink + ' no link</span>';
    }

    function basicSourceCountText(rows, sourceId, done) {
        if (!done) return 'Searching';
        var c = basicSourceCounts(rows, sourceId);
        if (!(rows || []).length) return 'No matches';
        return c.usable + ' usable / ' + c.review + ' review / ' + c.noLink + ' no link';
    }

    function basicQueryHTML(queries) {
        queries = (queries || []).filter(Boolean);
        if (!queries.length) return '';
        return '<span class="vsr-basic-query-label">Queries</span>' + queries.slice(0, 6).map(function (q) {
            return '<span class="vsr-basic-query-chip">' + esc(q) + '</span>';
        }).join('') + (queries.length > 6 ? '<span class="vsr-basic-query-more">+' + (queries.length - 6) + '</span>' : '');
    }

    // Age from an indexer's publish date. Indexers state an ISO timestamp; the
    // board states 'x hours ago' already, so only the former needs converting.
    function basicAgeLabel(r) {
        if (r.age) return r.age;
        if (!r.published_at) return '';
        var t = Date.parse(r.published_at);
        if (!isFinite(t)) return '';
        var mins = Math.max(0, (Date.now() - t) / 60000);
        if (mins < 90) return Math.round(mins) + ' min ago';
        var hrs = mins / 60;
        if (hrs < 36) return Math.round(hrs) + ' hours ago';
        var days = hrs / 24;
        if (days < 45) return Math.round(days) + ' days ago';
        var mons = days / 30.4;
        return mons < 18 ? Math.round(mons) + ' months ago' : Math.round(days / 365) + ' years ago';
    }

    // The extras a hit carries, which differ by source rather than being a poorer
    // version of the same thing: an indexer knows age and grab count, Soulseek
    // knows how many peers hold the file and how contended they are, EXT.to knows
    // what the film IS. Show whichever the hit actually has.
    function basicExtrasHTML(r, d) {
        var bits = [];
        if (d && d.imdb_rating != null) bits.push('<b class="vsr-fresh-rating">&#9733; ' + esc(d.imdb_rating) +
            (d.imdb_votes ? ' <i>(' + esc(freshNum(d.imdb_votes)) + ')</i>' : '') + '</b>');
        if (d && d.title) bits.push('<b>' + esc(d.title) + '</b>');
        if (d && d.year) bits.push('<span>' + esc(d.year) + '</span>');
        if (d && d.runtime_minutes) bits.push('<span>' + esc(d.runtime_minutes) + ' min</span>');
        if (d && d.genres && d.genres.length) bits.push('<span>' + esc(d.genres.slice(0, 3).join(' \u00b7 ')) + '</span>');
        var age = basicAgeLabel(r);
        if (age) bits.push('<span>' + esc(age) + '</span>');
        if (r.grabs) bits.push('<span>' + esc(freshNum(r.grabs)) + ' grabs</span>');
        if (r.peer_count) bits.push('<span>' + esc(r.peer_count) + ' peer' + (r.peer_count === 1 ? '' : 's') + '</span>');
        if (r.queue) bits.push('<span>queue ' + esc(r.queue) + '</span>');
        if (!bits.length) return '';
        return '<div class="vsr-fresh-meta">' + bits.join('') + '</div>';
    }

    // The expanded panel. EXT.to fills it from its detail page; every other source
    // fills it from what the search itself returned, so the card opens for all of
    // them rather than only the one with a scraper.
    function basicFactsHTML(r, sourceId, d) {
        var rows = [];
        function add(label, value) { if (value != null && value !== '') rows.push({ label: label, value: value }); }
        if (d && (d.facts || []).length) {
            rows = (d.facts || []).slice();
            if (d.imdb_id) add('IMDb', d.imdb_id);
        } else {
            add('Release', r.title);
            add('Source', r.username || sourceId);
            add('Protocol', (r.protocol || '').toUpperCase());
            add('Size', basicSizeLabel(r));
            add('Age', basicAgeLabel(r));
            add('Swarm', basicHealthLabel(r));
            add('Grabs', r.grabs ? freshNum(r.grabs) : '');
            add('Peers holding it', r.peer_count || '');
            add('Free slots', r.slots || '');
            add('Queue', r.queue || '');
            add('Quality', r.quality_label || r.resolution);
            add('Codec', r.codec);
            add('Audio', r.audio);
            add('HDR', r.hdr);
            add('Group', r.group);
            add('Files', r.file_count || '');
            if (r.rejected) add('Why review', r.rejected);
        }
        if (!rows.length) return '';
        return '<div class="vsr-fresh-facts">' + rows.map(function (f) {
            return '<div class="vsr-fresh-fact"><span>' + esc(f.label) + '</span><em>' + esc(f.value) + '</em></div>';
        }).join('') + '</div>';
    }

    function basicResultHTML(r, sourceId, index) {
        r = r || {};
        var cfg = BASIC_SEARCH_SOURCES[sourceId] || {};
        var bits = [r.quality_label || r.resolution, r.source, r.codec, r.audio, r.hdr, r.group].filter(Boolean);
        var provider = r.username || (r.indexer_id ? String(r.indexer_id).toUpperCase() : (cfg.label || 'Source'));
        var transport = (cfg.source || r.source || sourceId || 'search').toString();
        var protocol = (r.protocol || transport).toString().toUpperCase() || 'SEARCH';
        var locator = r.download_url || r.magnet_uri ? 'Ready link' : (r.info_url ? 'Detail page' : 'Result only');
        var accepted = r.accepted === false ? 'Review' : 'Candidate';
        var analysis = r.rejected ? '<details class="vsr-basic-hit-analysis"><summary>Why review?</summary><p>' + esc(r.rejected) + '</p></details>' : '';
        var visibleNote = r.rejected ? '<p class="vsr-basic-hit-note"><span>Review</span>' + esc(r.rejected) + '</p>' : '';
        var origin = r.indexer_id || sourceId;
        var sourceLine = [
            '<span class="vsr-basic-source-chip">' + esc(provider) + '</span>',
            '<span>' + esc(protocol) + '</span>',
            '<span>' + esc(transport === 'extto' ? 'torrent via EXT.to' : transport) + '</span>',
            origin ? '<span>' + esc(origin) + '</span>' : ''
        ].filter(Boolean).join('');
        var chipHtml = bits.length ? bits.slice(0, 7).map(function (b) { return '<span>' + esc(b) + '</span>'; }).join('') : '<span>Release</span>';
        var grabbable = basicHitGrabbable(r, sourceId);
        var grabLabel = grabbable && r.accepted === false ? 'Try anyway' : (grabbable ? 'Identify' : 'No link');
        var key = basicHitKey(r, sourceId);
        var d = r.info_url ? basicDetail[r.info_url] : null;
        if (d === false) d = null;                       // looked, nothing came back
        var open = !!basicOpen[key];
        return '<article class="vsr-basic-hit ' + (r.accepted === false ? 'vsr-basic-hit--review' : '') +
                (d ? ' vsr-basic-hit--rich' : '') +
                // the extra grid column belongs to the IMAGE, not to having facts:
                // a matched release with no artwork must keep the 2-column layout
                (d && d.poster_url ? ' vsr-basic-hit--art' : '') +
                (open ? ' vsr-basic-hit--open' : '') +
                '" data-vsr-basic-toggle="' + esc(sourceId) + ':' + index + '" role="button" tabindex="0"' +
                ' aria-expanded="' + (open ? 'true' : 'false') + '">' +
            (d && d.poster_url
                ? '<img class="vsr-basic-art" src="/api/video/img?u=' + esc(encodeURIComponent(d.poster_url)) +
                  '" alt="" loading="lazy" decoding="async">'
                : '') +
            '<div class="vsr-basic-hit-main">' +
                '<div class="vsr-basic-hit-kicker">' + sourceLine + '</div>' +
                '<strong title="' + esc(r.title || '') + '">' + esc(r.title || 'Untitled release') + '</strong>' +
                '<div class="vsr-basic-hit-tags">' + chipHtml + '</div>' +
                visibleNote +
                basicExtrasHTML(r, d) +
                analysis +
            '</div>' +
            '<div class="vsr-basic-hit-side">' +
                '<span class="vsr-basic-hit-size">' + esc(basicSizeLabel(r)) + '</span>' +
                '<span class="vsr-basic-hit-health">' + esc(basicHealthLabel(r)) + '</span>' +
                '<span class="vsr-basic-hit-linkstate">' + esc(locator) + '</span>' +
                '<span class="vsr-basic-hit-verdict">' + esc(accepted) + '</span>' +
                '<button class="vsr-basic-hit-grab" type="button" data-vsr-basic-grab="' + esc(sourceId) + ':' + index + '"' +
                    (grabbable ? '' : ' disabled') + '>' + esc(grabLabel) + '</button>' +
            '</div>' +
            '<span class="vsr-fresh-chev" aria-hidden="true"></span>' +
            (open ? basicFactsHTML(r, sourceId, d) : '') +
        '</article>';
    }

    // Rewrite ONE source's hit list in place. renderBasicPreview rebuilds the whole
    // panel from the form, which would throw the results away and leave empty cards.
    function basicRerenderHits(sourceId) {
        var card = document.querySelector('[data-vsr-basic-card="' + sourceId + '"]');
        var host = card && card.querySelector('[data-vsr-basic-hits]');
        if (!host) return;
        var rows = basicRowsBySource[sourceId] || [];
        host.innerHTML = rows.map(function (r, i) { return basicResultHTML(r, sourceId, i); }).join('');
    }

    function basicToggleHit(sourceId, index) {
        var rows = basicRowsBySource[sourceId] || [];
        var r = rows[index];
        if (!r) return;
        var key = basicHitKey(r, sourceId);
        if (basicOpen[key]) { delete basicOpen[key]; basicRerenderHits(sourceId); return; }
        basicOpen[key] = true;
        basicRerenderHits(sourceId);
        // EXT.to can say much more than the search returned - but only by fetching
        // its detail page, which is a Cloudflare challenge. So it happens HERE, on
        // the card you actually opened, not while drawing a list of 25. The hourly
        // board refresh has usually matched it already, in which case this is a
        // cache read and returns instantly.
        if (!r.info_url || basicDetail[r.info_url] !== undefined) return;
        var token = ++basicSeq;
        fetch('/api/video/downloads/detail?fetch=1&url=' + encodeURIComponent(r.info_url),
              { headers: { 'Accept': 'application/json' } })
            .then(_json).then(function (res) {
                if (token !== basicSeq) return;
                basicDetail[r.info_url] = (res && res.success && res.detail) ? res.detail : false;
                basicRerenderHits(sourceId);
            }).catch(function () {
                if (token !== basicSeq) return;
                basicDetail[r.info_url] = false;   // don't retry on every re-render
                basicRerenderHits(sourceId);
            });
    }

    function setBasicSourceTab(id) {
        if (!BASIC_SEARCH_SOURCES[id]) return;
        basicActiveSource = id;
        document.querySelectorAll('[data-vsr-basic-source-tab]').forEach(function (tab) {
            var on = tab.getAttribute('data-vsr-basic-source-tab') === id;
            tab.classList.toggle('active', on);
            tab.setAttribute('aria-selected', on ? 'true' : 'false');
        });
        document.querySelectorAll('[data-vsr-basic-card]').forEach(function (card) {
            var on = card.getAttribute('data-vsr-basic-card') === id;
            card.classList.toggle('active', on);
            card.hidden = !on;
        });
    }

    function renderBasicHits(card, rows, done, error, totalFiles, queries) {
        var state = card.querySelector('[data-vsr-basic-state]');
        var hits = card.querySelector('[data-vsr-basic-hits]');
        var sourceId = card.getAttribute('data-vsr-basic-card');
        var countsHost = card.querySelector('[data-vsr-basic-counts]');
        var queryHost = card.querySelector('[data-vsr-basic-queries]');
        if (!hits) return;
        if (error) {
            if (state) state.textContent = 'Needs setup';
            if (countsHost) countsHost.innerHTML = '';
            if (queryHost) queryHost.innerHTML = basicQueryHTML(queries);
            var errTab = document.querySelector('[data-vsr-basic-source-tab="' + sourceId + '"] [data-vsr-basic-tab-count]');
            if (errTab) errTab.textContent = 'Needs setup';
            hits.innerHTML = '<div class="vsr-basic-source-note">' + esc(error) + '</div>';
            return;
        }
        rows = rows || [];
        var label = done ? (rows.length ? rows.length + ' found' : 'No matches') : 'Searching';
        var countText = basicSourceCountText(rows, sourceId, done);
        card.classList.toggle('is-searching', !done);
        if (state) state.innerHTML = done ? esc(label) : '<span class="vsr-basic-loader-dot" aria-hidden="true"></span>' + esc(label);
        if (countsHost) countsHost.innerHTML = basicSourceCountHTML(rows, sourceId, done);
        if (queryHost) queryHost.innerHTML = basicQueryHTML(queries);
        var tabBtn = document.querySelector('[data-vsr-basic-source-tab="' + sourceId + '"]');
        var tab = tabBtn && tabBtn.querySelector('[data-vsr-basic-tab-count]');
        if (tabBtn) tabBtn.classList.toggle('is-searching', !done);
        if (tab) tab.textContent = countText;
        if (!rows.length) {
            hits.innerHTML = '<div class="vsr-basic-source-note ' + (!done ? 'vsr-basic-source-note--loading' : '') + '">' + (done
                ? (totalFiles ? 'Files were found, but none matched as video releases.' : 'No matching releases found.')
                : '<span class="vsr-basic-loader" aria-hidden="true"><i></i><i></i><i></i></span><span>Searching this source...</span>') + '</div>';
            return;
        }
        var shown = rows.slice(0, 12);
        basicRowsBySource[sourceId] = shown;   // what the Identify buttons index into
        hits.innerHTML = shown.map(function (r, i) { return basicResultHTML(r, sourceId, i); }).join('') +
            (rows.length > 12 ? '<div class="vsr-basic-source-note">+' + (rows.length - 12) + ' more results in this source</div>' : '');
    }

    function runBasicSearch(q, sources) {
        var token = ++basicSeq;
        sources.forEach(function (id) {
            var card = document.querySelector('[data-vsr-basic-card="' + id + '"]');
            if (!card) return;
            var body = basicSearchBody(q, id);
            renderBasicHits(card, [], false);
            fetch('/api/video/downloads/search/start', { method: 'POST', headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
                body: JSON.stringify(body) }).then(_json).then(function (d) {
                if (token !== basicSeq || !card.isConnected) return;
                if (d && d.error) { renderBasicHits(card, [], true, d.error, 0, d.queries || []); return; }
                if (!d || !d.id) { renderBasicHits(card, d ? d.results : [], true, null, 0, d ? d.queries : []); return; }
                renderBasicHits(card, [], false, null, 0, d.queries || []);
                pollBasicSearch(token, card, body, d.id, d.poll_ms, d.queries || []);
            }).catch(function () {
                if (token === basicSeq && card.isConnected) renderBasicHits(card, [], true, 'Search failed.');
            });
        });
    }

    function pollBasicSearch(token, card, body, id, pollMs, queries) {
        var started = Date.now(), lastN = -1, stable = 0, total = 0;
        var maxMs = Math.min(80000, pollMs || 60000);
        function tick() {
            if (token !== basicSeq || !card.isConnected) return;
            var qs = '?id=' + encodeURIComponent(id) + '&scope=' + encodeURIComponent(body.scope || 'movie') + '&title=' + encodeURIComponent(body.title || '');
            fetch('/api/video/downloads/search/poll' + qs, { headers: { 'Accept': 'application/json' } }).then(_json).then(function (d) {
                if (token !== basicSeq || !card.isConnected) return;
                var rows = (d && d.results) || [];
                total = (d && d.total_files) || total;
                if (rows.length === lastN) stable++; else { stable = 0; lastN = rows.length; }
                var elapsed = Date.now() - started;
                var done = elapsed >= maxMs || rows.length >= 25 || (rows.length > 0 && elapsed > 20000 && stable >= 6);
                renderBasicHits(card, rows, done, null, total, (d && d.queries) || queries || []);
                if (!done) setTimeout(tick, 1500);
            }).catch(function () {
                if (token === basicSeq && card.isConnected) renderBasicHits(card, [], true, 'Search polling failed.', 0, queries || []);
            });
        }
        tick();
    }

    function renderBasicPreview() {
        var host = $('[data-video-search-results]'); if (!host) return;
        var q = (($('[data-vsr-basic-query]') || {}).value || '').trim();
        var cat = (($('[data-vsr-basic-category]') || {}).value || 'all');
        var sort = (($('[data-vsr-basic-sort]') || {}).value || 'seeders');
        ensureBasicSourceConfig();
        var sources = basicConfiguredSources === null ? [] : basicSources();
        var rowSources = basicConfiguredSources === null ? null : sources;
        show('[data-video-search-loading]', false);
        show('[data-video-search-hint]', false);
        show('[data-video-search-empty]', false);
        host.innerHTML = '<section class="vsr-basic-results">' +
            '<div class="vsr-basic-results-head"><div><span>Basic Search</span>' +
            '<h2>' + esc(q || 'Ready when you are') + '</h2></div>' +
            '<button class="vsr-basic-ghost" type="button" data-vsr-basic-focus>Refine</button></div>' +
            '<div class="vsr-basic-summary">' +
                '<span>' + esc(cat === 'all' ? 'All video' : cat) + '</span>' +
                '<span>Sort: ' + esc(sort) + '</span>' +
                '<span>' + (basicConfiguredSources === null ? 'Loading sources' : (sources.length ? sources.map(sourceLabel).map(esc).join(' + ') : 'No sources selected')) + '</span>' +
            '</div>' +
            basicSourceRows(q, rowSources) +
        '</section>';
        if (q && sources.length) runBasicSearch(q, sources);
    }

    function freshPeriodLabel(p) {
        return p === 'month' ? 'Month' : (p === 'week' ? 'Week' : 'Day');
    }

    function freshNum(n) {
        n = Number(n);
        return isFinite(n) ? n.toLocaleString() : '0';
    }

    function freshRows(category) {
        var sections = (freshCache && freshCache.sections) || {};
        var section = sections[category] || {};
        return section[freshPeriod] || [];
    }

    function freshStat(label, value, cls) {
        return '<span class="vsr-fresh-stat ' + (cls || '') + '"><em>' + esc(label) + '</em><strong>' + esc(value) + '</strong></span>';
    }

    function freshLoadingHTML() {
        var rows = '';
        for (var i = 0; i < 8; i++) rows += '<div class="vsr-fresh-row vsr-fresh-row--skel"><span></span><span></span><span></span><span></span></div>';
        return '<section class="vsr-fresh-board is-loading"><div class="vsr-fresh-board-head"><div><span>Fresh Releases</span><h2>Sourced from EXT.to</h2></div>' +
            '<div class="vsr-fresh-loader" aria-hidden="true"><i></i><i></i><i></i></div></div>' +
            '<div class="vsr-fresh-table">' + rows + '</div></section>';
    }

    // The facts EXT.to already stated on the release's own detail page, matched in
    // by the board refresh. Purely presentational — the user still identifies the
    // title in the modal; this is here so that call is an informed one.
    function freshMetaHTML(d) {
        if (!d) return '';
        var bits = [];
        if (d.imdb_rating != null) bits.push('<b class="vsr-fresh-rating">&#9733; ' + esc(d.imdb_rating) +
            (d.imdb_votes ? ' <i>(' + esc(freshNum(d.imdb_votes)) + ')</i>' : '') + '</b>');
        if (d.year) bits.push('<span>' + esc(d.year) + '</span>');
        if (d.runtime_minutes) bits.push('<span>' + esc(d.runtime_minutes) + ' min</span>');
        if (d.quality) bits.push('<span>' + esc(d.quality) + '</span>');
        if (d.genres && d.genres.length) bits.push('<span>' + esc(d.genres.slice(0, 3).join(' \u00b7 ')) + '</span>');
        if (!bits.length) return '';
        return '<div class="vsr-fresh-meta">' + bits.join('') + '</div>';
    }

    function freshArtHTML(d, title) {
        // EXT.to serves a grey 'no artwork' placeholder for a lot of TV; the parser
        // reports that as no poster, so we draw our own initial instead of a broken tile.
        if (d && d.poster_url) {
            // Through OUR origin, not ext.to directly: ext.to sends a
            // Cross-Origin-Resource-Policy header on its posters, so a direct <img>
            // is refused by the browser as ERR_BLOCKED_BY_RESPONSE.NotSameOrigin
            // even though the URL is right. /api/video/img also disk-caches it.
            var src = '/api/video/img?u=' + encodeURIComponent(d.poster_url);
            return '<img class="vsr-fresh-art" src="' + esc(src) + '" alt="" loading="lazy" decoding="async">';
        }
        return '<span class="vsr-fresh-art vsr-fresh-art--none" aria-hidden="true">' +
            esc((String(title || '?').trim()[0] || '?').toUpperCase()) + '</span>';
    }

    // Everything EXT.to stated about the title, in the order it stated it. Rendering
    // the LIST rather than named fields is deliberate: movie and TV pages carry
    // almost disjoint labels, and a category we have never seen still renders.
    function freshFactsHTML(d) {
        var facts = (d && d.facts) || [];
        if (!facts.length) return '';
        return '<div class="vsr-fresh-facts">' + facts.map(function (f) {
            return '<div class="vsr-fresh-fact"><span>' + esc(f.label) + '</span><em>' + esc(f.value) + '</em></div>';
        }).join('') +
        (d.imdb_id ? '<div class="vsr-fresh-fact"><span>IMDb</span><em>' + esc(d.imdb_id) + '</em></div>' : '') +
        '</div>';
    }

    function freshRowHTML(r, category, index) {
        r = r || {};
        var files = r.files == null ? '-' : freshNum(r.files);
        var seeds = r.seeders == null ? '-' : freshNum(r.seeders);
        var leech = r.leechers == null ? '-' : freshNum(r.leechers);
        var ready = !!(r.download_url || r.magnet_uri);
        var hint = category === 'movies' ? 'Movie' : (r.episode != null ? 'Episode' : 'Season pack');
        var d = r.detail || null;
        var named = d && d.title ? '<b>' + esc(d.title) + '</b> - ' : '';
        // Only a matched release has anything to expand into.
        var can = !!(d && (d.facts || []).length);
        var open = can && !!freshExpanded[r.url];
        return '<article class="vsr-fresh-row ' + (ready ? 'vsr-fresh-row--ready' : 'vsr-fresh-row--blocked') +
                (d ? ' vsr-fresh-row--rich' : '') + (can ? ' vsr-fresh-row--can-open' : '') +
                (open ? ' vsr-fresh-row--open' : '') + '"' +
                (can ? ' data-vsr-fresh-toggle="' + esc(category) + ':' + index + '" role="button" tabindex="0"' +
                       ' aria-expanded="' + (open ? 'true' : 'false') + '"' : '') + '>' +
            freshArtHTML(d, (d && d.title) || r.search_title || r.title) +
            '<div class="vsr-fresh-release"><strong title="' + esc(r.title || '') + '">' + esc(r.title || 'Untitled release') + '</strong>' +
                '<span>' + named + esc(r.age || 'Age unknown') + ' - ' + esc(r.source || 'EXT.to') + ' - ' + esc(hint) + '</span>' +
                freshMetaHTML(d) + '</div>' +
            freshStat('Size', r.size_text || 'Unknown') +
            freshStat('Files', files) +
            freshStat('Seed', seeds, 'vsr-fresh-seed') +
            freshStat('Leech', leech, 'vsr-fresh-leech') +
            '<button class="vsr-fresh-pick" type="button" data-vsr-fresh-pick="' + esc(category) + ':' + index + '" ' + (ready ? '' : 'disabled ') + '>' + (ready ? 'Identify' : 'No magnet') + '</button>' +
            (can ? '<span class="vsr-fresh-chev" aria-hidden="true"></span>' : '') +
            (open ? freshFactsHTML(d) : '') +
        '</article>';
    }

    function freshSectionHTML(category, label) {
        var rows = freshRows(category);
        var totalSeeds = rows.reduce(function (sum, r) { var n = Number(r.seeders); return sum + (isFinite(n) ? n : 0); }, 0);
        var body = rows.length ? rows.slice(0, 18).map(function (r, i) { return freshRowHTML(r, category, i); }).join('') :
            '<div class="vsr-basic-empty"><div class="vsr-basic-empty-mark">⌕</div><div><strong>No ' + esc(label.toLowerCase()) + ' releases found</strong><p>EXT.to did not publish rows for this period in the current homepage snapshot.</p></div></div>';
        return '<section class="vsr-fresh-board" data-vsr-fresh-section="' + esc(category) + '">' +
            '<div class="vsr-fresh-board-head"><div><span>' + esc(label) + '</span><h2>' + esc(freshPeriodLabel(freshPeriod)) + ' releases</h2></div>' +
            '<div class="vsr-fresh-board-stats">' + freshStat('Rows', freshNum(rows.length)) + freshStat('Seeds', freshNum(totalSeeds), 'vsr-fresh-seed') + '</div></div>' +
            '<div class="vsr-fresh-table-head"><span>Release</span><span>Size</span><span>Files</span><span>Seed</span><span>Leech</span><span></span></div>' +
            '<div class="vsr-fresh-table">' + body + '</div>' +
        '</section>';
    }

    function renderFreshReleases() {
        var host = $('[data-video-search-results]'); if (!host) return;
        show('[data-video-search-loading]', false);
        show('[data-video-search-hint]', false);
        show('[data-video-search-empty]', false);
        if (!freshCache && !freshLoading) loadFreshReleases();
        var tabs = ['day', 'week', 'month'].map(function (p) {
            var on = p === freshPeriod;
            return '<button class="vsr-fresh-period ' + (on ? 'active' : '') + '" type="button" data-vsr-fresh-period="' + p + '" aria-pressed="' + (on ? 'true' : 'false') + '">' + freshPeriodLabel(p) + '</button>';
        }).join('');
        if (freshLoading && !freshCache) {
            host.innerHTML = '<section class="vsr-fresh-results"><div class="vsr-fresh-head"><div><span>Fresh Releases</span><h2>Loading the latest board</h2><p>Sourced from EXT.to</p></div><div class="vsr-fresh-actions"><div class="vsr-fresh-periods">' + tabs + '</div>' + freshRefreshHTML() + '</div></div>' + freshLoadingHTML() + '</section>';
            return;
        }
        if (freshCache && freshCache.error) {
            host.innerHTML = '<section class="vsr-fresh-results"><div class="vsr-fresh-head"><div><span>Fresh Releases</span><h2>Sourced from EXT.to</h2><p>Fresh Releases needs the EXT.to homepage through FlareSolverr.</p></div><div class="vsr-fresh-actions"><div class="vsr-fresh-periods">' + tabs + '</div>' + freshRefreshHTML() + '</div></div>' +
                '<div class="vsr-basic-empty"><div class="vsr-basic-empty-mark">!</div><div><strong>Could not load Fresh Releases</strong><p>' + esc(freshCache.error) + '</p></div></div></section>';
            return;
        }
        host.innerHTML = '<section class="vsr-fresh-results"><div class="vsr-fresh-head"><div><span>Fresh Releases</span><h2>Sourced from EXT.to</h2><p>' +
                freshStampHTML() + '</p></div><div class="vsr-fresh-actions"><div class="vsr-fresh-periods">' + tabs + '</div>' + freshRefreshHTML() + '</div></div>' +
            '<div class="vsr-fresh-grid">' + freshSectionHTML('movies', 'Movies') + freshSectionHTML('tv', 'TV Series') + '</div></section>';
    }

    function freshToggleRow(category, index) {
        var row = freshRows(category)[index];
        if (!row || !row.url) return;
        if (freshExpanded[row.url]) delete freshExpanded[row.url];
        else freshExpanded[row.url] = true;
        renderFreshReleases();
    }

    function freshRefreshHTML() {
        return '<button class="vsr-fresh-refresh" type="button" data-vsr-fresh-refresh' +
            (freshRefreshing ? ' disabled' : '') + '>' +
            (freshRefreshing ? '<span class="vsr-fresh-refresh-spin" aria-hidden="true"></span>Matching\u2026' : 'Refresh') +
        '</button>';
    }

    // The board is a STORED snapshot, so say how old it is — otherwise there is no
    // way to tell a quiet hour from a refresh that has not run.
    function freshStampHTML() {
        var at = freshCache && freshCache.fetched_at;
        if (freshRefreshing) return 'Pulling the board and matching each release against EXT.to\u2026';
        if (!at) return 'Movies and TV releases from the EXT.to homepage, presented in SoulSync.';
        return 'Updated ' + esc(at) + ' \u00b7 refreshed hourly by the \u2018Refresh Fresh Releases\u2019 automation, or on demand.';
    }

    function refreshFreshReleases() {
        if (freshRefreshing) return;
        freshRefreshing = true;
        var token = ++freshSeq;
        renderFreshReleases();
        fetch(FRESH_URL + '/refresh', { method: 'POST', headers: { 'Accept': 'application/json' } })
            .then(_json).then(function (d) {
                if (token !== freshSeq) return;
                freshRefreshing = false;
                if (d && d.success) {
                    freshCache = d;
                    if (typeof showToast === 'function' && d.stats) {
                        showToast('Fresh Releases updated - ' + (d.stats.fetched || 0) + ' newly matched, ' +
                            (d.stats.cached || 0) + ' from cache', 'success');
                    }
                } else if (d && d.error && typeof showToast === 'function') {
                    showToast(d.error, 'error');
                }
                if (mode === 'fresh') renderFreshReleases();
            }).catch(function () {
                if (token !== freshSeq) return;
                freshRefreshing = false;
                if (typeof showToast === 'function') showToast('The refresh could not be started.', 'error');
                if (mode === 'fresh') renderFreshReleases();
            });
    }

    function loadFreshReleases(force) {
        if (freshLoading || (freshCache && !force)) return;
        var token = ++freshSeq;
        freshLoading = true;
        fetch(FRESH_URL, { headers: { 'Accept': 'application/json' } }).then(_json).then(function (d) {
            if (token !== freshSeq) return;
            freshLoading = false;
            freshCache = d || { error: 'Fresh Releases returned no data.' };
            if (mode === 'fresh') renderFreshReleases();
        }).catch(function () {
            if (token !== freshSeq) return;
            freshLoading = false;
            freshCache = { error: 'Fresh Releases failed to load.' };
            if (mode === 'fresh') renderFreshReleases();
        });
    }


    function freshDefaultIdentifyMode(row, category) {
        if (category === 'movies') return 'movie';
        return row && row.episode != null ? 'episode' : 'season';
    }

    // Basic Search hits arrive unlabelled - the tab searches release titles, not a
    // category - so the release name decides what the modal opens as: no season
    // token is a movie, a season with no episode is a pack. The Category select is
    // only a tiebreaker for names that parse to nothing either way.
    function basicDefaultIdentifyMode(row, category) {
        if (row && row.season != null) return row.episode != null ? 'episode' : 'season';
        if (category === 'tv' || category === 'anime') return 'episode';
        return 'movie';
    }

    // How to actually FETCH the release, per download source. Mirrors
    // video-download-view.js buildGrabPayload so the same release grabbed from
    // Basic Search and from a title's download modal is grabbed the same way:
    // Soulseek needs the peer + file (and gets the other accepted hits as its
    // retry pool), everything else hands the magnet/NZB carriers to the client.
    function identifyGrabDescriptor(row, sourceId, siblings) {
        var cfg = BASIC_SEARCH_SOURCES[sourceId] || {};
        var src = cfg.source || 'torrent';
        if (src === 'soulseek') {
            return {
                source: 'soulseek', username: row.username, filename: row.filename,
                files: row.files || [],
                candidates: (siblings || []).filter(function (x) {
                    return x && x.accepted && x.username && x.filename !== row.filename;
                }).map(function (x) {
                    return { username: x.username, filename: x.filename, size_bytes: x.size_bytes,
                        quality_label: x.quality_label, title: x.title };
                })
            };
        }
        return {
            source: src,
            username: row.username || cfg.label,        // indexer name (display only)
            filename: row.filename || row.title,
            indexer_id: row.indexer_id || cfg.indexer || sourceId,
            protocol: row.protocol || 'torrent',
            download_url: row.download_url || row.magnet_uri,
            magnet_uri: row.magnet_uri || row.download_url,
            info_url: row.info_url,       // EXT.to resolves its magnet from this at grab time
            magnet_id: row.magnet_id,
            guid: row.guid,
            candidates: []
        };
    }

    function identifyCanGrab() {
        var g = (freshIdentify && freshIdentify.grab) || {};
        return canFetchRelease(g.source, g);
    }

    function freshSearchKind() {
        return freshIdentify && freshIdentify.mode === 'movie' ? 'movie' : 'show';
    }

    function freshIdentifyQuery(row) {
        return (row && (row.search_title || row.parsed_title || row.title) || '').replace(/\b(S\d{1,3}E\d{1,3}|S\d{1,3}|Season\s*\d{1,3})\b/ig, '').trim();
    }

    function freshEnsureIdentifyModal() {
        var modal = $('[data-vsr-fresh-ident]');
        if (modal) return modal;
        document.body.insertAdjacentHTML('beforeend', '<div class="vsr-fi-backdrop hidden" data-vsr-fresh-ident>' +
            '<div class="vsr-fi-modal" role="dialog" aria-modal="true" aria-label="Identify release">' +
                '<div class="vsr-fi-head"><div><span>Fresh Release</span><h2 data-vsr-fi-title>Identify release</h2></div><button type="button" class="vsr-fi-close" data-vsr-fi-close>&times;</button></div>' +
                '<div class="vsr-fi-body">' +
                    '<div class="vsr-fi-release" data-vsr-fi-release></div>' +
                    '<div class="vsr-fi-modes" data-vsr-fi-modes></div>' +
                    '<div class="vsr-fi-fields" data-vsr-fi-fields>' +
                        '<label><span>Season</span><input type="number" min="0" max="999" data-vsr-fi-season></label>' +
                        '<label data-vsr-fi-episode-wrap><span>Episode</span><input type="number" min="1" max="999" data-vsr-fi-episode></label>' +
                    '</div>' +
                    '<label class="vsr-fi-search"><span data-vsr-fi-search-label>Search title</span><input type="text" data-vsr-fi-search autocomplete="off" spellcheck="false"></label>' +
                    '<div class="vsr-fi-results" data-vsr-fi-results></div>' +
                    '<div class="vsr-fi-note" data-vsr-fi-note></div>' +
                '</div>' +
                '<div class="vsr-fi-foot"><button type="button" class="vsr-fi-secondary" data-vsr-fi-close>Cancel</button><button type="button" class="vsr-fi-primary" data-vsr-fi-grab disabled>Start download</button></div>' +
            '</div></div>');
        modal = $('[data-vsr-fresh-ident]');
        modal.addEventListener('click', function (e) {
            if (e.target === modal || e.target.closest('[data-vsr-fi-close]')) { freshCloseIdentify(); return; }
            var modeBtn = e.target.closest('[data-vsr-fi-mode]');
            if (modeBtn && modal.contains(modeBtn)) {
                freshIdentify.mode = modeBtn.getAttribute('data-vsr-fi-mode') || freshIdentify.mode;
                freshIdentify.selected = null;
                freshRenderIdentifyModal();
                freshRunIdentifySearch();
                return;
            }
            var pick = e.target.closest('[data-vsr-fi-result]');
            if (pick && modal.contains(pick)) {
                var i = parseInt(pick.getAttribute('data-vsr-fi-result'), 10);
                freshIdentify.selected = freshIdentify.results && freshIdentify.results[i] || null;
                freshRenderIdentifyResults();
                freshUpdateGrabButton();
                return;
            }
            if (e.target.closest('[data-vsr-fi-grab]')) freshGrabIdentifiedRelease();
        });
        modal.querySelector('[data-vsr-fi-search]').addEventListener('input', function () {
            if (freshIdentifyTimer) clearTimeout(freshIdentifyTimer);
            freshIdentifyTimer = setTimeout(freshRunIdentifySearch, 260);
        });
        modal.querySelector('[data-vsr-fi-season]').addEventListener('input', freshUpdateGrabButton);
        modal.querySelector('[data-vsr-fi-episode]').addEventListener('input', freshUpdateGrabButton);
        return modal;
    }

    function freshOpenIdentify(category, index) {
        var rows = freshRows(category);
        var row = rows[index];
        if (!row || !(row.download_url || row.magnet_uri)) return;
        freshIdentify = {
            row: row,
            category: category,
            modes: category === 'movies' ? ['movie'] : ['episode', 'season'],
            mode: freshDefaultIdentifyMode(row, category),
            grab: identifyGrabDescriptor(row, 'extto', null),
            selected: null,
            results: [],
            grabbing: false
        };
        var modal = freshEnsureIdentifyModal();
        var seasonInput = modal.querySelector('[data-vsr-fi-season]');
        var episodeInput = modal.querySelector('[data-vsr-fi-episode]');
        if (seasonInput) seasonInput.value = row.season != null ? row.season : '';
        if (episodeInput) episodeInput.value = row.episode != null ? row.episode : '';
        modal.classList.remove('hidden');
        freshRenderIdentifyModal();
        var input = modal.querySelector('[data-vsr-fi-search]');
        if (input) { input.value = freshIdentifyQuery(row); try { input.focus(); input.select(); } catch (err) { /* ignore */ } }
        freshRunIdentifySearch();
    }

    // Basic Search's Identify: the same modal, opened over a hit from any source.
    // Every mode is offered because a release-title search can turn up a movie, an
    // episode or a pack in the same result list.
    function basicOpenIdentify(sourceId, index) {
        var rows = basicRowsBySource[sourceId] || [];
        var row = rows[index];
        if (!row || !basicHitGrabbable(row, sourceId)) return;
        var category = (($('[data-vsr-basic-category]') || {}).value || 'all');
        freshIdentify = {
            row: row,
            category: category,
            modes: ['movie', 'episode', 'season'],
            mode: basicDefaultIdentifyMode(row, category),
            grab: identifyGrabDescriptor(row, sourceId, rows),
            selected: null,
            results: [],
            grabbing: false
        };
        var modal = freshEnsureIdentifyModal();
        var seasonInput = modal.querySelector('[data-vsr-fi-season]');
        var episodeInput = modal.querySelector('[data-vsr-fi-episode]');
        if (seasonInput) seasonInput.value = row.season != null ? row.season : '';
        if (episodeInput) episodeInput.value = row.episode != null ? row.episode : '';
        modal.classList.remove('hidden');
        freshRenderIdentifyModal();
        var input = modal.querySelector('[data-vsr-fi-search]');
        if (input) { input.value = freshIdentifyQuery(row); try { input.focus(); input.select(); } catch (err) { /* ignore */ } }
        freshRunIdentifySearch();
    }

    function freshCloseIdentify() {
        var modal = $('[data-vsr-fresh-ident]');
        if (modal) modal.classList.add('hidden');
        freshIdentify = null;
        if (freshIdentifyTimer) clearTimeout(freshIdentifyTimer);
    }

    function freshRenderIdentifyModal() {
        var modal = freshEnsureIdentifyModal();
        var row = freshIdentify && freshIdentify.row || {};
        var kind = freshSearchKind();
        modal.querySelector('[data-vsr-fi-title]').textContent = freshIdentify.mode === 'movie' ? 'Identify movie' : (freshIdentify.mode === 'episode' ? 'Identify episode' : 'Identify season pack');
        // Fresh rows carry the homepage's own size + age strings; a Basic Search hit
        // carries neither, so fall back to the same size/health labels its result card
        // showed rather than printing 'Size unknown - Age unknown' over a real release.
        var sizeText = row.size_text || basicSizeLabel(row);
        var ageText = row.age || basicHealthLabel(row);
        modal.querySelector('[data-vsr-fi-release]').innerHTML = '<strong>' + esc(row.title || 'Untitled release') + '</strong>' +
            '<div><span>' + esc(sizeText) + '</span><span>' + esc(ageText) + '</span><span>' + esc(row.quality_label || row.resolution || 'Release') + '</span></div>';
        var modes = freshIdentify.modes || (freshIdentify.category === 'movies' ? ['movie'] : ['episode', 'season']);
        modal.querySelector('[data-vsr-fi-modes]').innerHTML = modes.map(function (m) {
            var label = m === 'movie' ? 'Movie' : (m === 'episode' ? 'Episode' : 'Season pack');
            return '<button type="button" class="' + (freshIdentify.mode === m ? 'active' : '') + '" data-vsr-fi-mode="' + m + '">' + label + '</button>';
        }).join('');
        modal.querySelector('[data-vsr-fi-fields]').hidden = freshIdentify.mode === 'movie';
        modal.querySelector('[data-vsr-fi-episode-wrap]').hidden = freshIdentify.mode !== 'episode';
        var season = modal.querySelector('[data-vsr-fi-season]');
        var episode = modal.querySelector('[data-vsr-fi-episode]');
        if (season && season.value === '') season.value = row.season != null ? row.season : '';
        if (episode && episode.value === '') episode.value = row.episode != null ? row.episode : '';
        modal.querySelector('[data-vsr-fi-search-label]').textContent = kind === 'movie' ? 'Search movies' : 'Search TV shows';
        freshRenderIdentifyResults();
        freshUpdateGrabButton();
    }

    function freshRunIdentifySearch() {
        if (!freshIdentify) return;
        var modal = freshEnsureIdentifyModal();
        var q = (modal.querySelector('[data-vsr-fi-search]').value || '').trim();
        var results = modal.querySelector('[data-vsr-fi-results]');
        freshIdentify.selected = null;
        freshUpdateGrabButton();
        if (!q) { freshIdentify.results = []; results.innerHTML = '<div class="vsr-fi-empty">Search for the real title to attach this release.</div>'; return; }
        var seq = ++freshIdentifySeq;
        results.innerHTML = '<div class="vsr-fi-empty"><span class="vsr-basic-loader" aria-hidden="true"><i></i><i></i><i></i></span><span>Searching...</span></div>';
        fetch(SEARCH_URL + '?q=' + encodeURIComponent(q), { headers: { 'Accept': 'application/json' } }).then(_json).then(function (d) {
            if (!freshIdentify || seq !== freshIdentifySeq) return;
            var want = freshSearchKind();
            freshIdentify.results = ((d && d.results) || []).filter(function (it) { return it && it.kind === want; }).slice(0, 8);
            freshRenderIdentifyResults();
        }).catch(function () {
            if (!freshIdentify || seq !== freshIdentifySeq) return;
            freshIdentify.results = [];
            results.innerHTML = '<div class="vsr-fi-empty">Search failed. Try again.</div>';
        });
    }

    function freshRenderIdentifyResults() {
        if (!freshIdentify) return;
        var modal = freshEnsureIdentifyModal();
        var rows = freshIdentify.results || [];
        var host = modal.querySelector('[data-vsr-fi-results]');
        if (!rows.length) { host.innerHTML = '<div class="vsr-fi-empty">No matching ' + (freshSearchKind() === 'movie' ? 'movies' : 'shows') + ' yet.</div>'; return; }
        host.innerHTML = rows.map(function (it, i) {
            var on = freshIdentify.selected && freshIdentify.selected.tmdb_id === it.tmdb_id;
            var poster = it.poster || it.poster_url || '';
            return '<button type="button" class="vsr-fi-result ' + (on ? 'active' : '') + '" data-vsr-fi-result="' + i + '">' +
                (poster ? '<img src="' + esc(poster) + '" alt="" loading="lazy">' : '<span class="vsr-fi-poster">' + (it.kind === 'movie' ? 'M' : 'TV') + '</span>') +
                '<span><strong>' + esc(it.title || 'Untitled') + '</strong><em>' + esc([it.year, it.kind === 'movie' ? 'Movie' : 'TV Show'].filter(Boolean).join(' - ')) + '</em></span>' +
            '</button>';
        }).join('');
    }

    function freshNumInput(sel) {
        var n = parseInt((freshEnsureIdentifyModal().querySelector(sel) || {}).value, 10);
        return isFinite(n) ? n : null;
    }

    function freshUpdateGrabButton() {
        var modal = $('[data-vsr-fresh-ident]');
        if (!modal || !freshIdentify) return;
        var ok = !!freshIdentify.selected && identifyCanGrab();
        if (freshIdentify.mode === 'episode') ok = ok && freshNumInput('[data-vsr-fi-season]') != null && freshNumInput('[data-vsr-fi-episode]') != null;
        if (freshIdentify.mode === 'season') ok = ok && freshNumInput('[data-vsr-fi-season]') != null;
        var btn = modal.querySelector('[data-vsr-fi-grab]');
        if (btn) { btn.disabled = !ok || freshIdentify.grabbing; btn.textContent = freshIdentify.grabbing ? 'Starting...' : 'Start download'; }
    }

    function freshGrabPayload() {
        var row = freshIdentify.row;
        var item = freshIdentify.selected;
        var isMovie = freshIdentify.mode === 'movie';
        var season = freshNumInput('[data-vsr-fi-season]');
        var episode = freshNumInput('[data-vsr-fi-episode]');
        var title = item.title || row.search_title || row.title;
        var year = item.year || row.year;
        var payload = {
            kind: isMovie ? 'movie' : 'show',
            title: title,
            release_title: row.title,
            size_bytes: row.size_bytes || 0,
            quality_label: row.quality_label || row.resolution,
            media_id: item.tmdb_id,
            media_source: 'tmdb',
            year: year,
            poster_url: item.poster || item.poster_url,
            search_ctx: isMovie ? { scope: 'movie', title: title, year: year } :
                { scope: freshIdentify.mode === 'episode' ? 'episode' : 'season', title: title, year: year,
                    season: season, episode: freshIdentify.mode === 'episode' ? episode : null }
        };
        // 'files' is the pack fan-out list, not a grab field - it never goes on the wire.
        var grab = freshIdentify.grab || {};
        Object.keys(grab).forEach(function (k) { if (k !== 'files') payload[k] = grab[k]; });
        return payload;
    }

    function freshGrabIdentifiedRelease() {
        if (!freshIdentify || freshIdentify.grabbing) return;
        freshUpdateGrabButton();
        var modal = freshEnsureIdentifyModal();
        var note = modal.querySelector('[data-vsr-fi-note]');
        freshIdentify.grabbing = true;
        freshUpdateGrabButton();
        if (note) note.textContent = '';
        var payload = freshGrabPayload();
        var grab = freshIdentify.grab || {};
        // Soulseek lists a pack's files BEFORE you download it, so a season grab fans
        // out into one row per episode right now; a torrent's files don't exist until
        // it finishes, so it goes in as ONE season-scoped row and the monitor maps the
        // finished folder. Same split as video-download-view.js _grabPack.
        var url = '/api/video/downloads/grab';
        if (freshIdentify.mode === 'season' && grab.source === 'soulseek'
                && grab.username && (grab.files || []).length > 1) {
            url = '/api/video/downloads/grab-pack';
            payload = { username: grab.username, files: grab.files, title: payload.title,
                quality_label: payload.quality_label, media_id: payload.media_id,
                media_source: payload.media_source, year: payload.year, poster_url: payload.poster_url };
        }
        fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify(payload) }).then(_json).then(function (res) {
            if (!freshIdentify) return;
            freshIdentify.grabbing = false;
            if (res && res.ok) {
                if (typeof showToast === 'function') showToast('Download started', 'success');
                document.dispatchEvent(new CustomEvent('soulsync:video-download-started'));
                freshCloseIdentify();
                return;
            }
            if (note) note.textContent = (res && res.error) || 'The download could not be started.';
            freshUpdateGrabButton();
        }).catch(function () {
            if (!freshIdentify) return;
            freshIdentify.grabbing = false;
            if (note) note.textContent = 'The download request failed.';
            freshUpdateGrabButton();
        });
    }    function setMode(next) {
        mode = next === 'basic' ? 'basic' : (next === 'fresh' ? 'fresh' : 'enhanced');
        document.querySelectorAll('[data-vsr-tab]').forEach(function (b) {
            var on = b.getAttribute('data-vsr-tab') === mode;
            b.classList.toggle('active', on);
            b.setAttribute('aria-selected', on ? 'true' : 'false');
        });
        document.querySelectorAll('[data-vsr-panel]').forEach(function (p) {
            var on = p.getAttribute('data-vsr-panel') === mode;
            p.classList.toggle('active', on);
            p.hidden = !on;
        });
        reqSeq++;
        if (mode === 'basic') { ensureBasicSourceConfig(); renderBasicPreview(); }
        else if (mode === 'fresh') renderFreshReleases();
        else if (!lastQuery) showIdle();
        else runSearch(lastQuery);
    }

    // Netflix-style poster card with owned/preview ribbon + hover affordance.
    function titleCard(it) {
        var fallback = it.kind === 'movie' ? '🎬' : '📺';
        var img = it.poster
            ? '<img src="' + esc(it.poster) + '" alt="" loading="lazy" ' +
              'onerror="this.outerHTML=\'<div class=&quot;vsr-poster-ph&quot;>' + fallback + '</div>\'">'
            : '<div class="vsr-poster-ph">' + fallback + '</div>';
        var owned = it.library_id != null;
        var ribbon = owned
            ? '<span class="vsr-ribbon vsr-ribbon--owned">In Library</span>'
            : '<span class="vsr-ribbon vsr-ribbon--preview">Preview</span>';
        var rating = it.rating
            ? '<span class="vsr-rating">★ ' + (Math.round(it.rating * 10) / 10) + '</span>' : '';
        // Owned → real library detail; otherwise the TMDB-backed (preview) detail.
        var source = owned ? 'library' : 'tmdb';
        var id = owned ? it.library_id : it.tmdb_id;
        var href = '/video-detail/' + source + '/' + it.kind + '/' + id;
        var sub = [it.year, it.kind === 'movie' ? 'Movie' : 'TV'].filter(Boolean).join(' · ');
        var cb = window.VideoGet ? VideoGet.cardButton({ kind: it.kind, tmdbId: it.tmdb_id,
            libraryId: it.library_id, title: it.title, poster: it.poster, status: it.status, source: source }) : '';
        return '<a class="vsr-card" href="' + href + '" ' +
            'data-vsr-open="' + it.kind + '" data-vsr-source="' + source + '" data-vsr-id="' + id + '">' + cb +
            '<div class="vsr-poster">' + img + ribbon + rating +
            '<span class="vsr-peek" aria-hidden="true">i</span></div>' +
            '<div class="vsr-info"><span class="vsr-name" title="' + esc(it.title) + '">' + esc(it.title) +
            '</span><span class="vsr-sub">' + esc(sub) + '</span></div></a>';
    }

    function personCard(it) {
        var img = it.poster
            ? '<img src="' + esc(it.poster) + '" alt="" loading="lazy" ' +
              'onerror="this.outerHTML=\'<div class=&quot;vsr-poster-ph&quot;>👤</div>\'">'
            : '<div class="vsr-poster-ph">👤</div>';
        var sub = it.known_for ? it.known_for : (it.department || '');
        var cb = window.VideoGet ? VideoGet.cardButton({ kind: 'person', tmdbId: it.tmdb_id,
            title: it.title, poster: it.poster }) : '';
        return '<a class="vsr-card vsr-card--person" href="#" ' +
            'data-vsr-open="person" data-vsr-id="' + it.tmdb_id + '">' + cb +
            '<div class="vsr-poster">' + img + '</div>' +
            '<div class="vsr-info vsr-info--center"><span class="vsr-name" title="' + esc(it.title) + '">' +
            esc(it.title) + '</span><span class="vsr-sub">' + esc(sub) + '</span></div></a>';
    }

    // A studio (production company) — a wide logo tile, since a studio has no
    // poster. Opens the studio detail page (a collection of films) via the shared
    // data-vsr-open dispatch.
    function studioCard(it) {
        var logo = it.logo
            ? '<img src="' + esc(it.logo) + '" alt="" loading="lazy" ' +
              'onerror="this.outerHTML=\'<span class=&quot;vsr-studio-ph&quot;>&#127902;</span>\'">'
            : '<span class="vsr-studio-ph">&#127902;</span>';
        var n = it.movie_count;
        var sub = n ? (n + (n === 1 ? ' film' : ' films')) : 'Studio';
        return '<a class="vsr-card vsr-card--studio" href="#" ' +
            'data-vsr-open="studio" data-vsr-source="tmdb" data-vsr-id="' + it.tmdb_id + '">' +
            '<div class="vsr-studio-logo">' + logo + '</div>' +
            '<div class="vsr-info vsr-info--center"><span class="vsr-name" title="' + esc(it.title) + '">' +
            esc(it.title) + '</span><span class="vsr-sub">' + esc(sub) + '</span></div></a>';
    }
    // ── progressive, per-group rendering (Netflix-style) ─────────────────────
    // Order: Movies → TV Shows → YouTube channels → People → Studios. Each group is
    // its OWN section that fills in when its source resolves — the fast multi-search
    // (movies/shows/people) paints instantly while YouTube + Studios (slower, parallel
    // fetches) stream in after. A group's DOM is only touched when ITS data lands, so
    // already-painted cards never re-animate.
    var _ORDER = [
        { kind: 'movie', label: 'Movies', icon: '🎬' },
        { kind: 'show', label: 'TV Shows', icon: '📺' },
        { kind: 'youtube', label: 'YouTube channels', icon: '▶' },
        { kind: 'person', label: 'People', icon: '👤' },
        { kind: 'studio', label: 'Studios', icon: '🎞️' },
    ];
    var _META = {}; _ORDER.forEach(function (g) { _META[g.kind] = g; });
    var _done = { multi: false, studio: false, youtube: false };

    function skelCards(kind) {
        var n = kind === 'person' ? 5 : kind === 'studio' ? 4 : 6;
        var studio = kind === 'studio';
        var art = studio ? '<div class="vsr-studio-logo vyt-skel"></div>' : '<div class="vsr-poster vyt-skel"></div>';
        var extra = studio ? ' vsr-card--studio' : (kind === 'person' ? ' vsr-card--person' : '');
        var ic = (kind === 'person' || studio) ? ' vsr-info--center' : '';
        var out = '';
        for (var i = 0; i < n; i++)
            out += '<div class="vsr-card vsr-card--skel' + extra + '">' + art +
                '<div class="vsr-info' + ic + '"><span class="vyt-skel vyt-skel-line"></span>' +
                '<span class="vyt-skel vyt-skel-line vyt-skel-line--sm"></span></div></div>';
        return out;
    }
    function slotHTML(g, inner, count, loading) {
        var grid = 'vsr-grid' + (g.kind === 'studio' ? ' vsr-grid--studios' : '');
        var badge = loading ? '<span class="vsr-yt-loading">searching…</span>'
            : (count != null ? '<span class="vsr-group-count">' + count + '</span>' : '');
        return '<section class="vsr-group" data-group="' + g.kind + '">' +
            '<h2 class="vsr-group-title"><span class="vsr-group-ic" aria-hidden="true">' + g.icon + '</span>' +
            esc(g.label) + badge + '</h2>' +
            '<div class="' + grid + '">' + inner + '</div></section>';
    }
    // Replace a group's skeleton with real cards, or fade it out when it has none.
    function fillGroup(kind, inner, count) {
        var host = $('[data-video-search-results]'); if (!host) return;
        var node = host.querySelector('[data-group="' + kind + '"]');
        if (!inner) {
            if (node) {
                node.classList.add('vsr-group--gone');
                setTimeout(function () { if (node.parentNode) node.parentNode.removeChild(node); checkEmpty(); }, 240);
            } else { checkEmpty(); }
            return;
        }
        var html = slotHTML(_META[kind], inner, count, false);
        if (node) node.outerHTML = html;
        else host.insertAdjacentHTML('beforeend', html);
        var fresh = host.querySelector('[data-group="' + kind + '"]');
        if (fresh && window.VideoWatchlist) VideoWatchlist.hydrate(fresh);
        checkEmpty();
    }
    function fillMulti(results) {
        ['movie', 'show', 'person'].forEach(function (kind) {
            var items = (results || []).filter(function (r) { return r.kind === kind; });
            var fn = kind === 'person' ? personCard : titleCard;
            fillGroup(kind, items.length ? items.map(fn).join('') : null, items.length);
        });
    }
    function fillStudios(items) {
        fillGroup('studio', (items && items.length) ? items.map(studioCard).join('') : null,
                  items ? items.length : 0);
    }
    function fillYt(channels) {
        var ok = channels && channels.length && window.VideoYoutube;
        fillGroup('youtube', ok ? channels.map(function (c) { return VideoYoutube.channelResultCard(c); }).join('') : null,
                  channels ? channels.length : 0);
    }
    // Only declare "No results" once every source has resolved and nothing remains.
    function checkEmpty() {
        if (!(_done.multi && _done.studio && _done.youtube)) return;
        var host = $('[data-video-search-results]');
        var any = host && host.querySelector('.vsr-group');
        show('[data-video-search-empty]', !any);
        if (!any && host) host.innerHTML = '';
    }

    // Idle state: a "Trending this week" rail so the page isn't a blank box.
    // ── recent searches (remembered on COMMIT — opening a result — not on
    //    every debounced keystroke, so the list holds real queries, not typos) ─
    function recents() {
        try { var r = JSON.parse(localStorage.getItem('vsRecent') || '[]'); return Array.isArray(r) ? r : []; }
        catch (e) { return []; }
    }
    function rememberSearch(q) {
        q = (q || '').trim();
        if (!q || q.length < 2) return;
        var r = recents().filter(function (x) { return x.toLowerCase() !== q.toLowerCase(); });
        r.unshift(q);
        try { localStorage.setItem('vsRecent', JSON.stringify(r.slice(0, 8))); } catch (e) { /* private mode */ }
    }
    function recentsHTML() {
        var r = recents();
        if (!r.length) return '';
        return '<div class="vsr-recent"><span class="vsr-recent-label">Recent</span>' +
            r.map(function (q) {
                return '<button class="vsr-recent-chip" type="button" data-vsr-recent="' + esc(q) + '">' + esc(q) + '</button>';
            }).join('') +
            '<button class="vsr-recent-clear" type="button" data-vsr-recent-clear title="Clear recent searches">✕</button>' +
            '</div>';
    }

    function renderTrending() {
        var host = $('[data-video-search-results]');
        if (!host || !trendingCache || !trendingCache.length) return;
        show('[data-video-search-hint]', false);
        show('[data-video-search-empty]', false);
        host.innerHTML = recentsHTML() +
            '<div class="vsr-group"><h2 class="vsr-group-title">' +
            '<span class="vsr-group-ic" aria-hidden="true">🔥</span>Trending this week</h2>' +
            '<div class="vsr-grid">' + trendingCache.map(titleCard).join('') + '</div></div>';
        if (window.VideoWatchlist) VideoWatchlist.hydrate(host);
    }
    function loadTrending() {
        if (trendingCache !== null) { if (!lastQuery) renderTrending(); return; }
        fetch('/api/video/trending', { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                trendingCache = (d && d.results) ? d.results : [];
                if (!lastQuery) renderTrending();
            })
            .catch(function () { trendingCache = []; });
    }
    function showIdle() {
        if (trendingCache && trendingCache.length) { renderTrending(); return; }
        show('[data-video-search-empty]', false);
        show('[data-video-search-hint]', true);
        var host = $('[data-video-search-results]'); if (host) host.innerHTML = recentsHTML();
        loadTrending();
    }

    function _json(r) { return r.ok ? r.json() : null; }
    var _accept = { headers: { 'Accept': 'application/json' } };
    function runSearch(q) {
        var seq = ++reqSeq;
        var doYt = !!(window.VideoYoutube && q.length >= 2);
        _done = { multi: false, studio: false, youtube: !doYt };   // no YT search → that leg is "done"
        show('[data-video-search-loading]', false);   // skeletons stand in for the spinner now
        show('[data-video-search-hint]', false);
        show('[data-video-search-empty]', false);
        // Instant ordered skeletons, so the page reacts the moment you type.
        var host = $('[data-video-search-results]');
        if (host) host.innerHTML = _ORDER
            .filter(function (g) { return g.kind !== 'youtube' || doYt; })
            .map(function (g) { return slotHTML(g, skelCards(g.kind), null, true); }).join('');

        // Fast multi-search (movies / shows / people) — paints first.
        fetch(SEARCH_URL + '?q=' + encodeURIComponent(q), _accept).then(_json)
            .then(function (d) { if (seq !== reqSeq) return; _done.multi = true; fillMulti((d && d.results) || []); })
            .catch(function () { if (seq !== reqSeq) return; _done.multi = true; fillMulti([]); });

        // Studios — parallel, slower (per-studio film-count ranking); streams in after.
        fetch(STUDIO_URL + '?q=' + encodeURIComponent(q), _accept).then(_json)
            .then(function (d) { if (seq !== reqSeq) return; _done.studio = true; fillStudios((d && d.results) || []); })
            .catch(function () { if (seq !== reqSeq) return; _done.studio = true; fillStudios([]); });

        // YouTube channels — parallel, best-effort.
        if (doYt) VideoYoutube.searchChannels(q)
            .then(function (d) { if (seq !== reqSeq) return; _done.youtube = true; fillYt((d && d.channels) || []); })
            .catch(function () { if (seq !== reqSeq) return; _done.youtube = true; fillYt([]); });
    }

    // A pasted YouTube channel OR playlist link → resolve + render a Follow chip
    // instead of a normal title search (the obscure-channel / playlist entry point).
    function runChannel(ref) {
        var seq = ++reqSeq;
        show('[data-video-search-loading]', true);
        VideoYoutube.resolve(ref).then(function (d) {
            if (seq !== reqSeq) return;
            show('[data-video-search-loading]', false);
            show('[data-video-search-hint]', false);
            show('[data-video-search-empty]', false);
            var host = $('[data-video-search-results]'); if (!host) return;
            if (d && d.success && d.playlist) {
                lastPlaylist = d.playlist; lastChannel = null;
                host.innerHTML = '<div class="vsr-group"><h2 class="vsr-group-title">' +
                    '<span class="vsr-group-ic" aria-hidden="true">▶</span>YouTube playlist</h2>' +
                    '<div class="vyt-search">' + VideoYoutube.playlistCard(d.playlist, d.following) + '</div></div>';
                return;
            }
            if (!d || !d.success || !d.channel) {
                host.innerHTML = '<div class="vsr-group"><div class="vyt-miss">' +
                    'Couldn’t read that link. Paste a channel link like ' +
                    '<code>youtube.com/@handle</code> or a playlist link.</div></div>';
                return;
            }
            lastChannel = d.channel; lastPlaylist = null;
            host.innerHTML = '<div class="vsr-group"><h2 class="vsr-group-title">' +
                '<span class="vsr-group-ic" aria-hidden="true">▶</span>YouTube channel</h2>' +
                '<div class="vyt-search">' + VideoYoutube.searchCard(d.channel, d.following) + '</div></div>';
        }).catch(function () {
            if (seq !== reqSeq) return;
            show('[data-video-search-loading]', false);
        });
    }

    function onInput(val) {
        var q = (val || '').trim();
        if (queryContext && q !== queryContext.q) clearQueryContext();
        lastQuery = q;
        if (timer) clearTimeout(timer);
        if (!q) {
            reqSeq++;                                 // cancel any in-flight render
            show('[data-video-search-loading]', false);
            showIdle();                               // back to the trending rail
            return;
        }
        if (window.VideoYoutube && (VideoYoutube.isChannelRef(q) || VideoYoutube.isPlaylistRef(q))) {
            timer = setTimeout(function () { runChannel(q); }, 360);
            return;
        }
        timer = setTimeout(function () { runSearch(q); }, 320);
    }

    // Follow / un-follow the resolved channel chip.
    function toggleFollow(btn) {
        if (!lastChannel) return;
        var on = btn.classList.contains('vyt-follow--on');
        btn.disabled = true;
        var done = function () { btn.disabled = false; document.dispatchEvent(new CustomEvent('soulsync:video-wishlist-changed')); };
        if (on) {
            VideoYoutube.unfollow(lastChannel.youtube_id).then(function () {
                btn.classList.remove('vyt-follow--on'); btn.innerHTML = '+ Follow'; done();
            }).catch(function () { btn.disabled = false; });
        } else {
            VideoYoutube.follow(lastChannel).then(function (d) {
                if (d && d.success) {
                    btn.classList.add('vyt-follow--on'); btn.innerHTML = '✓ Following';
                    if (typeof showToast === 'function')
                        showToast('Added ' + lastChannel.title + ' to watchlist', 'success');
                }
                done();
            }).catch(function () { btn.disabled = false; });
        }
    }

    // Add / remove the resolved playlist chip to the watchlist (standard watchlist button).
    function setPlBtn(btn, on) {
        btn.classList.toggle('watching', on);
        var ic = btn.querySelector('.watchlist-icon'); if (ic) ic.textContent = on ? '✓' : '＋';
        var tx = btn.querySelector('.watchlist-text'); if (tx) tx.textContent = on ? 'In Watchlist' : 'Add to Watchlist';
    }
    function togglePlaylistFollow(btn) {
        if (!lastPlaylist) return;
        var on = btn.classList.contains('watching');
        btn.disabled = true;
        var done = function () { btn.disabled = false; document.dispatchEvent(new CustomEvent('soulsync:video-wishlist-changed')); };
        if (on) {
            VideoYoutube.unfollowPlaylist(lastPlaylist.playlist_id).then(function () {
                setPlBtn(btn, false); done();
            }).catch(function () { btn.disabled = false; });
        } else {
            VideoYoutube.followPlaylist(lastPlaylist).then(function (d) {
                if (d && d.success) {
                    setPlBtn(btn, true);
                    if (typeof showToast === 'function')
                        showToast('Added ' + lastPlaylist.title + ' to watchlist', 'success');
                }
                done();
            }).catch(function () { btn.disabled = false; });
        }
    }

    function openCard(card) {
        var kind = card.getAttribute('data-vsr-open');
        var id = parseInt(card.getAttribute('data-vsr-id'), 10);
        if (isNaN(id)) return;
        rememberSearch(lastQuery);   // a picked result marks the query as a keeper
        if (kind === 'person') {
            document.dispatchEvent(new CustomEvent('soulsync:video-open-detail',
                { detail: { kind: 'person', id: id, source: 'tmdb' } }));
        } else {
            document.dispatchEvent(new CustomEvent('soulsync:video-open-detail',
                { detail: { kind: kind, id: id, source: card.getAttribute('data-vsr-source') || 'tmdb' } }));
        }
    }

    function wire() {
        if (wired) return;
        wired = true;
        document.querySelectorAll('[data-vsr-tab]').forEach(function (tab) {
            tab.addEventListener('click', function () { setMode(tab.getAttribute('data-vsr-tab')); });
        });
        var basicForm = $('[data-vsr-basic-form]');
        if (basicForm) {
            basicForm.addEventListener('submit', function (e) { e.preventDefault(); setMode('basic'); });
            basicForm.addEventListener('change', function () { if (mode === 'basic') renderBasicPreview(); });
        }
        var input = $('[data-video-search-input]');
        if (input) {
            input.addEventListener('input', function () { onInput(input.value); });
            input.addEventListener('keydown', function (e) {
                if (e.key === 'Escape') {
                    if (input.value) { input.value = ''; onInput(''); }
                    return;
                }
                if (e.key !== 'Enter' || !input.value.trim()) return;   // idle page: Enter is a no-op
                // Enter = open the top result (the fast path when the first hit is right)
                var host = $('[data-video-search-results]');
                var first = host && host.querySelector('[data-vsr-open]');
                if (first) { e.preventDefault(); openCard(first); }
            });
        }

        document.addEventListener('click', function (e) {
            var clear = e.target.closest('[data-video-search-context-clear]');
            if (!clear) return;
            e.preventDefault();
            clearQueryContext();
            var input = $('[data-video-search-input]');
            if (input) { input.value = ''; try { input.focus(); } catch (err) { /* ignore */ } }
            onInput('');
        });

        var results = $('[data-video-search-results]');
        if (results) {
            results.addEventListener('click', function (e) {
                if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
                var bf = e.target.closest('[data-vsr-basic-focus]');
                if (bf && results.contains(bf)) {
                    e.preventDefault();
                    var bq = $('[data-vsr-basic-query]');
                    if (bq) { try { bq.focus(); } catch (err) { /* ignore */ } }
                    return;
                }
                var st = e.target.closest('[data-vsr-basic-source-tab]');
                if (st && results.contains(st)) {
                    e.preventDefault();
                    setBasicSourceTab(st.getAttribute('data-vsr-basic-source-tab'));
                    return;
                }
                var freshPick = e.target.closest('[data-vsr-fresh-pick]');
                if (freshPick && results.contains(freshPick)) {
                    e.preventDefault();
                    var parts = String(freshPick.getAttribute('data-vsr-fresh-pick') || '').split(':');
                    freshOpenIdentify(parts[0], parseInt(parts[1], 10));
                    return;
                }
                var basicPick = e.target.closest('[data-vsr-basic-grab]');
                if (basicPick && results.contains(basicPick)) {
                    e.preventDefault();
                    var bits = String(basicPick.getAttribute('data-vsr-basic-grab') || '').split(':');
                    basicOpenIdentify(bits[0], parseInt(bits[1], 10));
                    return;
                }
                var fr = e.target.closest('[data-vsr-fresh-refresh]');
                if (fr && results.contains(fr)) {
                    e.preventDefault();
                    refreshFreshReleases();
                    return;
                }
                var ft = e.target.closest('[data-vsr-fresh-toggle]');
                if (ft && results.contains(ft)) {
                    e.preventDefault();
                    var tb = String(ft.getAttribute('data-vsr-fresh-toggle') || '').split(':');
                    freshToggleRow(tb[0], parseInt(tb[1], 10));
                    return;
                }
                var bt = e.target.closest('[data-vsr-basic-toggle]');
                if (bt && results.contains(bt)) {
                    e.preventDefault();
                    var bb = String(bt.getAttribute('data-vsr-basic-toggle') || '').split(':');
                    basicToggleHit(bb[0], parseInt(bb[1], 10));
                    return;
                }
                var fp = e.target.closest('[data-vsr-fresh-period]');
                if (fp && results.contains(fp)) {
                    e.preventDefault();
                    freshPeriod = fp.getAttribute('data-vsr-fresh-period') || 'day';
                    renderFreshReleases();
                    return;
                }
                var rc = e.target.closest('[data-vsr-recent]');
                if (rc && results.contains(rc)) {
                    e.preventDefault();
                    var q = rc.getAttribute('data-vsr-recent') || '';
                    var inp = $('[data-video-search-input]');
                    if (inp) { inp.value = q; try { inp.focus(); } catch (err) { /* ignore */ } }
                    onInput(q);
                    return;
                }
                var rcl = e.target.closest('[data-vsr-recent-clear]');
                if (rcl && results.contains(rcl)) {
                    e.preventDefault();
                    try { localStorage.removeItem('vsRecent'); } catch (err) { /* ignore */ }
                    showIdle();
                    return;
                }
                var fb = e.target.closest('[data-vyt-follow]');
                if (fb && results.contains(fb)) { e.preventDefault(); toggleFollow(fb); return; }
                var pfb = e.target.closest('[data-vyt-follow-playlist]');
                if (pfb && results.contains(pfb)) { e.preventDefault(); togglePlaylistFollow(pfb); return; }
                var ytc = e.target.closest('[data-vyt-open-channel]');
                if (ytc && results.contains(ytc)) {
                    e.preventDefault();
                    document.dispatchEvent(new CustomEvent('soulsync:video-open-detail',
                        { detail: { kind: 'channel', source: 'youtube', id: ytc.getAttribute('data-vyt-open-channel') } }));
                    return;
                }
                var ytp = e.target.closest('[data-vyt-playlist]');   // the chip (not its button) → open detail
                if (ytp && results.contains(ytp)) {
                    e.preventDefault();
                    document.dispatchEvent(new CustomEvent('soulsync:video-open-detail',
                        { detail: { kind: 'playlist', source: 'youtube', id: ytp.getAttribute('data-vyt-playlist') } }));
                    return;
                }
                var card = e.target.closest('[data-vsr-open]');
                if (!card || !results.contains(card)) return;
                e.preventDefault();
                openCard(card);
            });
        }
    }

    function onPageShown(e) {
        if (!e || e.detail !== PAGE_ID) return;
        wire();
        var input = $('[data-video-search-input]');
        if (_pendingQuery && input) {   // a keyword chip navigated here (#1042)
            input.value = _pendingQuery.q;
            queryContext = _pendingQuery;
            renderQueryContext();
            onInput(_pendingQuery.q);
            _pendingQuery = null;
            return;
        }
        renderQueryContext();
        if (input) { try { input.focus(); } catch (err) { /* ignore */ } }
        if (!lastQuery) loadTrending();               // fill the idle page
    }

    // Cross-page search-a-keyword (#1042): a keyword chip on a detail page
    // navigates here with a query. Stash it; onPageShown (fired by the nav) runs
    // it. Apply immediately too when we're already the active page.
    var _pendingQuery = null;
    document.addEventListener('soulsync:video-search-query', function (e) {
        var detail = e && e.detail;
        var qv = detail && (detail.q || detail);
        if (typeof qv !== 'string' || !qv.trim()) return;
        setMode('enhanced');
        _pendingQuery = { q: qv.trim(), source: (detail && detail.source) || 'search',
            kind: (detail && detail.kind) === 'show' ? 'show' : 'movie' };
        if (document.body.getAttribute('data-video-page') === PAGE_ID) {
            var input = $('[data-video-search-input]');
            if (input) {
                input.value = _pendingQuery.q;
                queryContext = _pendingQuery;
                renderQueryContext();
                onInput(_pendingQuery.q);
                _pendingQuery = null;
            }
        }
    });

    function init() {
        document.addEventListener('soulsync:video-page-shown', onPageShown);
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
