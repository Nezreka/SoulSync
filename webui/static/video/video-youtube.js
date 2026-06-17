/* Shared YouTube-channel helpers for the video side: detect a pasted channel
 * link, render channel/video cards, and follow/unfollow. Used by the search
 * page (paste a link → Follow), the wishlist YouTube tab, and the watchlist.
 * Scoped under window.VideoYoutube; all CSS is .vyt-* (never touches music wl-*).
 */
(function () {
    'use strict';

    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }

    // A pasted YouTube *channel* reference: a full channel URL or a bare @handle.
    // Deliberately NOT bare words, so normal title searches don't trigger it.
    function isChannelRef(q) {
        q = (q || '').trim();
        if (/^@[\w.\-]{2,}$/.test(q)) return true;
        if (!/youtube\.com/i.test(q)) return false;
        return /youtube\.com\/(@[\w.\-]+|channel\/UC[\w\-]+|c\/[\w.\-]+|user\/[\w.\-]+)/i.test(q);
    }

    function fmtDate(iso) {
        if (!iso) return '';
        var d = new Date(iso.length <= 10 ? iso + 'T00:00:00' : iso);
        if (isNaN(d)) return '';
        return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
    }

    function initials(s) {
        var w = String(s || '').trim().split(/\s+/).filter(Boolean);
        return (w.slice(0, 2).map(function (x) { return x[0]; }).join('') || '▶').toUpperCase();
    }

    function avatar(ch, cls) {
        if (ch && ch.poster_url || (ch && ch.avatar_url)) {
            return '<img class="' + cls + '" src="' + esc(ch.poster_url || ch.avatar_url) +
                '" alt="" loading="lazy" onerror="this.style.display=\'none\'">';
        }
        return '<span class="' + cls + ' vyt-avatar--ph">' + esc(initials(ch && ch.title)) + '</span>';
    }

    // A single wished video tile (thumbnail, title, date, remove).
    function videoCard(v) {
        var thumb = v.still_url
            ? '<img class="vyt-vid-img" src="' + esc(v.still_url) + '" alt="" loading="lazy" ' +
              'onerror="this.parentNode.classList.add(\'vyt-vid-thumb--none\')">'
            : '';
        var date = fmtDate(v.published_at);
        return '<div class="vyt-vid" data-vyt-vid="' + esc(v.youtube_id) + '">' +
            '<div class="vyt-vid-thumb' + (v.still_url ? '' : ' vyt-vid-thumb--none') + '">' + thumb +
                '<button class="vyt-vid-rm" type="button" data-vyt-rm="video" data-id="' + esc(v.youtube_id) +
                '" title="Remove">&#10005;</button></div>' +
            '<div class="vyt-vid-body">' +
                '<span class="vyt-vid-title" title="' + esc(v.title) + '">' + esc(v.title || 'Untitled') + '</span>' +
                (date ? '<span class="vyt-vid-date">' + esc(date) + '</span>' : '') +
            '</div></div>';
    }

    // The search-page result: avatar, title/handle, a strip of recent stills, Follow.
    function searchCard(ch, following) {
        var strip = (ch.videos || []).slice(0, 6).map(function (v) {
            return v.thumbnail_url
                ? '<span class="vyt-strip-cell"><img src="' + esc(v.thumbnail_url) + '" alt="" loading="lazy" ' +
                  'onerror="this.parentNode.style.display=\'none\'"></span>'
                : '';
        }).join('');
        var sub = [];
        if (ch.handle) sub.push(esc(ch.handle));
        if (ch.video_count != null) sub.push(esc(ch.video_count) + ' videos');
        return '<div class="vyt-chip" data-vyt-channel="' + esc(ch.youtube_id) + '">' +
            '<div class="vyt-chip-head">' +
                avatar(ch, 'vyt-chip-avatar') +
                '<div class="vyt-chip-meta">' +
                    '<span class="vyt-chip-badge">YouTube channel</span>' +
                    '<span class="vyt-chip-title">' + esc(ch.title) + '</span>' +
                    (sub.length ? '<span class="vyt-chip-sub">' + sub.join(' · ') + '</span>' : '') +
                '</div>' +
                followBtn(following) +
            '</div>' +
            (strip ? '<div class="vyt-strip">' + strip + '</div>' : '') +
        '</div>';
    }

    function followBtn(following) {
        return '<button class="vyt-follow' + (following ? ' vyt-follow--on' : '') + '" type="button" ' +
            'data-vyt-follow>' + (following ? '✓ Following' : '+ Follow') + '</button>';
    }

    function post(url, body) {
        return fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body || {}) }).then(function (r) { return r.ok ? r.json() : null; });
    }

    function follow(channel) { return post('/api/video/youtube/follow', { channel: channel }); }
    function unfollow(youtubeId) { return post('/api/video/youtube/unfollow', { youtube_id: youtubeId }); }
    function removeWish(scope, sourceId) {
        return post('/api/video/youtube/wishlist/remove', { scope: scope, source_id: sourceId });
    }
    function addVideos(channel, videos) {
        return post('/api/video/youtube/wishlist/add', { channel: channel, videos: videos });
    }

    // mm:ss / h:mm:ss from a seconds count
    function fmtDuration(sec) {
        sec = parseInt(sec, 10);
        if (!sec || sec < 0) return '';
        var h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
        var mm = (h && m < 10) ? '0' + m : '' + m, ss = s < 10 ? '0' + s : '' + s;
        return (h ? h + ':' + mm : m + '') + ':' + ss;
    }
    function compactCount(n) {
        n = parseInt(n, 10);
        if (!n && n !== 0) return '';
        if (n >= 1e9) return (n / 1e9).toFixed(1).replace(/\.0$/, '') + 'B';
        if (n >= 1e6) return (n / 1e6).toFixed(1).replace(/\.0$/, '') + 'M';
        if (n >= 1e3) return (n / 1e3).toFixed(1).replace(/\.0$/, '') + 'K';
        return '' + n;
    }

    function resolve(ref) {
        return fetch('/api/video/youtube/resolve?url=' + encodeURIComponent(ref),
            { headers: { Accept: 'application/json' } }).then(function (r) { return r.ok ? r.json() : (r.status === 404 ? r.json() : null); });
    }

    window.VideoYoutube = {
        esc: esc, isChannelRef: isChannelRef, fmtDate: fmtDate, avatar: avatar,
        fmtDuration: fmtDuration, compactCount: compactCount,
        videoCard: videoCard, searchCard: searchCard, followBtn: followBtn,
        follow: follow, unfollow: unfollow, removeWish: removeWish, addVideos: addVideos, resolve: resolve,
    };
})();
