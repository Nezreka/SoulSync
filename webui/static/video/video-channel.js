/*
 * SoulSync — Video YouTube Channel detail page (isolated).
 *
 * Opens in-app like a show/movie via soulsync:video-open-detail {kind:'channel',
 * source:'youtube', id:<channelId>}. Renders a banner hero (avatar, stats,
 * description, Follow), and the channel's uploads as a grid where each video can
 * be wished individually. Separate module — mirrors video-person.js — listening
 * only for kind==='channel'. Styled by .vc-* in video-side.css.
 */
(function () {
    'use strict';

    var PAGE_ID = 'video-channel-detail';
    var YT = function () { return window.VideoYoutube; };
    var state = { id: null, channel: null, videos: {} };

    function $(s, r) { return (r || document).querySelector(s); }
    function esc(s) { return YT() ? YT().esc(s) : String(s == null ? '' : s); }
    function show(el, on) { if (el) el.hidden = !on; }

    function watchUrl(vid) { return 'https://www.youtube.com/watch?v=' + encodeURIComponent(vid); }
    function channelUrl(id) { return 'https://www.youtube.com/channel/' + encodeURIComponent(id); }

    function videoCard(v) {
        var dur = YT().fmtDuration(v.duration_seconds);
        var thumb = v.thumbnail_url
            ? '<img class="vc-vid-img" src="' + esc(v.thumbnail_url) + '" alt="" loading="lazy" ' +
              'onerror="this.parentNode.classList.add(\'vc-vid-thumb--none\')">'
            : '';
        var bits = [];
        var d = YT().fmtDate(v.published_at); if (d) bits.push(esc(d));
        var vc = YT().compactCount(v.view_count); if (vc) bits.push(esc(vc) + ' views');
        var wished = !!v.wished;
        return '<div class="vc-vid" data-vc-vid="' + esc(v.youtube_id) + '">' +
            '<a class="vc-vid-thumb' + (v.thumbnail_url ? '' : ' vc-vid-thumb--none') + '" href="' + watchUrl(v.youtube_id) +
                '" target="_blank" rel="noopener" data-vc-ext>' + thumb +
                (dur ? '<span class="vc-vid-dur">' + esc(dur) + '</span>' : '') +
                '<span class="vc-vid-play" aria-hidden="true">&#9654;</span></a>' +
            '<div class="vc-vid-body">' +
                '<div class="vc-vid-title" title="' + esc(v.title) + '">' + esc(v.title || 'Untitled') + '</div>' +
                (bits.length ? '<div class="vc-vid-meta">' + bits.join(' · ') + '</div>' : '') +
            '</div>' +
            '<button class="vc-wish' + (wished ? ' vc-wish--on' : '') + '" type="button" data-vc-wish="' +
                esc(v.youtube_id) + '">' + (wished ? '✓ Wished' : '+ Wish') + '</button>' +
        '</div>';
    }

    function render(d) {
        var ch = d.channel || {};
        state.channel = ch;
        state.videos = {};
        (ch.videos || []).forEach(function (v) { state.videos[v.youtube_id] = v; });

        var banner = $('[data-vc-banner]');
        if (banner) banner.style.backgroundImage = ch.banner_url ? "url('" + ch.banner_url + "')" : '';
        var page = $('[data-video-channel]'); if (page) page.setAttribute('data-has-banner', ch.banner_url ? '1' : '0');

        var av = $('[data-vc-avatar]'), avph = $('[data-vc-avatar-ph]');
        if (av) {
            if (ch.avatar_url) { av.src = ch.avatar_url; show(av, true); if (avph) avph.hidden = true; }
            else { show(av, false); if (avph) avph.hidden = false; }
        }
        var name = $('[data-vc-name]'); if (name) name.textContent = ch.title || 'Channel';
        var meta = $('[data-vc-meta]');
        if (meta) {
            var m = [];
            if (ch.handle) m.push(esc(ch.handle));
            var subs = YT().compactCount(ch.subscriber_count); if (subs) m.push(subs + ' subscribers');
            if (ch.video_count != null) m.push(esc(ch.video_count) + ' videos');
            meta.innerHTML = m.join('<span class="vc-dot">·</span>');
        }
        var desc = $('[data-vc-desc]');
        if (desc) { desc.textContent = ch.description || ''; desc.hidden = !ch.description; }
        var yt = $('[data-vc-yt]'); if (yt) yt.href = channelUrl(ch.youtube_id);
        setFollow(!!d.following);

        var count = $('[data-vc-count]'); if (count) count.textContent = (ch.videos || []).length + ' shown';
        var grid = $('[data-vc-videos]');
        if (grid) grid.innerHTML = (ch.videos || []).map(videoCard).join('');
        show($('[data-vc-empty]'), !(ch.videos || []).length);
    }

    function setFollow(on) {
        var b = $('[data-vc-follow]'); if (!b) return;
        b.classList.toggle('vc-follow--on', on);
        b.textContent = on ? '✓ Following' : '+ Follow';
    }

    function load(id) {
        state.id = id;
        var ld = $('[data-vc-loading]'); show(ld, true);
        var grid = $('[data-vc-videos]'); if (grid) grid.innerHTML = '';
        var name = $('[data-vc-name]'); if (name) name.textContent = '';
        var meta = $('[data-vc-meta]'); if (meta) meta.innerHTML = '';
        var desc = $('[data-vc-desc]'); if (desc) { desc.textContent = ''; desc.hidden = true; }
        fetch('/api/video/youtube/channel/' + encodeURIComponent(id) + '?limit=60',
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
            btn.classList.toggle('vc-wish--on', val); btn.textContent = val ? '✓ Wished' : '+ Wish';
            btn.disabled = false; v.wished = val;
            document.dispatchEvent(new CustomEvent('soulsync:video-wishlist-changed'));
        };
        if (on) {
            YT().removeWish('video', id).then(function (d) { setOn(!(d && d.success)); if (d && d.success && typeof showToast === 'function') showToast('Removed from wishlist', 'info'); })
                .catch(function () { btn.disabled = false; });
        } else {
            YT().addVideos(channelStub(), [v]).then(function (d) {
                setOn(!!(d && d.success));
                if (d && d.success && typeof showToast === 'function') showToast('Added to wishlist', 'success');
            }).catch(function () { btn.disabled = false; });
        }
    }

    function toggleFollow(btn) {
        if (!YT()) return;
        var on = btn.classList.contains('vc-follow--on');
        btn.disabled = true;
        var done = function () { btn.disabled = false; document.dispatchEvent(new CustomEvent('soulsync:video-wishlist-changed')); load(state.id); };
        if (on) YT().unfollow(state.id).then(done).catch(function () { btn.disabled = false; });
        else YT().follow(channelStub()).then(function (d) {
            if (d && d.success && typeof showToast === 'function')
                showToast('Following · ' + (d.added_videos || 0) + ' videos added', 'success');
            done();
        }).catch(function () { btn.disabled = false; });
    }

    function onClick(e) {
        var wish = e.target.closest('[data-vc-wish]');
        if (wish) { e.preventDefault(); toggleWish(wish); return; }
        var fb = e.target.closest('[data-vc-follow]');
        if (fb) { e.preventDefault(); toggleFollow(fb); return; }
        // [data-vc-ext] anchors (thumbnail / Open on YouTube) fall through to navigate.
    }

    function onOpen(e) {
        if (!e || !e.detail || e.detail.kind !== 'channel') return;
        load(e.detail.id);
    }

    function init() {
        var page = $('[data-video-channel]');
        if (page) page.addEventListener('click', onClick);
        document.addEventListener('soulsync:video-open-detail', onOpen);
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
