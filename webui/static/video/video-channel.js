/*
 * SoulSync — Video YouTube Channel detail page (isolated).
 *
 * Opens in-app like a show/movie via soulsync:video-open-detail {kind:'channel',
 * source:'youtube', id:<channelId>}. Banner hero (avatar, stats, tags, Follow),
 * a sortable/filterable upload grid with Load-more + per-video inline detail, and
 * the channel's playlists as collapsible "seasons". Sibling of video-person.js;
 * listens only for kind==='channel'. Styled by .vc-* in video-side.css.
 */
(function () {
    'use strict';

    var PAGE_ID = 'video-channel-detail';
    var YT = function () { return window.VideoYoutube; };
    var PAGE_SIZE = 60;
    var state = { id: null, channel: null, videos: {}, all: [], sort: 'newest',
                  wishedOnly: false, limit: PAGE_SIZE, plLoaded: {} };

    function $(s, r) { return (r || document).querySelector(s); }
    function esc(s) { return YT() ? YT().esc(s) : String(s == null ? '' : s); }
    function show(el, on) { if (el) el.hidden = !on; }
    function watchUrl(v) { return 'https://www.youtube.com/watch?v=' + encodeURIComponent(v); }
    function channelUrl(id) { return 'https://www.youtube.com/channel/' + encodeURIComponent(id); }

    // ── a video tile (used in the main grid AND inside playlist sections) ──────
    function videoCard(v) {
        var dur = YT().fmtDuration(v.duration_seconds);
        var thumb = v.thumbnail_url
            ? '<img class="vc-vid-img" src="' + esc(YT().img(v.thumbnail_url)) + '" alt="" loading="lazy" ' +
              'onerror="this.parentNode.classList.add(\'vc-vid-thumb--none\')">'
            : '';
        var bits = [];
        var d = YT().fmtDate(v.published_at); if (d) bits.push(esc(d));
        var vc = YT().compactCount(v.view_count); if (vc) bits.push(esc(vc) + ' views');
        return '<div class="vc-vid" data-vc-vid="' + esc(v.youtube_id) + '">' +
            '<a class="vc-vid-thumb' + (v.thumbnail_url ? '' : ' vc-vid-thumb--none') + '" href="' + watchUrl(v.youtube_id) +
                '" target="_blank" rel="noopener" data-vc-ext>' + thumb +
                (dur ? '<span class="vc-vid-dur">' + esc(dur) + '</span>' : '') +
                '<span class="vc-vid-play" aria-hidden="true">&#9654;</span></a>' +
            '<div class="vc-vid-body" data-vc-expand="' + esc(v.youtube_id) + '" title="Show details">' +
                '<div class="vc-vid-title">' + esc(v.title || 'Untitled') + '</div>' +
                (bits.length ? '<div class="vc-vid-meta">' + bits.join(' · ') + '</div>' : '') +
            '</div>' +
            '<div class="vc-vid-detail" data-vc-detail="' + esc(v.youtube_id) + '" hidden></div>' +
            '<button class="vc-wish' + (v.wished ? ' vc-wish--on' : '') + '" type="button" data-vc-wish="' +
                esc(v.youtube_id) + '">' + (v.wished ? '✓ Wished' : '+ Wish') + '</button>' +
        '</div>';
    }

    function applyGrid() {
        var list = state.all.slice();
        if (state.wishedOnly) list = list.filter(function (v) { return v.wished; });
        if (state.sort === 'oldest') list.reverse();                 // state.all is newest-first
        else if (state.sort === 'views') list.sort(function (a, b) { return (b.view_count || 0) - (a.view_count || 0); });
        var grid = $('[data-vc-videos]'); if (grid) grid.innerHTML = list.map(videoCard).join('');
        var count = $('[data-vc-count]');
        if (count) count.textContent = list.length + (state.wishedOnly ? ' wished' : ' shown');
        show($('[data-vc-empty]'), !list.length);
    }

    function render(d) {
        var ch = d.channel || {};
        state.channel = ch; state.videos = {}; state.all = (ch.videos || []).slice();
        state.all.forEach(function (v) { state.videos[v.youtube_id] = v; });

        var banner = $('[data-vc-banner]');
        if (banner) banner.style.backgroundImage = ch.banner_url ? "url('" + YT().img(ch.banner_url) + "')" : '';
        var page = $('[data-video-channel]'); if (page) page.setAttribute('data-has-banner', ch.banner_url ? '1' : '0');

        var av = $('[data-vc-avatar]'), avph = $('[data-vc-avatar-ph]');
        if (av) {
            if (ch.avatar_url) { av.src = YT().img(ch.avatar_url); show(av, true); if (avph) avph.hidden = true; }
            else { show(av, false); if (avph) avph.hidden = false; }
        }
        var name = $('[data-vc-name]'); if (name) name.textContent = ch.title || 'Channel';
        var meta = $('[data-vc-meta]');
        if (meta) {
            var m = [];
            if (ch.handle) m.push(esc(ch.handle));
            var subs = YT().compactCount(ch.subscriber_count); if (subs) m.push(subs + ' subscribers');
            if (ch.video_count != null) m.push(esc(ch.video_count) + ' videos');
            var views = YT().compactCount(ch.view_count); if (views) m.push(views + ' views');
            meta.innerHTML = m.join('<span class="vc-dot">·</span>');
        }
        var tags = $('[data-vc-tags]');
        if (tags) {
            tags.innerHTML = (ch.tags || []).map(function (t) { return '<span class="vc-tag">' + esc(t) + '</span>'; }).join('');
            tags.hidden = !(ch.tags && ch.tags.length);
        }
        var desc = $('[data-vc-desc]');
        if (desc) { desc.textContent = ch.description || ''; desc.hidden = !ch.description; }
        var yt = $('[data-vc-yt]'); if (yt) yt.href = channelUrl(ch.youtube_id);
        setFollow(!!d.following);

        applyGrid();
        // More uploads likely exist if we got a full page back.
        show($('[data-vc-more]'), state.all.length >= state.limit);
        loadPlaylists(ch.youtube_id);
    }

    function setFollow(on) {
        var b = $('[data-vc-follow]'); if (!b) return;
        b.classList.toggle('vc-follow--on', on);
        b.textContent = on ? '✓ Following' : '+ Follow';
    }

    // ── playlists ("seasons") ──────────────────────────────────────────────────
    function playlistRow(p) {
        var thumb = p.thumbnail_url
            ? '<img class="vc-pl-thumb" src="' + esc(YT().img(p.thumbnail_url)) + '" alt="" loading="lazy">'
            : '<span class="vc-pl-thumb vc-pl-thumb--none">▶</span>';
        var n = p.video_count != null ? p.video_count + ' video' + (p.video_count === 1 ? '' : 's') : '';
        return '<div class="vc-pl vc-pl--collapsed" data-vc-pl="' + esc(p.playlist_id) + '">' +
            '<div class="vc-pl-hd" data-vc-pl-toggle="' + esc(p.playlist_id) + '">' + thumb +
                '<div class="vc-pl-meta"><span class="vc-pl-title">' + esc(p.title) + '</span>' +
                (n ? '<span class="vc-pl-count">' + n + '</span>' : '') + '</div>' +
                '<span class="vc-pl-chev" aria-hidden="true">▾</span>' +
            '</div>' +
            '<div class="vc-pl-vids" data-vc-pl-vids="' + esc(p.playlist_id) + '"></div>' +
        '</div>';
    }

    function loadPlaylists(cid) {
        var sec = $('[data-vc-pl-section]'), host = $('[data-vc-playlists]');
        if (host) host.innerHTML = '';
        if (sec) sec.hidden = true;
        if (!cid) return;
        fetch('/api/video/youtube/playlists/' + encodeURIComponent(cid), { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                var pls = (d && d.playlists) || [];
                if (!pls.length || !host) return;
                host.innerHTML = pls.map(playlistRow).join('');
                if (sec) sec.hidden = false;
            })
            .catch(function () { /* best-effort */ });
    }

    function loadPlaylistVideos(pid) {
        if (state.plLoaded[pid]) return;
        state.plLoaded[pid] = true;
        var host = $('[data-vc-pl-vids="' + pid + '"]'); if (!host) return;
        host.innerHTML = '<div class="vc-pl-loading">Loading…</div>';
        fetch('/api/video/youtube/playlist/' + encodeURIComponent(pid), { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                var vids = (d && d.videos) || [];
                vids.forEach(function (v) { state.videos[v.youtube_id] = v; });
                host.innerHTML = vids.length ? vids.map(videoCard).join('') : '<div class="vc-pl-loading">No videos.</div>';
            })
            .catch(function () { state.plLoaded[pid] = false; host.innerHTML = ''; });
    }

    // ── load ───────────────────────────────────────────────────────────────────
    function load(id, keepLimit) {
        state.id = id;
        if (!keepLimit) { state.limit = PAGE_SIZE; state.plLoaded = {}; }
        var ld = $('[data-vc-loading]'); show(ld, true);
        if (!keepLimit) { var g = $('[data-vc-videos]'); if (g) g.innerHTML = ''; }
        fetch('/api/video/youtube/channel/' + encodeURIComponent(id) + '?limit=' + state.limit,
            { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                show(ld, false);
                if (!d || !d.success) {
                    var nm = $('[data-vc-name]'); if (nm) nm.textContent = 'Channel unavailable';
                    show($('[data-vc-empty]'), true);
                    return;
                }
                render(d);
            })
            .catch(function () { show(ld, false); });
    }

    // ── interactions ──────────────────────────────────────────────────────────
    function channelStub() {
        var c = state.channel || {};
        return { youtube_id: c.youtube_id, title: c.title, avatar_url: c.avatar_url };
    }

    function toggleWish(btn) {
        if (!YT()) return;
        var id = btn.getAttribute('data-vc-wish');
        var v = state.videos[id]; if (!v) return;
        var on = btn.classList.contains('vc-wish--on');
        btn.disabled = true;
        var setOn = function (val) {
            v.wished = val; btn.disabled = false;
            // reflect on every card with this id (grid + playlists)
            var btns = document.querySelectorAll('[data-vc-wish="' + id + '"]');
            for (var i = 0; i < btns.length; i++) {
                btns[i].classList.toggle('vc-wish--on', val);
                btns[i].textContent = val ? '✓ Wished' : '+ Wish';
            }
            document.dispatchEvent(new CustomEvent('soulsync:video-wishlist-changed'));
        };
        if (on) YT().removeWish('video', id).then(function (d) { setOn(!(d && d.success)); }).catch(function () { btn.disabled = false; });
        else YT().addVideos(channelStub(), [v]).then(function (d) {
            setOn(!!(d && d.success));
            if (d && d.success && typeof showToast === 'function') showToast('Added to wishlist', 'success');
        }).catch(function () { btn.disabled = false; });
    }

    function toggleFollow(btn) {
        if (!YT()) return;
        var on = btn.classList.contains('vc-follow--on');
        btn.disabled = true;
        var done = function () { btn.disabled = false; document.dispatchEvent(new CustomEvent('soulsync:video-wishlist-changed')); load(state.id, true); };
        if (on) YT().unfollow(state.id).then(done).catch(function () { btn.disabled = false; });
        else YT().follow(channelStub()).then(function (d) {
            if (d && d.success && typeof showToast === 'function') showToast('Following · ' + (d.added_videos || 0) + ' videos added', 'success');
            done();
        }).catch(function () { btn.disabled = false; });
    }

    // expand a video's full description/stats inline (lazy, cached on the object)
    function toggleExpand(body) {
        var id = body.getAttribute('data-vc-expand');
        var panel = $('[data-vc-detail="' + id + '"]', body.parentNode); if (!panel) return;
        if (!panel.hidden) { panel.hidden = true; return; }
        panel.hidden = false;
        var v = state.videos[id] || {};
        if (v._full) { panel.innerHTML = detailHTML(v._full); return; }
        if (v._loading) return;
        v._loading = true;
        panel.innerHTML = '<div class="vc-vid-detail-load">Loading details…</div>';
        fetch('/api/video/youtube/video/' + encodeURIComponent(id), { headers: { Accept: 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                v._loading = false; v._full = (d && d.video) || {};
                if (!panel.hidden) panel.innerHTML = detailHTML(v._full);
            })
            .catch(function () { v._loading = false; panel.innerHTML = '<div class="vc-vid-detail-load">No details.</div>'; });
    }
    function detailHTML(f) {
        var stats = [];
        var likes = YT().compactCount(f.like_count); if (likes) stats.push(likes + ' likes');
        var vc = YT().compactCount(f.view_count); if (vc) stats.push(vc + ' views');
        return (stats.length ? '<div class="vc-vid-stats">' + esc(stats.join(' · ')) + '</div>' : '') +
            (f.description ? '<div class="vc-vid-desc">' + esc(f.description) + '</div>' : '<div class="vc-vid-desc">No description.</div>');
    }

    function onClick(e) {
        var t = e.target;
        var wish = t.closest('[data-vc-wish]'); if (wish) { e.preventDefault(); toggleWish(wish); return; }
        var fb = t.closest('[data-vc-follow]'); if (fb) { e.preventDefault(); toggleFollow(fb); return; }
        if (t.closest('[data-vc-ext]')) return;                 // thumbnail / YouTube link
        var more = t.closest('[data-vc-more]'); if (more) { e.preventDefault(); state.limit += PAGE_SIZE; load(state.id, true); return; }
        var plt = t.closest('[data-vc-pl-toggle]');
        if (plt) {
            var pid = plt.getAttribute('data-vc-pl-toggle');
            var blk = plt.closest('.vc-pl');
            if (blk && blk.classList.toggle('vc-pl--collapsed') === false) loadPlaylistVideos(pid);
            return;
        }
        var exp = t.closest('[data-vc-expand]'); if (exp) { e.preventDefault(); toggleExpand(exp); return; }
    }

    function onOpen(e) {
        if (!e || !e.detail || e.detail.kind !== 'channel') return;
        load(e.detail.id);
    }

    function init() {
        var page = $('[data-video-channel]');
        if (page) page.addEventListener('click', onClick);
        var sort = $('[data-vc-sort]');
        if (sort) sort.addEventListener('change', function () { state.sort = sort.value; applyGrid(); });
        var wt = $('[data-vc-wished-toggle]');
        if (wt) wt.addEventListener('click', function () {
            state.wishedOnly = !state.wishedOnly;
            wt.classList.toggle('vc-tool-btn--on', state.wishedOnly);
            applyGrid();
        });
        document.addEventListener('soulsync:video-open-detail', onOpen);
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
