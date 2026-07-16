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

    var lastQuery = '';
    var reqSeq = 0;            // guards against out-of-order responses
    var timer = null;
    var wired = false;
    var trendingCache = null;  // null = not fetched; [] = fetched/empty
    var lastChannel = null;    // resolved YouTube channel awaiting a Follow
    var lastPlaylist = null;   // resolved YouTube playlist awaiting Add-to-watchlist

    function $(sel) { return document.querySelector(sel); }
    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }
    function show(sel, on) { var n = $(sel); if (n) n.classList.toggle('hidden', !on); }

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

        var results = $('[data-video-search-results]');
        if (results) {
            results.addEventListener('click', function (e) {
                if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
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
        if (input) { try { input.focus(); } catch (err) { /* ignore */ } }
        if (!lastQuery) loadTrending();               // fill the idle page
    }

    function init() {
        document.addEventListener('soulsync:video-page-shown', onPageShown);
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
